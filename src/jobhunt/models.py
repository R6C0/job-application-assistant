"""Core domain types.

Everything that crosses a module boundary is one of these. They are deliberately
plain: no database rows, no HTTP payloads, no framework objects. Adapters convert
at the edges, which is what lets a new job source be added without touching the
scorer, and a new notifier without touching anything at all.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Stage(StrEnum):
    """Where an application has got to.

    The order matters. `Application.advance` refuses any transition that is not
    forward along this list, which is how the human gate is enforced as a
    property of the type rather than as a setting someone can flip.
    """

    DISCOVERED = "discovered"
    SCORED = "scored"
    REJECTED = "rejected"      # terminal: did not clear the bar
    RESEARCHED = "researched"
    DRAFTED = "drafted"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"       # a human pressed the button
    SUBMITTED = "submitted"
    ABANDONED = "abandoned"     # terminal: human said no


#: Stages from which no further progress is possible.
TERMINAL_STAGES = {Stage.REJECTED, Stage.SUBMITTED, Stage.ABANDONED}

#: The only stage that may precede SUBMITTED. See Application.advance.
SUBMIT_REQUIRES = Stage.APPROVED

_STAGE_ORDER = [
    Stage.DISCOVERED,
    Stage.SCORED,
    Stage.RESEARCHED,
    Stage.DRAFTED,
    Stage.AWAITING_REVIEW,
    Stage.APPROVED,
    Stage.SUBMITTED,
]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Job:
    """A posting, normalised from whichever source produced it."""

    source: str                  # "adzuna", "reed", ...
    external_id: str             # the source's own id
    title: str
    company: str
    description: str
    url: str
    location: str | None = None
    remote: bool | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "GBP"
    contract_type: str | None = None   # "permanent", "contract", ...
    posted_at: datetime | None = None
    fetched_at: datetime = field(default_factory=_now)

    @property
    def id(self) -> str:
        """Stable identity, so a rerun updates a row instead of adding one.

        Deliberately derived from source plus external id rather than from the
        content: a posting whose salary is edited is still the same posting, and
        re-notifying about it would train the reader to ignore notifications.
        """
        return hashlib.sha256(f"{self.source}:{self.external_id}".encode()).hexdigest()[:16]

    @property
    def dedupe_key(self) -> str:
        """Cross-source identity, for the same role listed on two boards.

        Company and title only, aggressively normalised. This will occasionally
        merge two genuinely different openings at the same company with the same
        title, which is the right way round: seeing one of them is recoverable,
        being notified twice is what makes someone stop reading.
        """
        norm = f"{self.company.strip().lower()}|{self.title.strip().lower()}"
        return hashlib.sha256(norm.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Reason:
    """One named contribution to a score.

    The whole point of the scorer is that it can explain itself, because the
    explanation is the raw material for the cover letter. A number on its own
    would tell you a job is a 78 and nothing you could write a sentence with.
    """

    component: str        # "skills", "title", "seniority", ...
    points: float         # signed contribution to the total
    detail: str           # human-readable, quotable
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Match:
    score: float                         # 0 to 100
    reasons: list[Reason]
    matched_skills: list[str]
    missing_skills: list[str]
    blockers: list[str] = field(default_factory=list)

    @property
    def is_viable(self) -> bool:
        return not self.blockers

    def top_reasons(self, n: int = 3) -> list[Reason]:
        return sorted(self.reasons, key=lambda r: r.points, reverse=True)[:n]


@dataclass(frozen=True)
class CompanyBrief:
    """What could be established about the employer from public records."""

    name: str
    summary: str
    facts: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    confidence: str = "low"   # low | medium | high

    @property
    def is_useful(self) -> bool:
        return bool(self.facts) and self.confidence != "low"


@dataclass
class Letter:
    body: str
    evidence_used: list[str]
    generator: str            # "claude" | "template"
    edited_by_human: bool = False
    created_at: datetime = field(default_factory=_now)


@dataclass
class Application:
    """A job plus everything derived from it, and how far it has got."""

    job: Job
    stage: Stage = Stage.DISCOVERED
    match: Match | None = None
    brief: CompanyBrief | None = None
    letter: Letter | None = None
    notified_at: datetime | None = None
    submitted_at: datetime | None = None
    notes: str = ""

    def advance(self, to: Stage) -> None:
        """Move the application forward, or refuse.

        Two invariants, both enforced here rather than at the call sites:

        1. Nothing leaves a terminal stage.
        2. SUBMITTED is reachable only from APPROVED, and APPROVED is only ever
           set by the review interface in response to a human action. There is
           no configuration flag that relaxes this, because the failure mode it
           prevents (a draft reaching a real employer unread) cannot be undone.
        """
        if self.stage in TERMINAL_STAGES:
            raise ValueError(f"{self.job.id} is already {self.stage.value}")

        if to is Stage.SUBMITTED and self.stage is not SUBMIT_REQUIRES:
            raise ValueError(
                f"refusing to submit {self.job.id} from {self.stage.value}: "
                f"submission requires {SUBMIT_REQUIRES.value}, set by human review"
            )

        if to in (Stage.REJECTED, Stage.ABANDONED):
            self.stage = to
            return

        if to in _STAGE_ORDER and self.stage in _STAGE_ORDER:
            if _STAGE_ORDER.index(to) <= _STAGE_ORDER.index(self.stage):
                raise ValueError(
                    f"refusing to move {self.job.id} backwards: "
                    f"{self.stage.value} -> {to.value}"
                )

        self.stage = to
        if to is Stage.SUBMITTED:
            self.submitted_at = _now()
