"""Configuration: a YAML file for preferences, environment for secrets.

The split is deliberate. `config.yaml` describes what you want and is safe to
commit or share; `.env` holds keys and is not. Nothing in this module reads a
secret out of YAML, so a config file that someone pastes into a chat cannot leak
a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


class ConfigError(RuntimeError):
    pass


@dataclass
class SourceConfig:
    enabled: bool = False
    queries: list[str] = field(default_factory=list)
    location: str = "London"
    radius_km: int = 30
    max_results: int = 50
    max_days_old: int = 14


@dataclass
class ScoringConfig:
    #: Below this, the job is dropped without a notification.
    notify_threshold: float = 65.0
    #: Component weights. They do not have to sum to anything in particular;
    #: the scorer normalises. Tune these rather than editing scorer.py.
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "skills": 40.0,
            "title": 20.0,
            "seniority": 15.0,
            "location": 10.0,
            "salary": 10.0,
            "recency": 5.0,
        }
    )


@dataclass
class NotifyConfig:
    backend: str = "ntfy"           # ntfy | desktop | none
    ntfy_topic: str = ""            # set via JOBHUNT_NTFY_TOPIC, not here
    ntfy_server: str = "https://ntfy.sh"
    review_base_url: str = "http://127.0.0.1:8765"
    #: Stop a bad day from turning into 40 phone buzzes.
    max_per_run: int = 10
    max_per_day: int = 25


@dataclass
class LettersConfig:
    backend: str = "auto"           # auto | claude | template
    model: str = "claude-sonnet-5"
    max_words: int = 320
    tone_sample_path: str | None = None


@dataclass
class ApplyConfig:
    #: There is no auto_submit option. See models.Application.advance and
    #: docs/DESIGN-DECISIONS.md. Assisted fill opens a real browser, fills what
    #: it can, and stops.
    assisted_fill: bool = True
    headless: bool = False
    cv_path: str = "cv.pdf"


@dataclass
class DaemonConfig:
    interval_minutes: int = 90
    #: Quiet hours in local time, inclusive start, exclusive end.
    quiet_hours: tuple[int, int] = (22, 7)


@dataclass
class Config:
    profile_path: str = "profile.yaml"
    database_path: str = "jobhunt.db"
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    letters: LettersConfig = field(default_factory=LettersConfig)
    apply: ApplyConfig = field(default_factory=ApplyConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)

    # ------------------------------------------------------------------
    # Secrets. Read from the environment every time rather than cached, so
    # rotating a key does not need a restart.
    # ------------------------------------------------------------------
    @property
    def adzuna_app_id(self) -> str | None:
        return os.getenv("ADZUNA_APP_ID")

    @property
    def adzuna_app_key(self) -> str | None:
        return os.getenv("ADZUNA_APP_KEY")

    @property
    def reed_api_key(self) -> str | None:
        return os.getenv("REED_API_KEY")

    @property
    def anthropic_api_key(self) -> str | None:
        return os.getenv("ANTHROPIC_API_KEY")

    @property
    def companies_house_key(self) -> str | None:
        return os.getenv("COMPANIES_HOUSE_API_KEY")

    @property
    def ntfy_topic(self) -> str | None:
        return os.getenv("JOBHUNT_NTFY_TOPIC") or (self.notify.ntfy_topic or None)

    def missing_secrets(self) -> list[str]:
        """What is configured as enabled but has no key behind it."""
        missing: list[str] = []
        if self.sources.get("adzuna", SourceConfig()).enabled and not (
            self.adzuna_app_id and self.adzuna_app_key
        ):
            missing.append("ADZUNA_APP_ID / ADZUNA_APP_KEY")
        if self.sources.get("reed", SourceConfig()).enabled and not self.reed_api_key:
            missing.append("REED_API_KEY")
        if self.notify.backend == "ntfy" and not self.ntfy_topic:
            missing.append("JOBHUNT_NTFY_TOPIC")
        if self.letters.backend == "claude" and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"no config at {path}. Copy config.example.yaml to {path} and edit it."
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    sources = {
        name: SourceConfig(**values)
        for name, values in (raw.get("sources") or {}).items()
    }

    quiet = (raw.get("daemon") or {}).get("quiet_hours")
    daemon_kwargs = dict(raw.get("daemon") or {})
    if quiet is not None:
        daemon_kwargs["quiet_hours"] = tuple(quiet)

    return Config(
        profile_path=raw.get("profile_path", "profile.yaml"),
        database_path=raw.get("database_path", "jobhunt.db"),
        sources=sources,
        scoring=ScoringConfig(**(raw.get("scoring") or {})),
        notify=NotifyConfig(**(raw.get("notify") or {})),
        letters=LettersConfig(**(raw.get("letters") or {})),
        apply=ApplyConfig(**(raw.get("apply") or {})),
        daemon=DaemonConfig(**daemon_kwargs),
    )
