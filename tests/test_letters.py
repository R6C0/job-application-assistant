"""Letter drafting tests.

These cover the template path and the validator. The Claude path is not tested
against the live API: the validator is what decides whether a generated draft is
acceptable, so testing the validator directly is both cheaper and more useful
than asserting on model output.
"""

from __future__ import annotations

from jobhunt.letters.drafter import BANNED_PHRASES, LetterDrafter
from jobhunt.matching.profile import Evidence
from jobhunt.matching.scorer import Scorer
from tests.conftest import make_job


def test_template_draft_uses_matched_evidence(profile, scoring):
    job = make_job()
    match = Scorer(profile, scoring).score(job)
    letter = LetterDrafter(profile, backend="template").draft(job, match)

    assert letter.generator == "template"
    assert "ev-pipeline" in letter.evidence_used
    # The Luau bullet has nothing to do with a data engineering role.
    assert "ev-unrelated" not in letter.evidence_used
    assert profile.name in letter.body


def test_template_draft_is_not_flagged_by_its_own_validator(profile, scoring):
    job = make_job()
    match = Scorer(profile, scoring).score(job)
    drafter = LetterDrafter(profile, backend="template")
    letter = drafter.draft(job, match)

    evidence = drafter._select_evidence(match)
    assert drafter._validate(letter.body, evidence) == []


def test_openings_vary_between_jobs(profile):
    drafter = LetterDrafter(profile, backend="template")
    moves = {
        drafter._opening_move(make_job(external_id=str(i)))
        for i in range(30)
    }
    # Deterministic per job, but not all the same across jobs.
    assert len(moves) > 1


def test_opening_is_stable_for_the_same_job(profile):
    drafter = LetterDrafter(profile, backend="template")
    job = make_job()
    assert drafter._opening_move(job) == drafter._opening_move(job)


class TestValidator:
    def setup_method(self):
        self.evidence = [
            Evidence(
                id="ev-1",
                text="I ran a pipeline of 380,000 records a month",
                metric="380,000 records a month",
                skills=["Python"],
            )
        ]

    def _drafter(self, profile):
        return LetterDrafter(profile, backend="template", max_words=100)

    def test_rejects_banned_phrases(self, profile):
        body = "I am writing to express my interest in this role."
        problems = self._drafter(profile)._validate(body, self.evidence)
        assert any("banned phrase" in p for p in problems)

    def test_rejects_invented_numbers(self, profile):
        body = "I ran a pipeline of 5,000,000 records a month."
        problems = self._drafter(profile)._validate(body, self.evidence)
        assert any("not in the supplied evidence" in p for p in problems)

    def test_accepts_numbers_that_came_from_evidence(self, profile):
        body = "I ran a pipeline of 380,000 records a month."
        assert self._drafter(profile)._validate(body, self.evidence) == []

    def test_rejects_over_length(self, profile):
        body = "word " * 200
        problems = self._drafter(profile)._validate(body, self.evidence)
        assert any("limit is" in p for p in problems)

    def test_clean_letter_passes(self, profile):
        body = (
            "Dear hiring team,\n\n"
            "I ran a pipeline of 380,000 records a month and would like to do "
            "similar work here.\n\nRegards,\nTest Person"
        )
        assert self._drafter(profile)._validate(body, self.evidence) == []


def test_banned_list_covers_the_usual_suspects():
    joined = " ".join(BANNED_PHRASES)
    for phrase in ("passionate", "perfect fit", "proven track record"):
        assert phrase in joined


def test_falls_back_to_template_without_api_key(profile, scoring):
    job = make_job()
    match = Scorer(profile, scoring).score(job)
    drafter = LetterDrafter(profile, backend="auto", api_key=None)

    assert drafter._can_use_claude is False
    assert drafter.draft(job, match).generator == "template"


def test_drafts_something_even_with_no_skill_overlap(profile, scoring):
    """A thin match should still produce a letter, not an exception."""
    job = make_job(title="Chef", description="Kitchen work.")
    match = Scorer(profile, scoring).score(job)
    letter = LetterDrafter(profile, backend="template").draft(job, match)

    assert letter.body.strip()
    assert letter.evidence_used


class TestTemplatePolish:
    """Two defects found by reading real output rather than by a failing test."""

    def test_splicing_keeps_the_capital_on_I(self, profile, scoring):
        """"I wrote the PySpark layer" must not become "i wrote ..."."""
        from jobhunt.letters.drafter import _decapitalise

        assert _decapitalise("I wrote the layer") == "I wrote the layer"
        assert _decapitalise("AWS Glue jobs ran") == "AWS Glue jobs ran"
        assert _decapitalise("When the vendor changed") == "when the vendor changed"

    def test_template_body_has_no_lowercase_i(self, profile, scoring):
        job = make_job(description="Python SQL ETL AWS pipeline work, plus dbt and Airflow.")
        match = Scorer(profile, scoring).score(job)
        body = LetterDrafter(profile, backend="template").draft(job, match).body

        assert " i " not in body
        assert not body.startswith("i ")

    def test_headline_skills_lead_with_core_not_alphabetical(self, profile, scoring):
        """Alphabetical order put 'airflow, databricks, dbt' ahead of Python and SQL."""
        job = make_job(
            description=(
                "Build pipelines with Python and SQL on AWS. ETL experience required. "
                "Nice to have: Airflow, dbt, Databricks."
            )
        )
        match = Scorer(profile, scoring).score(job)
        headline = LetterDrafter(profile, backend="template")._headline_skills(match)

        assert headline[0] in {"python", "sql", "etl", "aws"}
        assert "python" in headline
