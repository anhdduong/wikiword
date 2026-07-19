"""SQLite connection + minimal migration runner.

Migrations are numbered .sql files in app/migrations/, applied in filename
order, tracked in schema_migrations. Each migration runs in its own
transaction (SQLite DDL is transactional).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "wikiword.db"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; return the names of those applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    ran = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        with conn:
            conn.executescript(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.name,))
        ran.append(path.name)
    return ran


if __name__ == "__main__":
    conn = connect()
    ran = migrate(conn)
    if ran:
        print(f"Applied: {', '.join(ran)}")
    else:
        print("Up to date.")
