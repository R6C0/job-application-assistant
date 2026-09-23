"""Review interface tests.

Template errors only appear when a page is rendered, so these render every page
against real data. The approve and submit tests are the human gate again, this
time at the HTTP layer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobhunt.config import Config
from jobhunt.models import Application, CompanyBrief, Letter, Match, Reason, Stage
from jobhunt.review.app import create_app
from jobhunt.store import Store
from tests.conftest import make_job


@pytest.fixture
def client_and_store(tmp_path):
    store = Store(tmp_path / "r.db")
    config = Config(database_path=str(tmp_path / "r.db"), profile_path=str(tmp_path / "p.yaml"))
    with TestClient(create_app(config, store)) as client:
        yield client, store
    store.close()


def seed(store, stage=Stage.AWAITING_REVIEW, with_letter=True):
    job = make_job()
    store.upsert_job(job)
    app = Application(
        job=job,
        stage=stage,
        match=Match(
            score=82.0,
            reasons=[
                Reason("skills", 32.0, "4/4 core skills present", ["python", "sql"]),
                Reason("title", 20.0, "title matches target role 'Data Engineer'"),
            ],
            matched_skills=["python", "sql"],
            missing_skills=["aws"],
        ),
        brief=CompanyBrief(
            name="Example Ltd",
            summary="An active UK-registered company.",
            facts={"Age": "about 9 years"},
            sources=["https://example.com/record"],
            confidence="high",
        ),
        letter=Letter(body="Dear hiring team,\n\nA draft.", evidence_used=["ev-1"],
                      generator="template") if with_letter else None,
    )
    store.save_application(app)
    return job


def test_queue_renders(client_and_store):
    client, store = client_and_store
    seed(store)
    response = client.get("/")

    assert response.status_code == 200
    assert "Data Engineer" in response.text
    assert "82" in response.text


def test_queue_renders_when_empty(client_and_store):
    client, _ = client_and_store
    assert client.get("/").status_code == 200


def test_detail_renders_everything(client_and_store):
    client, store = client_and_store
    job = seed(store)
    response = client.get(f"/job/{job.id}")

    assert response.status_code == 200
    for expected in ("4/4 core skills present", "Example Ltd", "Dear hiring team", "python"):
        assert expected in response.text


def test_detail_renders_without_a_letter(client_and_store):
    client, store = client_and_store
    job = seed(store, with_letter=False)
    response = client.get(f"/job/{job.id}")

    assert response.status_code == 200
    assert "No draft" in response.text


def test_unknown_job_is_404(client_and_store):
    client, _ = client_and_store
    assert client.get("/job/nope").status_code == 404


def test_editing_the_letter_marks_it_human_edited(client_and_store):
    client, store = client_and_store
    job = seed(store)

    client.post(f"/job/{job.id}/letter", data={"body": "My own words."},
                follow_redirects=False)

    app = store.get_application(job.id)
    assert app.letter.body == "My own words."
    assert app.letter.edited_by_human is True


def test_saving_an_unchanged_letter_is_not_authorship(client_and_store):
    client, store = client_and_store
    job = seed(store)
    original = store.get_application(job.id).letter.body

    client.post(f"/job/{job.id}/letter", data={"body": original}, follow_redirects=False)

    assert store.get_application(job.id).letter.edited_by_human is False


class TestHumanGate:
    def test_approve_moves_to_approved(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        client.post(f"/job/{job.id}/approve", follow_redirects=False)
        assert store.get_application(job.id).stage is Stage.APPROVED

    def test_cannot_approve_twice(self, client_and_store):
        client, store = client_and_store
        job = seed(store, stage=Stage.APPROVED)

        response = client.post(f"/job/{job.id}/approve", follow_redirects=False)
        assert response.status_code == 409

    def test_cannot_mark_submitted_before_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        response = client.post(f"/job/{job.id}/mark-submitted", follow_redirects=False)
        assert response.status_code == 409
        assert store.get_application(job.id).stage is Stage.AWAITING_REVIEW

    def test_mark_submitted_after_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store, stage=Stage.APPROVED)

        client.post(f"/job/{job.id}/mark-submitted", follow_redirects=False)
        app = store.get_application(job.id)
        assert app.stage is Stage.SUBMITTED
        assert app.submitted_at is not None

    def test_assisted_fill_refused_before_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        response = client.post(f"/job/{job.id}/assist", follow_redirects=False)
        assert response.status_code == 409


def test_skip_abandons(client_and_store):
    client, store = client_and_store
    job = seed(store)

    client.post(f"/job/{job.id}/skip", data={"reason": "too senior"},
                follow_redirects=False)

    app = store.get_application(job.id)
    assert app.stage is Stage.ABANDONED
    assert app.notes == "too senior"
