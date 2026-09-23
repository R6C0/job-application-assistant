"""Desktop notifications, as a fallback when you are at the machine anyway.

Implemented with PowerShell toast on Windows and `notify-send` elsewhere, both
shelled out to rather than pulled in as a dependency. A job search tool should
not need a GUI toolkit installed to tell you about a job.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys

import httpx

from .base import Notification, Notifier

log = logging.getLogger(__name__)

_PS_TOAST = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, `
    ContentType=WindowsRuntime] > $null
$type = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($type)
$texts = $template.GetElementsByTagName("text")
$texts.Item(0).AppendChild($template.CreateTextNode({title})) > $null
$texts.Item(1).AppendChild($template.CreateTextNode({body})) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("jobhunt").Show($toast)
"""


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class DesktopNotifier(Notifier):
    async def send(self, client: httpx.AsyncClient, note: Notification) -> bool:
        body = note.body if not note.url else f"{note.body}\n{note.url}"
        try:
            if sys.platform == "win32":
                return await self._windows(note.title, body)
            return await self._linux(note.title, body)
        except Exception as exc:  # noqa: BLE001 - never break the pipeline
            log.warning("desktop notify failed: %s", exc)
            return False

    async def _windows(self, title: str, body: str) -> bool:
        script = _PS_TOAST.format(title=_ps_quote(title), body=_ps_quote(body))
        process = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await process.wait() == 0

    async def _linux(self, title: str, body: str) -> bool:
        if not shutil.which("notify-send"):
            log.info("desktop notify: notify-send not installed")
            return False
        process = await asyncio.create_subprocess_exec(
            "notify-send", title, body,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await process.wait() == 0
