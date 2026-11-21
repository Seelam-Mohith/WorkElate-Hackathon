"""Explicit connection and transaction lifetimes for local SQLite storage."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from workelate.storage.schema import SCHEMA
from workelate.storage.migrations import migrate


@contextmanager
def connect_database(path: Path, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    path = Path(path).resolve()
    if read_only:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            # executescript has implicit transaction behavior: use it only here,
            # never during a day's data transaction.
            conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            migrate(conn)
        yield conn
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
