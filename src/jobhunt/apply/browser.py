"""Assisted form filling.

What this does: opens the posting in a real, visible browser, finds the fields it
recognises, types your details in, attaches your CV and pastes the cover letter.

What it does not do: press submit. Not because that is hard, but because an
unattended submit that goes wrong cannot be recalled, and the entire value of
this tool is that you are applying to jobs you have actually read.

Selector strategy is deliberately boring. ATS vendors change their markup
regularly, so matching is by field semantics (autocomplete attribute, input
type, label text, placeholder) rather than by CSS path, and every field is
best-effort: a miss leaves the field empty for you to fill, which is a normal
Tuesday rather than a failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from ..models import Application, Stage

log = logging.getLogger(__name__)

#: Ordered candidate selectors per logical field. First hit wins.
FIELD_SELECTORS: dict[str, list[str]] = {
    "first_name": [
        "input[autocomplete='given-name']",
        "input[name*='first' i]",
        "input[id*='first' i]",
    ],
    "last_name": [
        "input[autocomplete='family-name']",
        "input[name*='last' i]",
        "input[id*='last' i]",
    ],
    "full_name": [
        "input[autocomplete='name']",
        "input[name='name']",
        "input[name*='full' i]",
        "input[id*='fullname' i]",
    ],
    "email": [
        "input[type='email']",
        "input[autocomplete='email']",
        "input[name*='email' i]",
    ],
    "phone": [
        "input[type='tel']",
        "input[autocomplete='tel']",
        "input[name*='phone' i]",
    ],
    "location": [
        "input[name*='location' i]",
        "input[name*='city' i]",
        "input[autocomplete='address-level2']",
    ],
    "linkedin": [
        "input[name*='linkedin' i]",
        "input[name*='urls' i][name*='linked' i]",
    ],
    "cover_letter": [
        "textarea[name*='cover' i]",
        "textarea[id*='cover' i]",
        "textarea[name*='letter' i]",
        "textarea[aria-label*='cover' i]",
    ],
    "cv": [
        "input[type='file'][name*='resume' i]",
        "input[type='file'][name*='cv' i]",
        "input[type='file']",
    ],
}


class AssistedApply:
    def __init__(self, config: Config, headless: bool = False) -> None:
        self.config = config
        # Headless defeats the point: you cannot check a form you cannot see.
        self.headless = headless

    async def open_and_fill(self, app: Application) -> str:
        """Open the posting and fill what can be found. Returns a summary."""
        if app.stage is not Stage.APPROVED:
            raise ValueError(
                f"refusing to open a form for {app.job.id}: stage is "
                f"{app.stage.value}, expected approved"
            )

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Run: pip install '.[browser]' "
                "&& playwright install chromium"
            ) from exc

        profile = self._profile_values(app)
        filled: list[str] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            await page.goto(app.job.url, wait_until="domcontentloaded", timeout=45_000)
            await self._dismiss_cookie_banner(page)
            await self._follow_apply_link(page)

            for field, value in profile.items():
                if value and await self._fill(page, field, value):
                    filled.append(field)

            if app.letter and await self._fill(page, "cover_letter", app.letter.body):
                filled.append("cover_letter")

            cv = Path(self.config.apply.cv_path)
            if cv.exists() and await self._attach(page, cv):
                filled.append("cv")
            elif not cv.exists():
                log.warning("cv not found at %s; nothing attached", cv)

            log.info(
                "form ready for %s. Filled: %s. Browser stays open for you to "
                "check and submit.",
                app.job.company,
                ", ".join(filled) or "nothing",
            )

            # Hold the browser open. Closing it here would throw away the work.
            await page.pause() if not self.headless else None
            await browser.close()

        return ", ".join(filled) or "no recognised fields"

    # ------------------------------------------------------------------
    def _profile_values(self, app: Application) -> dict[str, str]:
        from ..matching.profile import load_profile

        profile = load_profile(self.config.profile_path)
        parts = profile.name.split()

        return {
            "first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "full_name": profile.name,
            "email": profile.email,
            "phone": profile.phone or "",
            "location": profile.location or "",
        }

    async def _fill(self, page, field: str, value: str) -> bool:  # noqa: ANN001
        for selector in FIELD_SELECTORS.get(field, []):
            try:
                element = page.locator(selector).first
                if await element.count() == 0 or not await element.is_visible():
                    continue
                await element.fill(value, timeout=4000)
                return True
            except Exception:  # noqa: BLE001 - a miss is expected, not exceptional
                continue
        return False

    async def _attach(self, page, cv: Path) -> bool:  # noqa: ANN001
        for selector in FIELD_SELECTORS["cv"]:
            try:
                element = page.locator(selector).first
                if await element.count() == 0:
                    continue
                await element.set_input_files(str(cv), timeout=6000)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _dismiss_cookie_banner(self, page) -> None:  # noqa: ANN001
        """Banners sit over the form and swallow clicks."""
        for label in ("Accept all", "Accept All", "I accept", "Allow all", "Got it"):
            try:
                button = page.get_by_role("button", name=label)
                if await button.count():
                    await button.first.click(timeout=2500)
                    return
            except Exception:  # noqa: BLE001
                continue

    async def _follow_apply_link(self, page) -> None:  # noqa: ANN001
        """Aggregator links land on a description, not the form."""
        for label in ("Apply now", "Apply for this job", "Apply"):
            try:
                link = page.get_by_role("link", name=label)
                if await link.count():
                    await link.first.click(timeout=4000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return
            except Exception:  # noqa: BLE001
                continue
