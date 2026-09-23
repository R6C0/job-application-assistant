from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobhunt.config import ScoringConfig
from jobhunt.matching.profile import Evidence, Preferences, Profile
from jobhunt.models import Job


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="Test Person",
        email="test@example.com",
        location="London, UK",
        target_roles=["Data Engineer", "Analytics Engineer"],
        seniority="mid",
        must_have_skills=["Python", "SQL", "ETL", "AWS"],
        nice_to_have_skills=["PySpark", "Power BI", "Athena"],
        evidence=[
            Evidence(
                id="ev-pipeline",
                text="I own a pipeline processing 380,000 records a month",
                metric="380,000 records a month",
                skills=["Python", "SQL", "ETL"],
            ),
            Evidence(
                id="ev-aws",
                text="I built a serverless pipeline on S3, Glue and Athena",
                skills=["AWS", "Athena", "PySpark"],
            ),
            Evidence(
                id="ev-unrelated",
                text="I wrote a game in Luau",
                skills=["Luau"],
            ),
        ],
        preferences=Preferences(
            min_salary=45000,
            locations=["London", "Remote"],
            contract_ok=False,
            exclude_companies=["Bad Corp"],
            exclude_title_terms=["sales"],
        ),
        voice_sample="I work on data pipelines and the automation around them.",
    )


@pytest.fixture
def scoring() -> ScoringConfig:
    return ScoringConfig()


def make_job(**overrides) -> Job:
    defaults = dict(
        source="test",
        external_id="1",
        title="Data Engineer",
        company="Example Ltd",
        description=(
            "We are looking for a Data Engineer with strong Python and SQL. "
            "You will build ETL pipelines on AWS. PySpark experience is a plus. "
            "3 years of experience required."
        ),
        url="https://example.com/job/1",
        location="London, UK",
        salary_min=55000,
        salary_max=70000,
        contract_type="permanent",
        posted_at=datetime.now(UTC) - timedelta(days=1),
    )
    defaults.update(overrides)
    return Job(**defaults)
