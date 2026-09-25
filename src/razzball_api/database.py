"""Opening database connections, and telling "can't connect" apart from other errors.

Connection failures and query failures raise the same SQLAlchemy exception types
(e.g. OperationalError), so they are told apart by *when* they happen: anything
raised while opening the connection means the database is unavailable.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

BASEBALL = "baseball"
BASKETBALL = "basketball"
FOOTBALL = "football"

# MySQL errors that mean the server rejected this client's configuration
# (1045 access denied, 1130 host not allowed). Retrying will not help.
_NON_RETRYABLE_MYSQL_ERRORS = frozenset({1045, 1130})


class DatabaseUnavailableError(Exception):
    def __init__(self, database: str, *, error_code: int | None) -> None:
        super().__init__(f"{database} database unavailable")
        self.database = database
        self.error_code = error_code

    @property
    def retryable(self) -> bool:
        return self.error_code not in _NON_RETRYABLE_MYSQL_ERRORS


@contextmanager
def connect(engine: Engine, database: str) -> Generator[Connection]:
    """Open a connection, raising DatabaseUnavailableError if that fails."""
    try:
        conn = engine.connect()
    except DBAPIError as exc:
        raise DatabaseUnavailableError(
            database, error_code=_driver_error_code(exc)
        ) from exc
    with conn:
        yield conn


def _driver_error_code(exc: DBAPIError) -> int | None:
    # DB-API drivers such as PyMySQL put the numeric server error code first.
    args: tuple[object, ...] = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None
