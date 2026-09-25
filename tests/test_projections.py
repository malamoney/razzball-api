import datetime

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text

from razzball_api.extensions import db
from razzball_api.projections import routes
from tests.conftest import AppFactory, auth_headers


def _fixed_today(zone: datetime.tzinfo) -> datetime.date:
    return datetime.date(2026, 6, 2)


def test_mlb_daily_for_date_stringifies_values(client: FlaskClient) -> None:
    response = client.get("/mlb/projections/daily/2026-06-01", headers=auth_headers())

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_json() == {
        "data": [
            {"Name": "Aaron Judge", "Date": "2026-06-01", "HR": "0.41"},
            # NULL is rendered as "None", preserving the original API's output.
            {"Name": "Juan Soto", "Date": "2026-06-01", "HR": "None"},
        ]
    }


def test_mlb_daily_defaults_to_today(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "today_in", _fixed_today)

    response = client.get("/mlb/projections/daily", headers=auth_headers())

    assert [row["Name"] for row in response.get_json()["data"]] == ["Mookie Betts"]


def test_trailing_slash_is_accepted(client: FlaskClient) -> None:
    response = client.get("/mlb/projections/botdaily/", headers=auth_headers())
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/mlb/projections/botdaily", "daily-player"),
        ("/mlb/projections/botweekly", "weekly-player"),
        ("/mlb/projections/botros", "ros-player"),
    ],
)
def test_mlb_bot_views(client: FlaskClient, path: str, name: str) -> None:
    response = client.get(path, headers=auth_headers())
    assert response.get_json() == {"data": [{"Name": name, "HR": "1.5"}]}


def test_nba_endpoints(client: FlaskClient) -> None:
    daily = client.get("/nba/projections/daily/2026-01-10", headers=auth_headers())
    everything = client.get("/nba/projections/all", headers=auth_headers())
    ros = client.get("/nba/projections/ros", headers=auth_headers())

    assert [r["Name"] for r in daily.get_json()["projections"]] == ["Nikola Jokic"]
    assert len(everything.get_json()["projections"]) == 2
    assert ros.get_json() == {"projections": [{"Name": "Victor W.", "PTS": "24.0"}]}


def test_nfl_weekly(client: FlaskClient) -> None:
    response = client.get("/nfl/projections/weekly/2025/3", headers=auth_headers())
    assert response.get_json() == {
        "projections": [
            {"Name": "Josh Allen", "Season": "2025", "Week": "3", "Pts": "24.1"}
        ]
    }


@pytest.mark.parametrize(
    "path",
    [
        "/mlb/projections/daily/2026-13-01",
        "/mlb/projections/daily/yesterday",
        "/nba/projections/daily/2026-1-1'",
        "/nfl/projections/weekly/abc/3",
        "/nfl/projections/weekly/2025/0",
        "/nfl/projections/weekly/2025/23",
        "/nfl/projections/weekly/1800/3",
        "/nfl/projections/weekly/2025/-1",
    ],
)
def test_invalid_path_values_are_400(client: FlaskClient, path: str) -> None:
    response = client.get(path, headers=auth_headers())

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] is True
    assert body["details"]


def test_result_over_row_limit_is_an_error_not_truncation(
    make_app: AppFactory,
) -> None:
    app = make_app(max_rows=1)

    response = app.test_client().get(
        "/mlb/projections/daily/2026-06-01", headers=auth_headers()
    )

    assert response.status_code == 500
    assert response.get_json()["description"] == (
        "The result set is too large to return."
    )


def test_database_failure_is_500_not_empty_success(app: Flask) -> None:
    # The original API swallowed database errors and returned {"data": []}.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    with app.app_context(), db.engines[None].begin() as conn:
        conn.execute(text("DROP TABLE APISOURCE_MLB"))

    response = app.test_client().get(
        "/mlb/projections/daily/2026-06-01", headers=auth_headers()
    )

    assert response.status_code == 500
    body = response.get_json()
    assert body["message"] == "Internal Server Error"
    assert "APISOURCE_MLB" not in response.get_data(as_text=True)
    assert "sqlite" not in response.get_data(as_text=True).lower()
