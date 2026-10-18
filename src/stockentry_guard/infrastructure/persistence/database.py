"""Engine creation only: production schemas are managed explicitly by Alembic."""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if (
        url.get_backend_name() == "sqlite"
        and url.database is not None
        and url.database not in ("", ":memory:")
    ):
        if url.query.get("uri") == "true":
            raise ValueError("SQLite URI paths are not supported; use a normal file URL")
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url)
