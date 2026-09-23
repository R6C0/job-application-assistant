"""The review interface.

A small local web app, bound to localhost, holding the only code in this project
that can move an application to APPROVED. That is not incidental: the approve
route is a POST from a form a person has to look at, and `Application.advance`
refuses SUBMITTED from anywhere else.

It is not an API. There is no authentication because there is no remote access,
and adding either would mean this could run somewhere that removes the human
from the loop.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..models import Stage
from ..store import Store
from .api import build_api

log = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


#: Where `npm run build` puts the single-page app.
UI_DIST = Path(__file__).parent.parent.parent.parent / "web" / "dist"


def create_app(config: Config, store: Store) -> FastAPI:
    app = FastAPI(title="jobhunt review", docs_url=None, redoc_url=None)

    # JSON API first, so /api never collides with the SPA catch-all below.
    app.include_router(build_api(config, store))

    @app.get("/legacy", response_class=HTMLResponse)
    async def queue(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "queue.html",
            {
                "awaiting": store.list_applications(Stage.AWAITING_REVIEW),
                "approved": store.list_applications(Stage.APPROVED),
                "submitted": store.list_applications(Stage.SUBMITTED, limit=20),
                "counts": store.counts_by_stage(),
            },
        )

    @app.get("/legacy/job/{job_id}", response_class=HTMLResponse)
    async def detail(request: Request, job_id: str):
        app_record = store.get_application(job_id)
        if not app_record:
            raise HTTPException(404, "no such application")
        return TEMPLATES.TemplateResponse(
            request,
            "detail.html",
            {
                "app": app_record,
                "cv_path": config.apply.cv_path,
            },
        )

    @app.post("/legacy/job/{job_id}/letter")
    async def save_letter(job_id: str, body: str = Form(...)):
        app_record = store.get_application(job_id)
        if not app_record or not app_record.letter:
            raise HTTPException(404, "no draft to edit")

        changed = body.strip() != app_record.letter.body.strip()
        app_record.letter.body = body.strip()
        # Only flag as human-edited if it actually changed. Opening a draft and
        # pressing save unchanged is not authorship, and the flag is shown on
        # the approve screen precisely so you can see you have not read it yet.
        if changed:
            app_record.letter.edited_by_human = True
        store.save_application(app_record)
        return RedirectResponse(f"/legacy/job/{job_id}", status_code=303)

    @app.post("/legacy/job/{job_id}/approve")
    async def approve(job_id: str):
        app_record = store.get_application(job_id)
        if not app_record:
            raise HTTPException(404, "no such application")
        if app_record.stage is not Stage.AWAITING_REVIEW:
            raise HTTPException(409, f"cannot approve from {app_record.stage.value}")

        app_record.advance(Stage.APPROVED)
        store.save_application(app_record)
        log.info("approved %s (%s)", job_id, app_record.job.title)
        return RedirectResponse(f"/legacy/job/{job_id}", status_code=303)

    @app.post("/legacy/job/{job_id}/skip")
    async def skip(job_id: str, reason: str = Form("")):
        app_record = store.get_application(job_id)
        if not app_record:
            raise HTTPException(404, "no such application")
        app_record.notes = reason or "skipped in review"
        app_record.advance(Stage.ABANDONED)
        store.save_application(app_record)
        return RedirectResponse("/legacy", status_code=303)

    @app.post("/legacy/job/{job_id}/mark-submitted")
    async def mark_submitted(job_id: str):
        """Record that *you* submitted it on the employer's site.

        This tool never presses submit. The button records what you did, so the
        tracker is accurate and the same job is not surfaced again.
        """
        app_record = store.get_application(job_id)
        if not app_record:
            raise HTTPException(404, "no such application")
        try:
            app_record.advance(Stage.SUBMITTED)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.save_application(app_record)
        return RedirectResponse("/legacy", status_code=303)

    @app.post("/legacy/job/{job_id}/assist")
    async def assist(job_id: str):
        """Open the posting in a browser with your details filled in."""
        app_record = store.get_application(job_id)
        if not app_record:
            raise HTTPException(404, "no such application")
        if app_record.stage is not Stage.APPROVED:
            raise HTTPException(409, "approve the application before opening the form")

        from ..apply.browser import AssistedApply

        helper = AssistedApply(config, headless=config.apply.headless)
        try:
            filled = await helper.open_and_fill(app_record)
            app_record.notes = f"assisted fill: {filled}"
        except Exception as exc:  # noqa: BLE001
            log.warning("assisted fill failed for %s: %s", job_id, exc)
            app_record.notes = f"assisted fill failed: {exc}"
        store.save_application(app_record)
        return RedirectResponse(f"/legacy/job/{job_id}", status_code=303)

    # ------------------------------------------------------------------
    # The single-page app, when it has been built.
    #
    # Mounted last so it cannot shadow /api or /legacy. If web/dist is absent
    # the server still works: / redirects to the Jinja pages, which is the
    # point of keeping them.
    # ------------------------------------------------------------------
    if (UI_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", response_class=HTMLResponse)
        async def spa(full_path: str):
            """Serve index.html for any non-API path, so client routing works."""
            candidate = UI_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

        log.info("serving the React interface from %s", UI_DIST)
    else:

        @app.get("/", response_class=HTMLResponse)
        async def root_fallback():
            log.info("web/dist not found; falling back to the server-rendered pages")
            return RedirectResponse("/legacy", status_code=307)

    return app
