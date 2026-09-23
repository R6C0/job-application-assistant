from __future__ import annotations

import abc
import logging
from dataclasses import dataclass

import httpx

from ..config import NotifyConfig
from ..models import Application

log = logging.getLogger(__name__)


@dataclass
class Notification:
    title: str
    body: str
    url: str | None = None
    priority: str = "default"       # low | default | high

    @classmethod
    def for_application(cls, app: Application, review_base_url: str) -> Notification:
        score = app.match.score if app.match else 0
        salary = ""
        if app.job.salary_max:
            salary = f" | up to {app.job.salary_max:,.0f}"

        top = app.match.top_reasons(2) if app.match else []
        why = " | ".join(r.detail for r in top)

        return cls(
            title=f"{score:.0f}% {app.job.title}",
            body=f"{app.job.company}{salary}\n{why}",
            url=f"{review_base_url}/job/{app.job.id}",
            priority="high" if score >= 85 else "default",
        )


class Notifier(abc.ABC):
    def __init__(self, config: NotifyConfig, topic: str | None = None) -> None:
        self.config = config
        self.topic = topic

    @abc.abstractmethod
    async def send(self, client: httpx.AsyncClient, note: Notification) -> bool:
        """Deliver. Returns True on success.

        Never raises: a notifier that takes down the pipeline is worse than one
        that drops a message, because the application is already saved and
        reviewable either way.
        """


class NullNotifier(Notifier):
    """Logs instead of sending. Used by `--dry-run` and when disabled."""

    async def send(self, client: httpx.AsyncClient, note: Notification) -> bool:
        log.info("[notify:dry-run] %s | %s | %s", note.title, note.body, note.url)
        return True
