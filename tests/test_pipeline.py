"""End-to-end pipeline tests with a fake source.

The last test is the one that matters: after a full run, nothing is approved and
nothing is submitted. The pipeline is supposed to stop.
"""

from __future__ import annotations

import pytest

from jobhunt.config import Config, NotifyConfig, ScoringConfig, SourceConfig
from jobhunt.letters.drafter import LetterDrafter
from jobhunt.models import CompanyBrief, Stage
from jobhunt.notify.base import Notification, Notifier
from jobhunt.pipeline import Pipeline
from jobhunt.sources.base import JobSource, SourceError
from jobhunt.store import Store
from tests.conftest import make_job


class FakeSource(JobSource):
    name = "fake"

    def __init__(self, jobs, fail=False):
        super().__init__(SourceConfig(enabled=True))
        self.jobs = jobs
        self.fail = fail

    async def fetch(self, client):
        if self.fail:
            raise SourceError("fake: deliberately broken")
        return self.jobs


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__(NotifyConfig(backend="none"))
        self.sent: list[Notification] = []

    async def send(self, client, note):
        self.sent.append(note)
        return True


class FakeResearcher:
    async def research(self, client, company_name):
        return CompanyBrief(
            name=company_name,
            summary="An active UK-registered company.",
            facts={"Age": "about 9 years"},
            confidence="high",
        )


@pytest.fixture
def pipeline_parts(tmp_path, profile):
    store = Store(tmp_path / "p.db")
    config = Config(
        profile_path=str(tmp_path / "profile.yaml"),
        database_path=str(tmp_path / "p.db"),
        scoring=ScoringConfig(notify_threshold=60),
        notify=NotifyConfig(backend="none", max_per_run=10, max_per_day=25),
    )
    notifier = RecordingNotifier()
    yield config, profile, store, notifier
    store.close()


def build(config, profile, store, notifier, jobs, fail=False):
    return Pipeline(
        config=config,
        profile=profile,
        store=store,
        sources=[FakeSource(jobs, fail=fail)],
        notifier=notifier,
        drafter=LetterDrafter(profile, backend="template"),
        researcher=FakeResearcher(),
    )


async def test_good_job_reaches_review_and_stops(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    job = make_job()
    report = await build(config, profile, store, notifier, [job]).run()

    assert report.new == 1
    assert report.notified == 1

    app = store.get_application(job.id)
    assert app.stage is Stage.AWAITING_REVIEW
    assert app.letter is not None
    assert app.brief.confidence == "high"

    # The gate: a full run leaves nothing approved or submitted.
    assert store.list_applications(Stage.APPROVED) == []
    assert store.list_applications(Stage.SUBMITTED) == []


async def test_blocked_job_is_rejected_without_drafting(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    job = make_job(company="Bad Corp")
    report = await build(config, profile, store, notifier, [job]).run()

    assert report.blocked == 1
    assert report.drafted == 0
    assert notifier.sent == []

    app = store.get_application(job.id)
    assert app.stage is Stage.REJECTED
    assert "excluded company" in app.notes


async def test_low_score_is_rejected_quietly(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    job = make_job(title="Head Chef", description="Kitchen work.", salary_max=None)
    report = await build(config, profile, store, notifier, [job]).run()

    assert report.below_threshold == 1
    assert notifier.sent == []
    assert store.get_application(job.id).stage is Stage.REJECTED


async def test_rerun_does_not_renotify(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    jobs = [make_job()]

    first = await build(config, profile, store, notifier, jobs).run()
    second = await build(config, profile, store, notifier, jobs).run()

    assert first.notified == 1
    assert second.new == 0
    assert second.notified == 0
    assert len(notifier.sent) == 1


async def test_same_role_from_two_sources_notifies_once(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    jobs = [
        make_job(source="adzuna", external_id="a1"),
        make_job(source="reed", external_id="r1"),
    ]
    report = await build(config, profile, store, notifier, jobs).run()

    assert report.new == 2
    assert report.duplicates == 1
    assert len(notifier.sent) == 1


async def test_daily_budget_stops_notifications_but_keeps_the_queue(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    config.notify.max_per_day = 2

    # Distinct company and title each, or the deduplicator collapses them and
    # this would be testing deduplication instead of the budget.
    jobs = [
        make_job(external_id=str(i), company=f"Company {i}", title=f"Data Engineer {i}")
        for i in range(5)
    ]
    report = await build(config, profile, store, notifier, jobs).run()

    assert report.duplicates == 0
    assert report.notified == 2
    assert len(notifier.sent) == 2
    # The rest are still reviewable, just not pushed.
    assert len(store.list_applications(Stage.AWAITING_REVIEW)) == 5


async def test_broken_source_is_reported_not_fatal(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    pipeline = Pipeline(
        config=config,
        profile=profile,
        store=store,
        sources=[FakeSource([], fail=True), FakeSource([make_job()])],
        notifier=notifier,
        drafter=LetterDrafter(profile, backend="template"),
        researcher=FakeResearcher(),
    )
    report = await pipeline.run()

    assert len(report.errors) == 1
    assert "deliberately broken" in report.errors[0]
    # The working source still delivered.
    assert report.notified == 1


async def test_dry_run_sends_nothing(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    report = await build(config, profile, store, notifier, [make_job()]).run(dry_run=True)

    assert report.notified == 1     # counted as "would have notified"
    assert notifier.sent == []      # but nothing left the machine


async def test_notification_carries_score_and_link(pipeline_parts):
    config, profile, store, notifier = pipeline_parts
    job = make_job()
    await build(config, profile, store, notifier, [job]).run()

    note = notifier.sent[0]
    assert job.company in note.body
    assert note.url.endswith(f"/job/{job.id}")
