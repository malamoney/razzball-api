"""API-key authentication and per-endpoint authorization.

Clients send their key in the ``Razzball-Api-Key`` header. A key maps to an
``api_user`` row whose access group lists the (uri, method) pairs it may call.
The uri stored in ``api_access_group_endpoint_map`` is the route rule with its
``<variable>`` segments removed, e.g. ``/nfl/projections/weekly`` for
``/nfl/projections/weekly/<season>/<week>``.
"""

import functools
import logging
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass

from flask import g, request
from flask.typing import ResponseReturnValue
from sqlalchemy import Engine, text

from razzball_api.errors import ApiError
from razzball_api.extensions import db

logger = logging.getLogger(__name__)

API_KEY_HEADER = "Razzball-Api-Key"
MAX_API_KEY_LENGTH = 128
ACTIVE_STATUS = "ACTIVE"

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": f'ApiKey header="{API_KEY_HEADER}"'}

_USER_SQL = text(
    "SELECT api_user.id, api_user.access_group_id, api_user_status.status "
    "FROM api_user "
    "JOIN api_user_status ON api_user.status_id = api_user_status.id "
    "WHERE api_user.api_key = :api_key"
)
_ENDPOINTS_SQL = text(
    "SELECT uri, method FROM api_access_group_endpoint_map "
    "WHERE access_group_id = :access_group_id"
)


@dataclass(frozen=True, kw_only=True)
class ApiUser:
    id: int
    status: str
    endpoint_access: Mapping[str, Set[str]]

    def can_access(self, uri: str, method: str) -> bool:
        # HEAD is answered by GET handlers, so GET permission covers it.
        effective = "GET" if method == "HEAD" else method
        return effective in self.endpoint_access.get(uri, frozenset())


def find_api_user(engine: Engine, api_key: str) -> ApiUser | None:
    with engine.connect() as conn:
        row = conn.execute(_USER_SQL, {"api_key": api_key}).one_or_none()
        if row is None:
            return None
        user_id, access_group_id, status = row
        access: dict[str, set[str]] = {}
        for uri, method in conn.execute(
            _ENDPOINTS_SQL, {"access_group_id": access_group_id}
        ):
            access.setdefault(str(uri), set()).add(str(method).upper())
    return ApiUser(
        id=int(user_id),
        status=str(status),
        endpoint_access={uri: frozenset(methods) for uri, methods in access.items()},
    )


def access_key_for_rule(rule: str) -> str:
    """Strip ``<converter:name>`` segments from a route rule."""
    return "/".join(
        part
        for part in rule.split("/")
        if not (part.startswith("<") and part.endswith(">"))
    )


def authenticate(
    engine: Engine, api_key: str | None, *, rule: str, method: str
) -> ApiUser:
    """Return the user allowed to call ``method`` on ``rule`` or raise ApiError."""
    if not api_key:
        raise ApiError(
            401,
            "Unauthorized",
            f"The {API_KEY_HEADER} header is required.",
            headers=_UNAUTHORIZED_HEADERS,
        )
    user = (
        find_api_user(engine, api_key) if len(api_key) <= MAX_API_KEY_LENGTH else None
    )
    if user is None:
        raise ApiError(
            401, "Unauthorized", "Invalid API key.", headers=_UNAUTHORIZED_HEADERS
        )
    if user.status != ACTIVE_STATUS:
        raise ApiError(403, "Forbidden", "This API key is not active.")
    if not user.can_access(access_key_for_rule(rule), method):
        raise ApiError(403, "Forbidden", "This API key cannot access this endpoint.")
    return user


def api_key_required[**P](
    view: Callable[P, ResponseReturnValue],
) -> Callable[P, ResponseReturnValue]:
    @functools.wraps(view)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> ResponseReturnValue:
        rule = request.url_rule.rule if request.url_rule is not None else request.path
        user = authenticate(
            db.engine,
            request.headers.get(API_KEY_HEADER),
            rule=rule,
            method=request.method,
        )
        g.api_user_id = user.id
        return view(*args, **kwargs)

    return wrapper
