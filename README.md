# razzball-api

Read-only REST API that serves Razzball MLB, NBA and NFL projections from the
Razzball MySQL databases to API-key holders.

Version 2 is a rewrite of the original 2019 Flask service
(`gitlab.com/razzball/razzball_api`). The URLs and the JSON body of each response
are unchanged. [Changes from v1](#changes-from-v1) lists what callers will see
differently.

## Requirements

- Python 3.14 (see `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- Network access to the three MySQL databases (baseball, basketball, football)

## Setup

```bash
uv sync
```

## Configuration

All settings come from environment variables prefixed with `RAZZBALL_`. The app
reads them once, when `create_app()` runs. It fails at startup with a
`ConfigError` that names the missing or invalid variable, without printing its
value.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `RAZZBALL_BASEBALL_DATABASE_URL` | yes | – | SQLAlchemy URL for the baseball DB. Also holds the `api_user*`, `api_access_group_endpoint_map` and `api_request_log` tables. |
| `RAZZBALL_BASKETBALL_DATABASE_URL` | yes | – | SQLAlchemy URL for the basketball DB |
| `RAZZBALL_FOOTBALL_DATABASE_URL` | yes | – | SQLAlchemy URL for the football DB |
| `RAZZBALL_TRUSTED_HOSTS` | recommended | *(none)* | Comma-separated `Host` values to accept, e.g. `api.razzball.com`. Other hosts get 400. |
| `RAZZBALL_PROXY_COUNT` | no | `0` | Number of reverse proxies that set `X-Forwarded-For`/`-Proto`. Leave at `0` unless a proxy really sits in front. |
| `RAZZBALL_TIMEZONE` | no | `America/New_York` | Decides what "today" means for the `daily` endpoints called without a date |
| `RAZZBALL_LOG_LEVEL` | no | `INFO` | Python log level name |
| `RAZZBALL_LOG_REQUESTS_TO_DATABASE` | no | `true` | Write one `api_request_log` row per request. Skipped, with one log line, for a request that already found the baseball DB unreachable. |
| `RAZZBALL_MAX_ROWS` | no | `20000` | Most rows one request may return. A larger result is a 500, not a silently truncated list. |
| `RAZZBALL_DB_CONNECT_TIMEOUT` | no | `5` | Seconds to wait for a MySQL server to accept a connection before failing the request. A `connect_timeout` already in a database URL takes precedence. |

Database URLs use the PyMySQL driver, e.g.
`mysql+pymysql://user:password@host/razzball_football`. If the database is
reached over a network, add TLS parameters such as `?ssl_ca=/path/to/ca.pem`.
The API only reads projection tables and inserts into `api_request_log`, so give
it a database account with exactly those privileges.

Precedence: a `Settings` object passed to `create_app()` (used by tests) >
environment variables > the defaults above. Nothing loads `.env` files. For local
work, copy `.env.example` to `.env`, fill it in, and export it yourself
(`set -a; source .env; set +a`). Supply production values through the host's
secret management, never through a committed file.

This service does not own the database schema, so there are no migrations.

## Running locally

```bash
set -a; source .env; set +a
uv run flask --app razzball_api run --debug
```

## Production

Run it under Gunicorn behind the existing Apache (or another TLS-terminating
proxy):

```bash
uv sync --locked --no-dev
RAZZBALL_PROXY_COUNT=1 RAZZBALL_TRUSTED_HOSTS=api.razzball.com \
  uv run --locked gunicorn --bind 127.0.0.1:8000 --workers 4 'razzball_api:create_app()'
```

Apache then proxies to it:

```apache
ProxyPass        / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
RequestHeader set X-Forwarded-Proto "https"
```

- Set `RAZZBALL_PROXY_COUNT` to the number of proxies that append to
  `X-Forwarded-For` (1 for Apache alone). With `0`, the API logs the proxy's
  address. With too high a number, clients can spoof their address.
- Pick the worker count from measurements. Every request is a blocking database
  query, and each worker keeps a small connection pool per database.
- Set HSTS at the TLS terminator once HTTPS is enforced for the host.
- Logs go to stderr, one line per request, with a request ID
  (`X-Request-ID`, also returned in the response). Logs never contain API keys,
  headers, query strings or bodies.
- `GET /health` is a liveness check. It needs no key and does not touch the
  database.

## API

Every projection endpoint requires two headers:

- `Accept: application/vnd.razzball.api` or
  `Accept: application/vnd.razzball-v1+json`
- `Razzball-Api-Key: <key>`

A key is valid when it matches an `api_user` row whose status is `ACTIVE`. That
row's access group must list the endpoint (route with its `<variables>` removed,
e.g. `/nfl/projections/weekly`) and the method (`GET`) in
`api_access_group_endpoint_map`. `HEAD` counts as `GET`.

| Endpoint | Response |
| --- | --- |
| `GET /mlb/projections/daily[/<YYYY-MM-DD>]` | `{"data": [...]}`; date defaults to today in `RAZZBALL_TIMEZONE` |
| `GET /mlb/projections/botdaily` | `{"data": [...]}` |
| `GET /mlb/projections/botweekly` | `{"data": [...]}` |
| `GET /mlb/projections/botros` | `{"data": [...]}` |
| `GET /nba/projections/daily[/<YYYY-MM-DD>]` | `{"projections": [...]}` |
| `GET /nba/projections/all` | `{"projections": [...]}` |
| `GET /nba/projections/ros` | `{"projections": [...]}` |
| `GET /nfl/projections/weekly/<season>/<week>` | `{"projections": [...]}`; season 1990–next year, week 1–22 |
| `GET /health` | `{"status": "ok"}` (no headers required) |

Each row is an object containing every column of the source table, with each
value converted to a string exactly as v1 did (a database `NULL` becomes
`"None"`).

Errors always use this shape:

```json
{"error": true, "status_code": 403, "message": "Forbidden",
 "description": "This API key cannot access this endpoint.",
 "details": {"optional": "field-level validation messages"}}
```

| Status | When |
| --- | --- |
| 400 | Malformed date/season/week, or a `Host` not in `RAZZBALL_TRUSTED_HOSTS` |
| 401 | Missing or unknown API key |
| 403 | Key is inactive or its access group does not include the endpoint |
| 404 / 405 | Unknown URL / wrong method (405 includes `Allow`) |
| 406 | `Accept` does not include a Razzball vendor media type |
| 500 | Database error other than a connection failure (e.g. a failed query), or result over `RAZZBALL_MAX_ROWS`. Internal details are logged, never returned. |
| 503 | A database could not be reached. Includes `Retry-After: 30`, except for MySQL errors 1045 (access denied) and 1130 (host not allowed), which retrying won't fix. The log names the database, never its host or URL. |

## Development

```bash
uv run ruff check --fix .
uv run ruff format .
uv run pyright
uv run pytest            # add --cov=razzball_api for coverage
```

The tests run against in-memory SQLite databases seeded with the tables the API
reads. They need no MySQL server and no credentials.

CI runs the same checks without modifying any files: GitHub Actions
(`.github/workflows/ci.yml`) on every pull request and push to `main`, and
`.gitlab-ci.yml` for GitLab. On GitHub, `main` requires a pull request whose
`check` job passes.

To audit dependencies:

```bash
uv export --no-dev --no-emit-project --format requirements-txt > /tmp/reqs.txt
uvx pip-audit --strict -r /tmp/reqs.txt
```

## Changes from v1

Security fixes:

- Removed unsafe authentication shortcuts and an unauthenticated
  state-changing endpoint. Every projection request now needs a valid, active
  API key with access to that endpoint, and the API exposes only read-only
  methods.
- Database credentials now come only from the environment, never from source.
- Logs no longer contain API keys, request headers or request bodies.
  `api_request_log.api_key` stores a 16-character SHA-256 fingerprint instead of
  the key.
- `X-Forwarded-For` is trusted only for the configured number of proxies.
- Database errors are reported as a `500` with a generic message instead of
  an empty `200` response.
- Added host validation, input validation, a row cap, a 16 KB request body
  limit, and `nosniff`/CSP/`no-store` response headers.

Other changes callers will see:

- A missing `Razzball-Api-Key` is `401` (v1: `400`). An inactive key or a key
  without access to the endpoint is `403` (v1: `401`).
- A missing or wrong `Accept` header is `406` (v1: `400`, or a crash when the
  header was absent). An `Accept` list that includes a vendor type is accepted.
- Invalid dates, seasons and weeks are `400`. v1 passed them to the database and
  returned an empty list.
- The "Hello from ..." text endpoints (`/`, `/mlb/projections/`, etc.) were
  replaced by `GET /health`.
- `api_request_log.api_user_id` is `0` for requests without an authenticated
  user. v1 wrote 403/404/500 into that column as markers.
- `daily` without a date uses `RAZZBALL_TIMEZONE`. v1 used the server's local
  clock.
- Deployment moves from mod_wsgi to Gunicorn behind Apache. Flask-Login and
  Cerberus are no longer used.
