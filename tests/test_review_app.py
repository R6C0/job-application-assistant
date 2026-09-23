"""Review interface tests.

Two surfaces are covered:

* the JSON API the React app uses, which is where the human gate now lives for
  every real interaction;
* the server-rendered pages under /legacy, which exist so the tool still works
  without a frontend build.

The `TestHumanGate` class is the important one either way.
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
        letter=(
            Letter(
                body="Dear hiring team,\n\nA draft.",
                evidence_used=["ev-1"],
                generator="template",
            )
            if with_letter
            else None
        ),
    )
    store.save_application(app)
    return job


# ----------------------------------------------------------------------
# JSON API
# ----------------------------------------------------------------------
class TestApi:
    def test_stats(self, client_and_store):
        client, store = client_and_store
        seed(store)
        body = client.get("/api/stats").json()

        assert body["awaiting"] == 1
        assert body["average_score"] == 82.0

    def test_list_and_filter_by_stage(self, client_and_store):
        client, store = client_and_store
        seed(store)

        assert len(client.get("/api/applications").json()) == 1
        assert len(client.get("/api/applications?stage=awaiting_review").json()) == 1
        assert client.get("/api/applications?stage=submitted").json() == []

    def test_unknown_stage_is_400(self, client_and_store):
        client, _ = client_and_store
        assert client.get("/api/applications?stage=banana").status_code == 400

    def test_get_one_carries_everything_the_ui_needs(self, client_and_store):
        client, store = client_and_store
        job = seed(store)
        body = client.get(f"/api/applications/{job.id}").json()

        assert body["job"]["title"] == "Data Engineer"
        assert body["match"]["reasons"][0]["detail"] == "4/4 core skills present"
        assert body["brief"]["confidence"] == "high"
        assert body["letter"]["word_count"] > 0

    def test_missing_application_is_404(self, client_and_store):
        client, _ = client_and_store
        assert client.get("/api/applications/nope").status_code == 404

    def test_meta_exposes_the_drafters_ban_list(self, client_and_store):
        """The editor highlights banned phrases using the drafter's own list."""
        client, _ = client_and_store
        body = client.get("/api/meta").json()

        assert "perfect fit" in body["banned_phrases"]
        assert body["notify_threshold"] > 0

    def test_editing_marks_the_letter_human_edited(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        body = client.put(
            f"/api/applications/{job.id}/letter", json={"body": "My own words."}
        ).json()

        assert body["letter"]["edited_by_human"] is True
        assert store.get_application(job.id).letter.body == "My own words."

    def test_saving_an_unchanged_letter_is_not_authorship(self, client_and_store):
        client, store = client_and_store
        job = seed(store)
        original = store.get_application(job.id).letter.body

        client.put(f"/api/applications/{job.id}/letter", json={"body": original})

        assert store.get_application(job.id).letter.edited_by_human is False

    def test_skip_abandons_with_a_reason(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        client.post(f"/api/applications/{job.id}/skip", json={"reason": "too senior"})

        app = store.get_application(job.id)
        assert app.stage is Stage.ABANDONED
        assert app.notes == "too senior"

    def test_run_endpoint_reports_idle(self, client_and_store):
        client, _ = client_and_store
        assert client.get("/api/run").json()["running"] is False


# ----------------------------------------------------------------------
# The gate, over HTTP
# ----------------------------------------------------------------------
class TestHumanGate:
    def test_approve_moves_to_approved(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        client.post(f"/api/applications/{job.id}/approve")
        assert store.get_application(job.id).stage is Stage.APPROVED

    def test_cannot_approve_twice(self, client_and_store):
        client, store = client_and_store
        job = seed(store, stage=Stage.APPROVED)

        assert client.post(f"/api/applications/{job.id}/approve").status_code == 409

    def test_cannot_mark_submitted_before_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        response = client.post(f"/api/applications/{job.id}/mark-submitted")

        assert response.status_code == 409
        assert "requires approved" in response.json()["detail"]
        assert store.get_application(job.id).stage is Stage.AWAITING_REVIEW

    def test_mark_submitted_after_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store, stage=Stage.APPROVED)

        client.post(f"/api/applications/{job.id}/mark-submitted")

        app = store.get_application(job.id)
        assert app.stage is Stage.SUBMITTED
        assert app.submitted_at is not None

    def test_assisted_fill_refused_before_approval(self, client_and_store):
        client, store = client_and_store
        job = seed(store)

        assert client.post(f"/api/applications/{job.id}/assist").status_code == 409


# ----------------------------------------------------------------------
# Server-rendered fallback
# ----------------------------------------------------------------------
class TestLegacyPages:
    def test_queue_renders(self, client_and_store):
        client, store = client_and_store
        seed(store)
        response = client.get("/legacy")

        assert response.status_code == 200
        assert "Data Engineer" in response.text

    def test_queue_renders_when_empty(self, client_and_store):
        client, _ = client_and_store
        assert client.get("/legacy").status_code == 200

    def test_detail_renders_everything(self, client_and_store):
        client, store = client_and_store
        job = seed(store)
        response = client.get(f"/legacy/job/{job.id}")

        assert response.status_code == 200
        for expected in ("4/4 core skills present", "Example Ltd", "Dear hiring team"):
            assert expected in response.text

    def test_detail_renders_without_a_letter(self, client_and_store):
        client, store = client_and_store
        job = seed(store, with_letter=False)

        assert "No draft" in client.get(f"/legacy/job/{job.id}").text

    def test_root_falls_back_when_no_frontend_build(self, client_and_store):
        """Without web/dist the tool still works, which is why /legacy exists."""
        client, store = client_and_store
        seed(store)
        response = client.get("/", follow_redirects=True)

        assert response.status_code == 200
