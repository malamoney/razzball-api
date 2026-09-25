import pytest
from flask import Flask
from flask.testing import FlaskClient

from razzball_api.auth import ApiUser, access_key_for_rule
from tests.conftest import (
    FULL_KEY,
    INACTIVE_KEY,
    MLB_ONLY_KEY,
    VENDOR_ACCEPT,
    auth_headers,
    request_log_rows,
)

MLB_DAILY = "/mlb/projections/daily/2026-06-01"


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("/nfl/projections/weekly/<season>/<week>", "/nfl/projections/weekly"),
        ("/mlb/projections/daily/<date>", "/mlb/projections/daily"),
        ("/mlb/projections/daily", "/mlb/projections/daily"),
        ("/x/<int:id>/y", "/x/y"),
    ],
)
def test_access_key_strips_route_variables(rule: str, expected: str) -> None:
    assert access_key_for_rule(rule) == expected


def test_head_is_allowed_by_get_permission() -> None:
    user = ApiUser(id=1, status="ACTIVE", endpoint_access={"/a": frozenset({"GET"})})
    assert user.can_access("/a", "HEAD")
    assert not user.can_access("/a", "POST")
    assert not user.can_access("/b", "GET")


def test_missing_api_key_is_401_and_audited(app: Flask, client: FlaskClient) -> None:
    response = client.get(MLB_DAILY, headers=auth_headers(None))

    assert response.status_code == 401
    assert "ApiKey" in response.headers["WWW-Authenticate"]
    body = response.get_json()
    assert body["error"] is True
    assert body["status_code"] == 401
    [row] = request_log_rows(app)
    assert row["api_user_id"] == 0
    assert row["status_code"] == 401
    assert row["api_key"] == ""


@pytest.mark.parametrize("key", ["not-a-real-key", "x" * 500])
def test_unknown_or_oversized_key_is_401(client: FlaskClient, key: str) -> None:
    response = client.get(MLB_DAILY, headers=auth_headers(key))
    assert response.status_code == 401


def test_inactive_key_is_403(client: FlaskClient) -> None:
    response = client.get(MLB_DAILY, headers=auth_headers(INACTIVE_KEY))
    assert response.status_code == 403
    assert "not active" in response.get_json()["description"]


def test_key_is_limited_to_its_access_group(client: FlaskClient) -> None:
    allowed = client.get(MLB_DAILY, headers=auth_headers(MLB_ONLY_KEY))
    other_mlb = client.get(
        "/mlb/projections/botros", headers=auth_headers(MLB_ONLY_KEY)
    )
    other_sport = client.get(
        "/nfl/projections/weekly/2025/3", headers=auth_headers(MLB_ONLY_KEY)
    )

    assert allowed.status_code == 200
    assert other_mlb.status_code == 403
    assert other_sport.status_code == 403


def test_valid_key_is_audited_with_fingerprint_not_raw_key(
    app: Flask, client: FlaskClient
) -> None:
    client.get(MLB_DAILY, headers=auth_headers(FULL_KEY))

    [row] = request_log_rows(app)
    assert row["api_user_id"] == 11
    assert row["status_code"] == 200
    assert row["description"] == "200 OK"
    assert row["uri"] == MLB_DAILY
    assert row["api_key"] != FULL_KEY
    assert FULL_KEY not in str(row)
    assert len(str(row["api_key"])) == 16


def test_head_request_with_get_permission(client: FlaskClient) -> None:
    response = client.head(MLB_DAILY, headers=auth_headers())
    assert response.status_code == 200


def test_only_the_api_key_header_authenticates(client: FlaskClient) -> None:
    response = client.get(
        MLB_DAILY,
        headers={
            "Accept": VENDOR_ACCEPT,
            "Authorization": "Bearer something",
            "X-Secret-Key": "something",
        },
    )
    assert response.status_code == 401


def test_api_exposes_only_read_only_methods(app: Flask) -> None:
    for rule in app.url_map.iter_rules():
        assert (rule.methods or set()) <= {"GET", "HEAD", "OPTIONS"}, rule.rule
