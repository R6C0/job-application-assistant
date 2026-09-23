"""The run: fetch, deduplicate, score, research, draft, notify, stop.

Stop is the important step. The pipeline's last action is to put an application
in `AWAITING_REVIEW` and tell you about it. It has no code path that submits
anything, which is the design working rather than a feature not yet built.

Ordering exists to spend effort only where it is earned. Scoring is free and
happens to everything; company research costs an API call and only happens to
jobs that cleared the threshold; drafting costs money and only happens to jobs
that survived research. Roughly 90% of what is fetched never reaches the drafter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .config import Config
from .letters.drafter import LetterDrafter
from .matching.profile import Profile
from .matching.scorer import Scorer
from .models import Application, Job, Stage
from .notify import Notification, Notifier
from .research.company import CompanyResearcher
from .sources import JobSource, SourceError
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    blocked: int = 0
    below_threshold: int = 0
    drafted: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)

    def summary(self) -> str:
        return (
            f"fetched {self.fetched}, new {self.new}, "
            f"blocked {self.blocked}, below threshold {self.below_threshold}, "
            f"drafted {self.drafted}, notified {self.notified}"
            + (f", {len(self.errors)} errors" if self.errors else "")
        )


class Pipeline:
    def __init__(
        self,
        config: Config,
        profile: Profile,
        store: Store,
        sources: list[JobSource],
        notifier: Notifier,
        drafter: LetterDrafter | None = None,
        researcher: CompanyResearcher | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.store = store
        self.sources = sources
        self.notifier = notifier
        self.scorer = Scorer(profile, config.scoring)
        self.drafter = drafter or LetterDrafter(
            profile,
            backend=config.letters.backend,
            model=config.letters.model,
            max_words=config.letters.max_words,
            api_key=config.anthropic_api_key,
        )
        self.researcher = researcher or CompanyResearcher(config.companies_house_key)

    async def run(self, dry_run: bool = False) -> RunReport:
        report = RunReport()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            jobs = await self._fetch(client, report)
            candidates = self._triage(jobs, report)

            budget = self._notification_budget()
            for app in candidates[: self.config.notify.max_per_run]:
                await self._enrich(client, app, report)

                if budget <= 0:
                    log.info("daily notification budget spent; %s stays queued", app.job.id)
                    self.store.save_application(app)
                    continue

                if await self._notify(client, app, dry_run):
                    report.notified += 1
                    budget -= 1

                self.store.save_application(app)

        log.info("run complete: %s", report.summary())
        return report

    # ------------------------------------------------------------------
    async def _fetch(self, client: httpx.AsyncClient, report: RunReport) -> list[Job]:
        jobs: list[Job] = []
        for source in self.sources:
            try:
                found = await source.fetch(client)
                log.info("%s: %d postings", source.name, len(found))
                jobs.extend(found)
            except SourceError as exc:
                # A missing key for one board should not stop the other.
                log.error("%s: %s", source.name, exc)
                report.errors.append(f"{source.name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                log.exception("%s: unexpected failure", source.name)
                report.errors.append(f"{source.name}: {exc}")

        report.fetched = len(jobs)
        return jobs

    def _triage(self, jobs: list[Job], report: RunReport) -> list[Application]:
        """Store everything, score everything, return only what is worth effort."""
        candidates: list[Application] = []

        for job in jobs:
            is_new = self.store.upsert_job(job)
            if not is_new:
                continue
            report.new += 1

            # The same role from two boards: keep the first, note the second.
            if self.store.seen_dedupe_key(job.dedupe_key, job.id):
                report.duplicates += 1
                app = Application(
                    job=job,
                    stage=Stage.REJECTED,
                    notes="duplicate of another source",
                )
                self.store.save_application(app)
                continue

            app = Application(job=job)
            app.match = self.scorer.score(job)
            app.advance(Stage.SCORED)

            if app.match.blockers:
                report.blocked += 1
                app.notes = "; ".join(app.match.blockers)
                app.advance(Stage.REJECTED)
                self.store.save_application(app)
                continue

            if app.match.score < self.config.scoring.notify_threshold:
                report.below_threshold += 1
                app.notes = f"scored {app.match.score} below threshold"
                app.advance(Stage.REJECTED)
                self.store.save_application(app)
                continue

            candidates.append(app)

        candidates.sort(key=lambda a: a.match.score if a.match else 0, reverse=True)
        return candidates

    async def _enrich(
        self, client: httpx.AsyncClient, app: Application, report: RunReport
    ) -> None:
        try:
            app.brief = await self.researcher.research(client, app.job.company)
        except Exception as exc:  # noqa: BLE001
            log.warning("research failed for %s: %s", app.job.company, exc)
        app.advance(Stage.RESEARCHED)

        try:
            app.letter = self.drafter.draft(app.job, app.match, app.brief)
            report.drafted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("drafting failed for %s: %s", app.job.id, exc)
            report.errors.append(f"draft {app.job.id}: {exc}")

        app.advance(Stage.DRAFTED)
        app.advance(Stage.AWAITING_REVIEW)

    async def _notify(
        self, client: httpx.AsyncClient, app: Application, dry_run: bool
    ) -> bool:
        if self.store.already_notified(app.job.id):
            return False

        note = Notification.for_application(app, self.config.notify.review_base_url)
        if dry_run:
            log.info("[dry-run] would notify: %s | %s", note.title, note.url)
            return True

        if await self.notifier.send(client, note):
            app.notified_at = datetime.now()
            self.store.record_notification(app.job.id)
            return True
        return False

    def _notification_budget(self) -> int:
        used = self.store.notifications_today()
        return max(0, self.config.notify.max_per_day - used)
