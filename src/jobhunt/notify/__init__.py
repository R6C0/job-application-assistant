"""Notification backends.

A notifier's contract is narrow on purpose: it is handed a finished summary and
a link, and its only job is delivery. It does not decide whether to send. That
decision lives in the pipeline, with the budget and the quiet hours, because
"should this interrupt someone" is a policy question and not a transport one.
"""

from __future__ import annotations

import logging

from ..config import NotifyConfig
from .base import Notification, Notifier, NullNotifier
from .desktop import DesktopNotifier
from .ntfy import NtfyNotifier

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[Notifier]] = {
    "ntfy": NtfyNotifier,
    "desktop": DesktopNotifier,
    "none": NullNotifier,
}


def build_notifier(config: NotifyConfig, topic: str | None = None) -> Notifier:
    backend = config.backend.lower()
    if backend not in REGISTRY:
        log.warning("unknown notify backend %r; notifications disabled", backend)
        return NullNotifier(config, topic)
    return REGISTRY[backend](config, topic)


__all__ = ["Notification", "Notifier", "NullNotifier", "build_notifier"]
