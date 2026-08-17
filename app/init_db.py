"""Apply schema.sql to the configured CockroachDB cluster."""

import pathlib

from . import db

SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"


def main() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    try:
        with db.pool().connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql)
        print(f"applied {SCHEMA.name} to CockroachDB")
    finally:
        db.close()


if __name__ == "__main__":
    main()
