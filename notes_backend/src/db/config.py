import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DbConfig:
    """Database configuration resolved from db_connection.txt and/or environment."""

    dsn: str


def _parse_db_connection_txt(contents: str) -> str:
    """
    Parse db_connection.txt contents.

    notes_database writes lines like:
      psql postgresql://user:pass@host:port/dbname

    We extract the URL part.
    """
    text = contents.strip()
    if not text:
        raise ValueError("db_connection.txt is empty")

    # Common contract: starts with "psql " followed by URL
    if text.startswith("psql "):
        return text[len("psql ") :].strip()

    # If the file already contains just the URL, accept it.
    if text.startswith("postgresql://") or text.startswith("postgres://"):
        return text

    raise ValueError("Unrecognized db_connection.txt format")


# PUBLIC_INTERFACE
def get_db_config() -> DbConfig:
    """Resolve database DSN from db_connection.txt (preferred) or environment variables."""
    # Prefer a connection file if present (local dev + container contract).
    # Search in current working directory and parent directories (repo/container root scenarios).
    candidates = [
        Path(os.getcwd()) / "db_connection.txt",
        Path(__file__).resolve().parents[3] / "db_connection.txt",  # .../notes_backend/db_connection.txt
        Path(__file__).resolve().parents[4] / "db_connection.txt",  # workspace root (defensive)
    ]

    for p in candidates:
        try:
            if p.is_file():
                dsn = _parse_db_connection_txt(p.read_text(encoding="utf-8"))
                return DbConfig(dsn=dsn)
        except Exception:
            # If file exists but malformed, continue to env fallback.
            pass

    # Env fallback (do not assume variable names beyond common practice).
    # Orchestrator can wire these in .env when needed.
    dsn = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRES_DSN")
        or os.getenv("DB_DSN")
    )
    if not dsn:
        raise RuntimeError(
            "Database connection not configured. Provide db_connection.txt or set DATABASE_URL/POSTGRES_URL."
        )

    # Normalize postgres:// to postgresql:// for drivers that require it.
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://") :]

    return DbConfig(dsn=dsn)
