"""Settings loaded and validated from ``RAZZBALL_*`` environment variables.

Precedence: explicit ``Settings`` passed to ``create_app`` > environment variables >
defaults defined here. Nothing reads a ``.env`` file automatically; see README.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_PREFIX = "RAZZBALL_"


class ConfigError(Exception):
    """Raised at startup when configuration is missing or invalid."""


@dataclass(frozen=True, kw_only=True)
class Settings:
    # SQLAlchemy URLs, e.g. mysql+pymysql://user:pass@host/db?ssl_ca=/path/ca.pem.
    # The baseball database also holds the api_user and api_request_log tables.
    baseball_database_url: str
    basketball_database_url: str
    football_database_url: str
    trusted_hosts: tuple[str, ...] = ()
    # Number of reverse proxies in front of the app that set X-Forwarded-For and
    # X-Forwarded-Proto. 0 means forwarded headers are ignored entirely.
    proxy_count: int = 0
    # Timezone used to decide what "today" means for daily projections.
    timezone: str = "America/New_York"
    log_level: str = "INFO"
    log_requests_to_database: bool = True
    # Upper bound on rows returned by one projections request. Exceeding it is an
    # error rather than a silent truncation.
    max_rows: int = 20_000
    extra_flask_config: Mapping[str, object] = field(default_factory=dict[str, object])

    def __post_init__(self) -> None:
        for name in (
            "baseball_database_url",
            "basketball_database_url",
            "football_database_url",
        ):
            if not getattr(self, name):
                raise ConfigError(f"{ENV_PREFIX}{name.upper()} is required")
        if self.proxy_count < 0:
            raise ConfigError(f"{ENV_PREFIX}PROXY_COUNT must be >= 0")
        if self.max_rows < 1:
            raise ConfigError(f"{ENV_PREFIX}MAX_ROWS must be >= 1")
        if self.log_level.upper() not in logging.getLevelNamesMapping():
            raise ConfigError(f"{ENV_PREFIX}LOG_LEVEL is not a valid log level")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"{ENV_PREFIX}TIMEZONE is not a known timezone") from exc

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        def get(name: str) -> str | None:
            value = env.get(ENV_PREFIX + name)
            return value.strip() if value is not None and value.strip() else None

        defaults = cls.__dataclass_fields__
        return cls(
            baseball_database_url=get("BASEBALL_DATABASE_URL") or "",
            basketball_database_url=get("BASKETBALL_DATABASE_URL") or "",
            football_database_url=get("FOOTBALL_DATABASE_URL") or "",
            trusted_hosts=_parse_list(get("TRUSTED_HOSTS")),
            proxy_count=_parse_int("PROXY_COUNT", get("PROXY_COUNT"), 0),
            timezone=get("TIMEZONE") or str(defaults["timezone"].default),
            log_level=(get("LOG_LEVEL") or "INFO").upper(),
            log_requests_to_database=_parse_bool(
                "LOG_REQUESTS_TO_DATABASE", get("LOG_REQUESTS_TO_DATABASE"), True
            ),
            max_rows=_parse_int("MAX_ROWS", get("MAX_ROWS"), 20_000),
        )

    def to_flask_config(self) -> dict[str, object]:
        config: dict[str, object] = {
            "SQLALCHEMY_DATABASE_URI": self.baseball_database_url,
            "SQLALCHEMY_BINDS": {
                "basketball": self.basketball_database_url,
                "football": self.football_database_url,
            },
            # Validate pooled connections and recycle them before MySQL's
            # wait_timeout closes them server-side.
            "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "pool_recycle": 280},
            # The API accepts no request bodies; anything larger is refused early.
            "MAX_CONTENT_LENGTH": 16 * 1024,
            "TRUSTED_HOSTS": list(self.trusted_hosts) or None,
        }
        config.update(self.extra_flask_config)
        return config


def _parse_list(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_int(name: str, raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} must be an integer") from exc


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    match raw.lower():
        case "1" | "true" | "yes" | "on":
            return True
        case "0" | "false" | "no" | "off":
            return False
        case _:
            raise ConfigError(f"{ENV_PREFIX}{name} must be true or false")
