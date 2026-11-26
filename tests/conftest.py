from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra.database import Database
from app.main import create_app


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "test_annuity.db"
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{db_file}",
        llm_api_key="",
        explain_rate_limit_per_minute=20,
    )


@pytest.fixture()
def db(test_settings: Settings) -> Iterator[Database]:
    database = Database(
        database_url=test_settings.database_url,
        migrations_dir=test_settings.migrations_dir,
    )
    database.run_migrations()
    yield database
    database.dispose()


@pytest.fixture()
def client(test_settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=test_settings)
    with TestClient(app) as test_client:
        yield test_client
