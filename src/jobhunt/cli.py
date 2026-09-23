"""Command line entry point.

    jobhunt check      validate config, profile and keys without calling anything
    jobhunt run        one pass: fetch, score, research, draft, notify
    jobhunt watch      the same on a timer until stopped
    jobhunt review     serve the review interface
    jobhunt status     what is in the queue

`check` exists because the first run of a tool like this fails for boring
reasons: a key not exported, a profile field missing, a topic left blank. Better
to say all of that at once, before the first API call, than to discover it one
exception at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config
from .letters.drafter import LetterDrafter
from .matching.profile import ProfileError, load_profile
from .models import Stage
from .notify import build_notifier
from .notify.base import NullNotifier
from .pipeline import Pipeline
from .research.company import CompanyResearcher
from .sources import build_sources
from .store import Store


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load(args: argparse.Namespace) -> tuple[Config, Store]:
    config = load_config(args.config)
    return config, Store(config.database_path)


def _build_pipeline(config: Config, store: Store, dry_run: bool) -> Pipeline:
    profile = load_profile(config.profile_path)
    notifier = (
        NullNotifier(config.notify)
        if dry_run
        else build_notifier(config.notify, config.ntfy_topic)
    )
    return Pipeline(
        config=config,
        profile=profile,
        store=store,
        sources=build_sources(config.sources),
        notifier=notifier,
        drafter=LetterDrafter(
            profile,
            backend=config.letters.backend,
            model=config.letters.model,
            max_words=config.letters.max_words,
            api_key=config.anthropic_api_key,
        ),
        researcher=CompanyResearcher(config.companies_house_key),
    )


# ----------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    problems: list[str] = []
    notes: list[str] = []

    try:
        profile = load_profile(config.profile_path)
        print(f"profile      {profile.name} <{profile.email}>")
        print(f"             {len(profile.evidence)} evidence entries, "
              f"{len(profile.must_have_skills)} core skills")
        if not profile.voice_sample:
            notes.append("no voice_sample in profile: letters will read more generic")
    except ProfileError as exc:
        problems.append(str(exc))

    enabled = [name for name, source in config.sources.items() if source.enabled]
    print(f"sources      {', '.join(enabled) or 'none enabled'}")
    if not enabled:
        problems.append("no sources enabled in config.yaml")

    missing = config.missing_secrets()
    if missing:
        problems.append("missing environment variables: " + ", ".join(missing))

    print(f"notify       {config.notify.backend}")
    print(f"letters      {config.letters.backend}"
          + (" (claude available)" if config.anthropic_api_key else " (template only)"))
    print(f"research     {'companies house' if config.companies_house_key else 'disabled'}")

    cv = Path(config.apply.cv_path)
    print(f"cv           {cv} {'found' if cv.exists() else 'NOT FOUND'}")
    if not cv.exists():
        notes.append(f"no CV at {cv}: assisted fill will not attach one")

    for note in notes:
        print(f"note         {note}")
    for problem in problems:
        print(f"PROBLEM      {problem}", file=sys.stderr)

    if problems:
        print("\nFix the problems above, then run jobhunt check again.", file=sys.stderr)
        return 1

    print("\nReady. Run: jobhunt run --dry-run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config, store = _load(args)
    try:
        pipeline = _build_pipeline(config, store, args.dry_run)
        report = asyncio.run(pipeline.run(dry_run=args.dry_run))
    finally:
        store.close()

    print(report.summary())
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    if report.notified:
        print(f"\nReview them: jobhunt review  ->  {config.notify.review_base_url}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .daemon import Daemon

    config, store = _load(args)
    try:
        pipeline = _build_pipeline(config, store, args.dry_run)
        daemon = Daemon(
            pipeline,
            interval_minutes=config.daemon.interval_minutes,
            quiet_hours=config.daemon.quiet_hours,
        )
        asyncio.run(daemon.run_forever(dry_run=args.dry_run))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        store.close()
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    import uvicorn

    from .review.app import create_app

    config, store = _load(args)
    app = create_app(config, store)
    # Localhost only. See review/app.py for why this is not configurable.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    store.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config, store = _load(args)
    try:
        counts = store.counts_by_stage()
        if not counts:
            print("nothing yet. Run: jobhunt run")
            return 0

        width = max(len(stage) for stage in counts)
        for stage, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{stage.replace('_', ' '):<{width}}  {count}")

        waiting = store.list_applications(Stage.AWAITING_REVIEW, limit=5)
        if waiting:
            print("\nwaiting for you:")
            for app in waiting:
                score = app.match.score if app.match else 0
                print(f"  {score:5.0f}  {app.job.title} - {app.job.company}")
    finally:
        store.close()
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobhunt",
        description="Find, score and draft for jobs worth applying to. You decide the rest.",
    )
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="validate configuration without calling anything")

    run = sub.add_parser("run", help="one pass")
    run.add_argument("--dry-run", action="store_true", help="do everything except notify")

    watch = sub.add_parser("watch", help="run on a timer until stopped")
    watch.add_argument("--dry-run", action="store_true")

    review = sub.add_parser("review", help="serve the review interface")
    review.add_argument("--port", type=int, default=8765)

    sub.add_parser("status", help="what is in the queue")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    handlers = {
        "check": cmd_check,
        "run": cmd_run,
        "watch": cmd_watch,
        "review": cmd_review,
        "status": cmd_status,
    }

    try:
        return handlers[args.command](args)
    except (ConfigError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
