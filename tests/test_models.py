"""Tests for the human gate.

If any of these fail, the tool can submit an application nobody read. They are
the most important tests in the project.
"""

from __future__ import annotations

import pytest

from jobhunt.models import Application, Stage
from tests.conftest import make_job


def test_cannot_submit_without_approval():
    app = Application(job=make_job(), stage=Stage.AWAITING_REVIEW)

    with pytest.raises(ValueError, match="requires approved"):
        app.advance(Stage.SUBMITTED)

    assert app.stage is Stage.AWAITING_REVIEW


def test_cannot_submit_straight_from_drafted():
    app = Application(job=make_job(), stage=Stage.DRAFTED)
    with pytest.raises(ValueError):
        app.advance(Stage.SUBMITTED)


def test_submit_allowed_after_approval():
    app = Application(job=make_job(), stage=Stage.AWAITING_REVIEW)
    app.advance(Stage.APPROVED)
    app.advance(Stage.SUBMITTED)

    assert app.stage is Stage.SUBMITTED
    assert app.submitted_at is not None


def test_terminal_stages_are_terminal():
    for stage in (Stage.SUBMITTED, Stage.REJECTED, Stage.ABANDONED):
        app = Application(job=make_job(), stage=stage)
        with pytest.raises(ValueError, match="already"):
            app.advance(Stage.APPROVED)


def test_cannot_move_backwards():
    app = Application(job=make_job(), stage=Stage.DRAFTED)
    with pytest.raises(ValueError, match="backwards"):
        app.advance(Stage.SCORED)


def test_can_always_abandon():
    app = Application(job=make_job(), stage=Stage.APPROVED)
    app.advance(Stage.ABANDONED)
    assert app.stage is Stage.ABANDONED


class TestIdentity:
    def test_id_is_stable_across_refetch(self):
        first = make_job(description="original text")
        second = make_job(description="edited by the employer")
        assert first.id == second.id

    def test_id_differs_by_source(self):
        assert make_job(source="adzuna").id != make_job(source="reed").id

    def test_dedupe_key_matches_across_sources(self):
        """The same role on two boards should collapse to one notification."""
        adzuna = make_job(source="adzuna", external_id="a1", company="Example Ltd")
        reed = make_job(source="reed", external_id="r9", company="example ltd ")
        assert adzuna.dedupe_key == reed.dedupe_key
        assert adzuna.id != reed.id

    def test_dedupe_key_differs_for_different_roles(self):
        a = make_job(title="Data Engineer")
        b = make_job(title="Senior Data Engineer")
        assert a.dedupe_key != b.dedupe_key
