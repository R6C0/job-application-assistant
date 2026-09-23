"""JSON API for the React review interface.

The HTML templates remain as a no-build fallback. This module is what the
single-page app talks to.

The human gate is unchanged and lives where it always did, in
`Application.advance`. `/approve` is still the only route that can set
`APPROVED`, and it still requires a deliberate call from a person looking at a
screen. Moving the interface to React does not move the gate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from ..config import Config
from ..letters.drafter import BANNED_PHRASES
from ..models import Application, Stage
from ..store import Store

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Wire format
# ----------------------------------------------------------------------
class ReasonOut(BaseModel):
    component: str
    points: float
    detail: str
    evidence: list[str] = Field(default_factory=list)


class MatchOut(BaseModel):
    score: float
    reasons: list[ReasonOut]
    matched_skills: list[str]
    missing_skills: list[str]
    blockers: list[str]


class BriefOut(BaseModel):
    name: str
    summary: str
    facts: dict[str, str]
    sources: list[str]
    confidence: str


class LetterOut(BaseModel):
    body: str
    evidence_used: list[str]
    generator: str
    edited_by_human: bool
    word_count: int


class JobOut(BaseModel):
    id: str
    source: str
    title: str
    company: str
    description: str
    url: str
    location: str | None = None
    remote: bool | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "GBP"
    contract_type: str | None = None
    posted_at: datetime | None = None


class ApplicationOut(BaseModel):
    job: JobOut
    stage: str
    match: MatchOut | None = None
    brief: BriefOut | None = None
    letter: LetterOut | None = None
    notes: str = ""
    notified_at: datetime | None = None
    submitted_at: datetime | None = None


class StatsOut(BaseModel):
    counts: dict[str, int]
    awaiting: int
    approved: int
    submitted: int
    rejected: int
    notified_today: int
    average_score: float | None = None


class RunStateOut(BaseModel):
    running: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str | None = None
    errors: list[str] = Field(default_factory=list)


def _to_out(app: Application) -> ApplicationOut:
    return ApplicationOut(
        job=JobOut(
            id=app.job.id,
            source=app.job.source,
            title=app.job.title,
            company=app.job.company,
            description=app.job.description,
            url=app.job.url,
            location=app.job.location,
            remote=app.job.remote,
            salary_min=app.job.salary_min,
            salary_max=app.job.salary_max,
            currency=app.job.currency,
            contract_type=app.job.contract_type,
            posted_at=app.job.posted_at,
        ),
        stage=app.stage.value,
        match=(
            MatchOut(
                score=app.match.score,
                reasons=[ReasonOut(**r.__dict__) for r in app.match.reasons],
                matched_skills=app.match.matched_skills,
                missing_skills=app.match.missing_skills,
                blockers=app.match.blockers,
            )
            if app.match
            else None
        ),
        brief=BriefOut(**app.brief.__dict__) if app.brief else None,
        letter=(
            LetterOut(
                body=app.letter.body,
                evidence_used=app.letter.evidence_used,
                generator=app.letter.generator,
                edited_by_human=app.letter.edited_by_human,
                word_count=len(app.letter.body.split()),
            )
            if app.letter
            else None
        ),
        notes=app.notes,
        notified_at=app.notified_at,
        submitted_at=app.submitted_at,
    )


# ----------------------------------------------------------------------
# A single background run, so the UI can trigger a fetch without blocking.
# ----------------------------------------------------------------------
class RunState:
    """One run at a time. A second request while running is refused, not queued.

    Queuing would let an impatient double-click cost two rounds of API quota,
    and the second run would find nothing new anyway.
    """

    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.summary: str | None = None
        self.errors: list[str] = []

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def snapshot(self) -> RunStateOut:
        return RunStateOut(
            running=self.running,
            started_at=self.started_at,
            finished_at=self.finished_at,
            summary=self.summary,
            errors=self.errors,
        )


def build_api(config: Config, store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")
    run_state = RunState()

    # ------------------------------------------------------------------
    @router.get("/stats", response_model=StatsOut)
    async def stats() -> StatsOut:
        counts = store.counts_by_stage()
        awaiting = store.list_applications(Stage.AWAITING_REVIEW, limit=500)
        scores = [a.match.score for a in awaiting if a.match]
        return StatsOut(
            counts=counts,
            awaiting=counts.get(Stage.AWAITING_REVIEW.value, 0),
            approved=counts.get(Stage.APPROVED.value, 0),
            submitted=counts.get(Stage.SUBMITTED.value, 0),
            rejected=counts.get(Stage.REJECTED.value, 0),
            notified_today=store.notifications_today(),
            average_score=round(sum(scores) / len(scores), 1) if scores else None,
        )

    @router.get("/applications", response_model=list[ApplicationOut])
    async def list_applications(
        stage: str | None = None, limit: int = 100
    ) -> list[ApplicationOut]:
        stage_enum = None
        if stage:
            try:
                stage_enum = Stage(stage)
            except ValueError as exc:
                raise HTTPException(400, f"unknown stage {stage!r}") from exc
        return [_to_out(a) for a in store.list_applications(stage_enum, limit=limit)]

    @router.get("/applications/{job_id}", response_model=ApplicationOut)
    async def get_application(job_id: str) -> ApplicationOut:
        app = store.get_application(job_id)
        if not app:
            raise HTTPException(404, "no such application")
        return _to_out(app)

    # ------------------------------------------------------------------
    @router.put("/applications/{job_id}/letter", response_model=ApplicationOut)
    async def save_letter(job_id: str, body: str = Body(..., embed=True)) -> ApplicationOut:
        app = store.get_application(job_id)
        if not app or not app.letter:
            raise HTTPException(404, "no draft to edit")

        if body.strip() != app.letter.body.strip():
            app.letter.edited_by_human = True
        app.letter.body = body.strip()
        store.save_application(app)
        return _to_out(app)

    @router.post("/applications/{job_id}/approve", response_model=ApplicationOut)
    async def approve(job_id: str) -> ApplicationOut:
        app = store.get_application(job_id)
        if not app:
            raise HTTPException(404, "no such application")
        if app.stage is not Stage.AWAITING_REVIEW:
            raise HTTPException(409, f"cannot approve from {app.stage.value}")

        app.advance(Stage.APPROVED)
        store.save_application(app)
        log.info("approved %s (%s)", job_id, app.job.title)
        return _to_out(app)

    @router.post("/applications/{job_id}/skip", response_model=ApplicationOut)
    async def skip(job_id: str, reason: str = Body("", embed=True)) -> ApplicationOut:
        app = store.get_application(job_id)
        if not app:
            raise HTTPException(404, "no such application")
        app.notes = reason or "skipped in review"
        app.advance(Stage.ABANDONED)
        store.save_application(app)
        return _to_out(app)

    @router.post("/applications/{job_id}/mark-submitted", response_model=ApplicationOut)
    async def mark_submitted(job_id: str) -> ApplicationOut:
        """Record that you submitted it. This tool never presses submit."""
        app = store.get_application(job_id)
        if not app:
            raise HTTPException(404, "no such application")
        try:
            app.advance(Stage.SUBMITTED)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.save_application(app)
        return _to_out(app)

    @router.post("/applications/{job_id}/assist", response_model=ApplicationOut)
    async def assist(job_id: str) -> ApplicationOut:
        app = store.get_application(job_id)
        if not app:
            raise HTTPException(404, "no such application")
        if app.stage is not Stage.APPROVED:
            raise HTTPException(409, "approve the application before opening the form")

        from ..apply.browser import AssistedApply

        helper = AssistedApply(config, headless=config.apply.headless)
        try:
            filled = await helper.open_and_fill(app)
            app.notes = f"assisted fill: {filled}"
        except Exception as exc:  # noqa: BLE001
            log.warning("assisted fill failed for %s: %s", job_id, exc)
            app.notes = f"assisted fill failed: {exc}"
        store.save_application(app)
        return _to_out(app)

    # ------------------------------------------------------------------
    @router.get("/run", response_model=RunStateOut)
    async def run_status() -> RunStateOut:
        return run_state.snapshot()

    @router.post("/run", response_model=RunStateOut)
    async def start_run(dry_run: bool = False) -> RunStateOut:
        if run_state.running:
            raise HTTPException(409, "a run is already in progress")

        from ..cli import _build_pipeline

        pipeline = _build_pipeline(config, store, dry_run)
        run_state.started_at = datetime.now()
        run_state.finished_at = None
        run_state.summary = None
        run_state.errors = []

        async def go() -> None:
            try:
                report = await pipeline.run(dry_run=dry_run)
                run_state.summary = report.summary()
                run_state.errors = report.errors
            except Exception as exc:  # noqa: BLE001
                log.exception("run from the UI failed")
                run_state.summary = "run failed"
                run_state.errors = [str(exc)]
            finally:
                run_state.finished_at = datetime.now()

        run_state.task = asyncio.create_task(go())
        return run_state.snapshot()

    # ------------------------------------------------------------------
    @router.get("/meta")
    async def meta() -> dict[str, Any]:
        """Everything the client needs that is not per-application.

        `banned_phrases` is sent so the letter editor can highlight them as you
        type, using the same list the drafter validates against. One source of
        truth, and the highlight cannot drift from the rule.
        """
        return {
            "banned_phrases": BANNED_PHRASES,
            "notify_threshold": config.scoring.notify_threshold,
            "weights": config.scoring.weights,
            "cv_path": config.apply.cv_path,
            "letters_backend": config.letters.backend,
            "sources": [name for name, s in config.sources.items() if s.enabled],
        }

    return router


StageLiteral = Literal[
    "discovered",
    "scored",
    "rejected",
    "researched",
    "drafted",
    "awaiting_review",
    "approved",
    "submitted",
    "abandoned",
]
