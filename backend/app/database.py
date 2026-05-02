import os
from pathlib import Path
from sqlmodel import Session, create_engine, SQLModel

from app.config import settings

# Resolve SQLite path relative to project root (ELS/)
PROJECT_ROOT = Path(__file__).parent.parent.parent
db_url = settings.database_url
if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:////"):
    # Relative path — resolve relative to project root
    rel_path = db_url.replace("sqlite:///", "", 1)
    abs_path = PROJECT_ROOT / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{abs_path}"

engine = create_engine(
    db_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables():
    # Import models before create_all so every SQLModel table is registered.
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns():
    if not db_url.startswith("sqlite:///"):
        return

    expected_columns = {
        "news_articles": {
            "image_url": "VARCHAR",
        },
        "interrogation_runs": {
            "case_code": "VARCHAR DEFAULT ''",
            "operation_name": "VARCHAR DEFAULT ''",
            "recording_path": "VARCHAR",
            "rounds_completed": "INTEGER DEFAULT 0",
            "status": "VARCHAR DEFAULT 'pending'",
            "completed_at": "VARCHAR",
        },
        "game_profiles": {
            "credits": "INTEGER DEFAULT 180",
            "streak": "INTEGER DEFAULT 0",
            "upgrades_json": "VARCHAR DEFAULT '{}'",
            "stats_json": "VARCHAR DEFAULT '{}'",
            "created_at": "VARCHAR",
            "updated_at": "VARCHAR",
        },
    }

    with engine.begin() as conn:
        for table_name, columns_map in expected_columns.items():
            tables = conn.exec_driver_sql(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
            ).fetchall()
            if not tables:
                continue
            existing_columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()}
            for column_name, column_sql in columns_map.items():
                if column_name in existing_columns:
                    continue
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def get_session():
    with Session(engine) as session:
        yield session
