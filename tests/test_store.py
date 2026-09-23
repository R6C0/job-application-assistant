"""Store tests.

Idempotency is the point. The pipeline runs on a timer against sources that
return the same postings every time, so "second run does nothing new" is the
property that decides whether this tool is usable or a notification machine.
"""

from __future__ import annotations

import pytest

from jobhunt.models import Application, CompanyBrief, Letter, Stage
from jobhunt.store import Store
from tests.conftest import make_job


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_first_insert_is_new_second_is_not(store):
    job = make_job()
    assert store.upsert_job(job) is True
    assert store.upsert_job(job) is False


def test_update_preserves_identity(store):
    store.upsert_job(make_job(description="original"))
    store.upsert_job(make_job(description="updated"))

    loaded = store.get_job(make_job().id)
    assert loaded is not None
    assert loaded.description == "updated"


def test_round_trips_a_full_application(store):
    job = make_job()
    store.upsert_job(job)

    app = Application(
        job=job,
        stage=Stage.AWAITING_REVIEW,
        brief=CompanyBrief(name="Example Ltd", summary="Real company", confidence="high"),
        letter=Letter(body="Dear team", evidence_used=["ev-1"], generator="template"),
        notes="looks promising",
    )
    store.save_application(app)

    loaded = store.get_application(job.id)
    assert loaded is not None
    assert loaded.stage is Stage.AWAITING_REVIEW
    assert loaded.brief.confidence == "high"
    assert loaded.letter.body == "Dear team"
    assert loaded.letter.evidence_used == ["ev-1"]
    assert loaded.notes == "looks promising"


def test_saving_twice_updates_rather_than_duplicates(store):
    job = make_job()
    store.upsert_job(job)

    app = Application(job=job, stage=Stage.SCORED)
    store.save_application(app)
    app.advance(Stage.RESEARCHED)
    store.save_application(app)

    assert store.counts_by_stage() == {"researched": 1}


def test_dedupe_key_lookup_ignores_the_same_job(store):
    adzuna = make_job(source="adzuna", external_id="a1")
    store.upsert_job(adzuna)

    assert store.seen_dedupe_key(adzuna.dedupe_key, excluding_job_id=adzuna.id) is False

    reed = make_job(source="reed", external_id="r1")
    store.upsert_job(reed)
    assert store.seen_dedupe_key(reed.dedupe_key, excluding_job_id=reed.id) is True


class TestNotificationBudget:
    def test_counts_only_todays(self, store):
        assert store.notifications_today() == 0
        store.record_notification("job-1")
        assert store.notifications_today() == 1

    def test_recording_twice_counts_once(self, store):
        store.record_notification("job-1")
        store.record_notification("job-1")
        assert store.notifications_today() == 1

    def test_already_notified(self, store):
        assert store.already_notified("job-1") is False
        store.record_notification("job-1")
        assert store.already_notified("job-1") is True


def test_list_by_stage_is_sorted_by_score(store):
    from jobhunt.models import Match, Reason

    for index, score in enumerate([50.0, 90.0, 70.0]):
        job = make_job(external_id=str(index))
        store.upsert_job(job)
        store.save_application(
            Application(
                job=job,
                stage=Stage.AWAITING_REVIEW,
                match=Match(
                    score=score,
                    reasons=[Reason("skills", score, "test")],
                    matched_skills=[],
                    missing_skills=[],
                ),
            )
        )

    scores = [a.match.score for a in store.list_applications(Stage.AWAITING_REVIEW)]
    assert scores == [90.0, 70.0, 50.0]


def test_missing_application_returns_none(store):
    assert store.get_application("nope") is None
