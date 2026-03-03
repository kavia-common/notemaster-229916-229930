from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row

from src.db.config import get_db_config


@contextmanager
def _get_conn() -> Iterator[psycopg.Connection]:
    """Context manager for a short-lived DB connection."""
    cfg = get_db_config()
    conn = psycopg.connect(cfg.dsn, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def fetch_one(query: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """Run a SELECT that returns at most one row."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchone()


def fetch_all(query: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Run a SELECT that returns all rows."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return list(cur.fetchall())


def execute(
    query: str, params: Optional[Sequence[Any]] = None, returning: bool = False
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    Execute a statement (INSERT/UPDATE/DELETE).

    Returns (rowcount, returned_row if returning=True).
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            row = cur.fetchone() if returning else None
        conn.commit()
        return (cur.rowcount, row)
