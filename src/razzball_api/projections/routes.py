import datetime
import logging
from collections.abc import Sequence

from flask import Blueprint, Response, current_app, jsonify
from flask.typing import ResponseReturnValue
from sqlalchemy import Engine

from razzball_api.auth import api_key_required
from razzball_api.config import Settings
from razzball_api.errors import ApiError, error_response
from razzball_api.extensions import db
from razzball_api.projections import queries
from razzball_api.projections.queries import MlbBotView, ProjectionRow

logger = logging.getLogger(__name__)

bp = Blueprint("projections", __name__)

MIN_NFL_SEASON = 1990
MAX_NFL_WEEK = 22


def _settings() -> Settings:
    settings = current_app.extensions["razzball_settings"]
    if not isinstance(settings, Settings):  # pragma: no cover - setup invariant
        raise RuntimeError("create_app() did not register settings")
    return settings


def today_in(zone: datetime.tzinfo) -> datetime.date:
    return datetime.datetime.now(zone).date()


def _parse_date(raw: str | None) -> datetime.date:
    if raw is None:
        return today_in(_settings().zone)
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise ApiError(
            400,
            "Bad Request",
            "Invalid date.",
            details={"date": "Expected a date in YYYY-MM-DD format."},
        ) from None


def _parse_bounded_int(name: str, raw: str, *, low: int, high: int) -> int:
    if raw.isascii() and raw.isdigit():
        value = int(raw)
        if low <= value <= high:
            return value
    raise ApiError(
        400,
        "Bad Request",
        f"Invalid {name}.",
        details={name: f"Expected an integer between {low} and {high}."},
    )


def _rows_response(key: str, rows: Sequence[ProjectionRow]) -> Response:
    return jsonify({key: rows})


def _max_rows() -> int:
    return _settings().max_rows


def _engine(bind: str | None) -> Engine:
    return db.engines[bind]


@bp.errorhandler(queries.ResultTooLargeError)
def handle_result_too_large(error: queries.ResultTooLargeError) -> ResponseReturnValue:
    logger.error("Projection query exceeded RAZZBALL_MAX_ROWS=%d", error.max_rows)
    return error_response(
        500, "Internal Server Error", "The result set is too large to return."
    )


# --- MLB ---------------------------------------------------------------------


@bp.get("/mlb/projections/daily")
@bp.get("/mlb/projections/daily/<date>")
@api_key_required
def mlb_daily(date: str | None = None) -> ResponseReturnValue:
    day = _parse_date(date)
    return _rows_response(
        "data", queries.mlb_daily(_engine(None), day, max_rows=_max_rows())
    )


def _mlb_bot(view: MlbBotView) -> ResponseReturnValue:
    return _rows_response(
        "data", queries.mlb_bot(_engine(None), view, max_rows=_max_rows())
    )


@bp.get("/mlb/projections/botdaily")
@api_key_required
def mlb_bot_daily() -> ResponseReturnValue:
    return _mlb_bot(MlbBotView.DAILY)


@bp.get("/mlb/projections/botweekly")
@api_key_required
def mlb_bot_weekly() -> ResponseReturnValue:
    return _mlb_bot(MlbBotView.WEEKLY)


@bp.get("/mlb/projections/botros")
@api_key_required
def mlb_bot_ros() -> ResponseReturnValue:
    return _mlb_bot(MlbBotView.ROS)


# --- NBA ---------------------------------------------------------------------


@bp.get("/nba/projections/daily")
@bp.get("/nba/projections/daily/<date>")
@api_key_required
def nba_daily(date: str | None = None) -> ResponseReturnValue:
    day = _parse_date(date)
    return _rows_response(
        "projections",
        queries.nba_daily(_engine("basketball"), day, max_rows=_max_rows()),
    )


@bp.get("/nba/projections/all")
@api_key_required
def nba_all() -> ResponseReturnValue:
    return _rows_response(
        "projections", queries.nba_all(_engine("basketball"), max_rows=_max_rows())
    )


@bp.get("/nba/projections/ros")
@api_key_required
def nba_rest_of_season() -> ResponseReturnValue:
    return _rows_response(
        "projections",
        queries.nba_rest_of_season(_engine("basketball"), max_rows=_max_rows()),
    )


# --- NFL ---------------------------------------------------------------------


@bp.get("/nfl/projections/weekly/<season>/<week>")
@api_key_required
def nfl_weekly(season: str, week: str) -> ResponseReturnValue:
    this_year = today_in(_settings().zone).year
    season_value = _parse_bounded_int(
        "season", season, low=MIN_NFL_SEASON, high=this_year + 1
    )
    week_value = _parse_bounded_int("week", week, low=1, high=MAX_NFL_WEEK)
    return _rows_response(
        "projections",
        queries.nfl_weekly(
            _engine("football"), season_value, week_value, max_rows=_max_rows()
        ),
    )
