"""Scorer tests.

The blocker tests matter most. A scoring bug costs you a notification; a blocker
bug means the tool drafts a letter for a job you are not eligible for, and you
find out after reading it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobhunt.matching.scorer import Scorer, _required_years
from tests.conftest import make_job


def test_relevant_job_scores_well(profile, scoring):
    match = Scorer(profile, scoring).score(make_job())

    assert match.score > 70
    assert match.is_viable
    assert {"python", "sql", "etl", "aws"} <= set(match.matched_skills)


def test_irrelevant_job_scores_low(profile, scoring):
    job = make_job(
        title="Head Chef",
        description="Busy kitchen, 40 covers a night. Knife skills essential.",
        salary_min=None,
        salary_max=None,
    )
    match = Scorer(profile, scoring).score(job)

    assert match.score < 40
    assert match.matched_skills == []


def test_every_component_explains_itself(profile, scoring):
    match = Scorer(profile, scoring).score(make_job())

    components = {r.component for r in match.reasons}
    assert components == {"skills", "title", "seniority", "location", "salary", "recency"}
    # The detail strings end up in notifications and prompts, so none may be empty.
    assert all(r.detail for r in match.reasons)


class TestBlockers:
    def test_excluded_company(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(company="Bad Corp"))
        assert not match.is_viable
        assert any("excluded company" in b for b in match.blockers)

    def test_excluded_title_term(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(title="Sales Engineer"))
        assert any("excluded title term" in b for b in match.blockers)

    def test_contract_when_not_wanted(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(contract_type="contract"))
        assert any("contract" in b for b in match.blockers)

    def test_salary_ceiling_below_floor(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(salary_min=20000, salary_max=30000))
        assert any("below floor" in b for b in match.blockers)

    def test_missing_salary_does_not_block(self, profile, scoring):
        """Most UK postings hide salary. Blocking on absence would discard the market."""
        match = Scorer(profile, scoring).score(make_job(salary_min=None, salary_max=None))
        assert match.is_viable

    def test_too_many_years_required(self, profile, scoring):
        job = make_job(description="You will need 12 years of Python and SQL experience.")
        match = Scorer(profile, scoring).score(job)
        assert any("12 years" in b for b in match.blockers)

    def test_viable_job_has_no_blockers(self, profile, scoring):
        assert Scorer(profile, scoring).score(make_job()).blockers == []


class TestYearsParsing:
    def test_takes_the_largest_requirement(self):
        assert _required_years("3 years of Python, 5 years of SQL") == 5

    def test_ignores_implausible_numbers(self):
        # "Founded in 1998" must not read as 1998 years of experience.
        assert _required_years("Founded 1998. We want 4 years of experience.") == 4

    def test_handles_ranges(self):
        assert _required_years("3-5 years experience") == 3

    def test_none_when_unstated(self):
        assert _required_years("A great opportunity for a motivated engineer.") is None


class TestSeniority:
    def test_matching_level_scores_full(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(title="Mid-level Data Engineer"))
        reason = next(r for r in match.reasons if r.component == "seniority")
        assert reason.points == scoring.weights["seniority"]

    def test_distant_level_scores_low(self, profile, scoring):
        job = make_job(title="Principal Data Engineer")
        match = Scorer(profile, scoring).score(job)
        reason = next(r for r in match.reasons if r.component == "seniority")
        assert reason.points < scoring.weights["seniority"] * 0.5

    def test_unstated_is_neutral_not_punished(self, profile, scoring):
        match = Scorer(profile, scoring).score(make_job(title="Data Engineer"))
        reason = next(r for r in match.reasons if r.component == "seniority")
        assert reason.points == scoring.weights["seniority"] * 0.5


class TestRecency:
    def test_fresh_posting_scores_full(self, profile, scoring):
        job = make_job(posted_at=datetime.now(UTC))
        match = Scorer(profile, scoring).score(job)
        assert next(r for r in match.reasons if r.component == "recency").points == 5.0

    def test_stale_posting_scores_zero(self, profile, scoring):
        job = make_job(posted_at=datetime.now(UTC) - timedelta(days=60))
        match = Scorer(profile, scoring).score(job)
        assert next(r for r in match.reasons if r.component == "recency").points == 0.0

    def test_naive_datetime_does_not_crash(self, profile, scoring):
        """Sources are inconsistent about timezones; the scorer must not care."""
        job = make_job(posted_at=datetime.now() - timedelta(days=3))
        assert Scorer(profile, scoring).score(job).score > 0


def test_score_is_bounded(profile, scoring):
    perfect = make_job(
        title="Data Engineer",
        description=" ".join(["Python SQL ETL AWS PySpark Power BI Athena"] * 20),
    )
    assert 0 <= Scorer(profile, scoring).score(perfect).score <= 100
