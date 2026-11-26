from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, text

REQUIRED_TABLES = frozenset({"policies", "ledger_entries", "outbox_events", "idempotency_records"})


class Database:
    """SQLAlchemy 2.0 database wrapper supporting SQLite (default) and PostgreSQL."""

    def __init__(self, database_url: str, migrations_dir: Path) -> None:
        self.database_url = database_url
        self.migrations_dir = migrations_dir

        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:///") and database_url != "sqlite:///:memory:":
            db_path = Path(database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connect_args["check_same_thread"] = False
        elif database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self.engine: Engine = create_engine(
            database_url,
            connect_args=connect_args,
            future=True,
        )

        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    def _dialect_migrations_dir(self) -> Path:
        dialect = self.engine.dialect.name
        folder_name = "postgres" if dialect == "postgresql" else dialect
        sql_dir = self.migrations_dir / folder_name
        if not sql_dir.exists():
            raise RuntimeError(f"No migrations directory found for dialect '{dialect}': {sql_dir}")
        return sql_dir

    def run_migrations(self) -> list[str]:
        """Apply dialect-specific numbered SQL migrations idempotently."""
        sql_dir = self._dialect_migrations_dir()

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
            )
            applied = {
                row[0]
                for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
            }

        newly_applied: list[str] = []
        for sql_file in sorted(sql_dir.glob("*.sql")):
            version = sql_file.name
            if version in applied:
                continue

            sql = sql_file.read_text(encoding="utf-8")
            with self.engine.begin() as conn:
                if self.engine.dialect.name == "sqlite":
                    dbapi_conn = conn.connection.dbapi_connection
                    if dbapi_conn is None:
                        raise RuntimeError("Missing DBAPI connection for SQLite migration")
                    dbapi_conn.executescript(sql)
                else:
                    conn.exec_driver_sql(sql)

                conn.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {"version": version, "applied_at": datetime.now(UTC).isoformat()},
                )
            newly_applied.append(version)

        return newly_applied

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    def check_readiness(self) -> tuple[bool, list[str]]:
        """Verify DB connectivity and presence of all required domain tables."""
        self.ping()
        existing = set(inspect(self.engine).get_table_names())
        missing = sorted(REQUIRED_TABLES - existing)
        return len(missing) == 0, missing

    def dispose(self) -> None:
        self.engine.dispose()
