"""ntfy.sh push notifications.

Chosen because it needs no account, no bot registration and no OAuth: you pick a
topic name, subscribe to it in the phone app, and POST to it. The cost of that
simplicity is that the topic name is the only secret, so it has to be an
unguessable string rather than "joshua-jobs".

Anyone who knows your topic can read your notifications. The tool warns about
short topics for that reason.
"""

from __future__ import annotations

import logging

import httpx

from .base import Notification, Notifier

log = logging.getLogger(__name__)

MIN_TOPIC_LENGTH = 16

_PRIORITY = {"low": "2", "default": "3", "high": "4"}


class NtfyNotifier(Notifier):
    async def send(self, client: httpx.AsyncClient, note: Notification) -> bool:
        if not self.topic:
            log.warning("ntfy: no topic configured; set JOBHUNT_NTFY_TOPIC")
            return False

        if len(self.topic) < MIN_TOPIC_LENGTH:
            log.warning(
                "ntfy: topic %r is short enough to guess. Anyone who guesses it "
                "sees your job notifications. Use a random string of %d or more "
                "characters.",
                self.topic,
                MIN_TOPIC_LENGTH,
            )

        headers = {
            "Title": note.title.encode("ascii", "replace").decode(),
            "Priority": _PRIORITY.get(note.priority, "3"),
            "Tags": "briefcase",
        }
        if note.url:
            # Tapping the notification opens the review page for this job.
            headers["Click"] = note.url
            headers["Actions"] = f"view, Review, {note.url}"

        try:
            response = await client.post(
                f"{self.config.ntfy_server.rstrip('/')}/{self.topic}",
                content=note.body.encode("utf-8"),
                headers=headers,
                timeout=15.0,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("ntfy: send failed: %s", exc)
            return False
