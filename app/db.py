"""CockroachDB connection pool and transaction helper.

Every mutating operation in this project runs inside `tx()`. That is the whole
point of the design: screening, embedding, the memory write and the audit row
either all land or none do, so a poisoned memory never has a visible
intermediate state.
"""

import functools
import pathlib
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None

# The CockroachDB Cloud CA chain, committed deliberately. It is a public
# certificate chain, not a secret.
CA_BUNDLE = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "cockroach-ca.crt"


def dsn() -> str:
    """The configured DSN, with a CA bundle attached when one is needed.

    libpq does NOT fall back to the operating system's trust store. Under
    `sslmode=verify-full` it looks for `~/.postgresql/root.crt` and fails the
    connection outright if that file is missing — which is every fresh
    container, however standard the issuing CA happens to be. (The chain here
    is rooted at ISRG Root X1/X2, and it still fails, because libpq never
    consults the store those roots live in.)

    `sslrootcert=system` would express this directly, but it needs libpq 16+
    and psycopg's bundled binary is currently libpq 14 — so pointing at a
    committed copy of the chain is the portable answer. An explicit
    `sslrootcert` in the DSN always wins.
    """
    info = conninfo_to_dict(config.COCKROACH_DSN)
    verifying = info.get("sslmode") in ("verify-ca", "verify-full")
    if verifying and not info.get("sslrootcert") and CA_BUNDLE.is_file():
        info["sslrootcert"] = str(CA_BUNDLE)
    return make_conninfo(**info)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            dsn(),
            min_size=1,
            max_size=8,
            kwargs={"autocommit": True},
            open=True,
        )
    return _pool


def close() -> None:
    """Shut the pool down cleanly.

    One-shot scripts call this before exiting. Without it psycopg_pool prints
    a "couldn't stop thread ... within 5.0 seconds" warning per worker at
    interpreter teardown — harmless, but it lands in the terminal output the
    demo video records.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx():
    """Yield a cursor inside an explicit transaction. Single attempt.

    A context manager cannot re-run its caller's body, so retry does not belong
    here — see `retry_on_serialization` for that. Keeping the two separate is
    what makes the retry actually work: an earlier version wrapped this
    generator in a retry loop, which yields twice and raises `generator didn't
    stop after throw()` instead of retrying.
    """
    with pool().connection() as conn:
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def retry_on_serialization(attempts: int = 5):
    """Re-run a whole transactional function on SQLSTATE 40001.

    CockroachDB uses serializable isolation, so a contended transaction can be
    aborted and must be retried from the beginning by the client — that is the
    documented contract, and it is why the retry wraps the entire function
    rather than a cursor.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except psycopg.errors.SerializationFailure:
                    if attempt == attempts:
                        raise
        return wrapper

    return decorate


@contextmanager
def read():
    """Yield a read-only, autocommit cursor."""
    with pool().connection() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            yield cur


_AS_OF_ALLOWED = set("0123456789-.:+ smhdTZ'")


def as_of_clause(as_of: str | None) -> str:
    """Render a CockroachDB AS OF SYSTEM TIME clause.

    The clause is statement-scoped, not table-scoped: it goes ONCE after the
    whole FROM clause (all joins included) and before WHERE. Repeating it per
    table reference is a syntax error.

    CockroachDB does not accept a placeholder here, so the value is
    interpolated — which means it must be validated. Accepts relative offsets
    ('-30s', '-5m') and ISO timestamps ('2026-08-18 09:30:00'); anything else
    raises rather than reaching the planner.
    """
    if not as_of:
        return ""
    if len(as_of) > 40 or not set(as_of) <= _AS_OF_ALLOWED:
        raise ValueError(f"unsupported AS OF SYSTEM TIME value: {as_of!r}")
    return f" AS OF SYSTEM TIME '{as_of}'"


def to_vector(values: list[float]) -> str:
    """Render a Python float list as a CockroachDB VECTOR literal."""
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"
