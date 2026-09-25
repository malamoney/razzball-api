"""Read-only projection queries against the Razzball MySQL databases.

The source tables are maintained outside this service and their columns change
between seasons, so rows are returned with every column, each value converted
with ``str()`` exactly as the original API did (NULL becomes ``"None"``).
Table names come only from the constants below, never from request input.
"""

import datetime
from collections.abc import Mapping
from enum import StrEnum

from sqlalchemy import Engine, TextClause, text

type ProjectionRow = dict[str, str]


class ResultTooLargeError(Exception):
    def __init__(self, max_rows: int) -> None:
        super().__init__(f"Query returned more than {max_rows} rows")
        self.max_rows = max_rows


class MlbBotView(StrEnum):
    DAILY = "botdaily"
    WEEKLY = "botweekly"
    ROS = "botros"


_MLB_BOT_TABLES: Mapping[MlbBotView, str] = {
    MlbBotView.DAILY: "APISOURCE_MLB_DAILY",
    MlbBotView.WEEKLY: "APISOURCE_MLB_WEEKLY",
    MlbBotView.ROS: "APISOURCE_MLB_ROS",
}


def _fetch(
    engine: Engine,
    statement: TextClause,
    params: Mapping[str, object] | None = None,
    *,
    max_rows: int,
) -> list[ProjectionRow]:
    with engine.connect() as conn:
        result = conn.execute(statement, dict(params or {}))
        columns = list(result.keys())
        rows = result.fetchmany(max_rows + 1)
    if len(rows) > max_rows:
        raise ResultTooLargeError(max_rows)
    return [
        {column: str(value) for column, value in zip(columns, row, strict=True)}
        for row in rows
    ]


def mlb_daily(
    engine: Engine, day: datetime.date, *, max_rows: int
) -> list[ProjectionRow]:
    return _fetch(
        engine,
        text("SELECT * FROM APISOURCE_MLB WHERE Date = :day"),
        {"day": day.isoformat()},
        max_rows=max_rows,
    )


def mlb_bot(engine: Engine, view: MlbBotView, *, max_rows: int) -> list[ProjectionRow]:
    return _fetch(
        engine, text(f"SELECT * FROM {_MLB_BOT_TABLES[view]}"), max_rows=max_rows
    )


def nba_daily(
    engine: Engine, day: datetime.date, *, max_rows: int
) -> list[ProjectionRow]:
    return _fetch(
        engine,
        text("SELECT * FROM nba_api_master WHERE Date = :day"),
        {"day": day.isoformat()},
        max_rows=max_rows,
    )


def nba_all(engine: Engine, *, max_rows: int) -> list[ProjectionRow]:
    return _fetch(engine, text("SELECT * FROM nba_api_master"), max_rows=max_rows)


def nba_rest_of_season(engine: Engine, *, max_rows: int) -> list[ProjectionRow]:
    return _fetch(engine, text("SELECT * FROM nba_api_master_ros"), max_rows=max_rows)


def nfl_weekly(
    engine: Engine, season: int, week: int, *, max_rows: int
) -> list[ProjectionRow]:
    return _fetch(
        engine,
        text("SELECT * FROM nfl_api_master WHERE Season = :season AND Week = :week"),
        {"season": season, "week": week},
        max_rows=max_rows,
    )
