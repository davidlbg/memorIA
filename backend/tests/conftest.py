import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

# This token is exclusively a deterministic fixture, never an application secret.
os.environ["AUTH_SECRET"] = "test-only-token-000000000000000000000000000"
os.environ["CONNECTOR_SECRET"] = "connector-test-only-000000000000000000000000"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ["EXTERNAL_AI_ENABLED"] = "false"
os.environ["EMBEDDING_DIMENSIONS"] = "256"
TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
if TEST_URL:
    parsed = make_url(TEST_URL)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (parsed.database or "").endswith(
        "_test"
    ):
        raise RuntimeError("Tests require a loopback PostgreSQL database ending in _test")
    os.environ["DATABASE_URL"] = TEST_URL


@pytest.fixture(scope="session")
def factory() -> sessionmaker[Session]:
    if not TEST_URL:
        pytest.skip("Set TEST_DATABASE_URL to an explicitly disposable local PostgreSQL database")
    from app.config import get_settings
    from app.database.session import engine

    get_settings.cache_clear()
    engine.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")
    return sessionmaker(create_engine(TEST_URL), expire_on_commit=False)


@pytest.fixture
def db(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with factory.begin() as session:
        session.execute(
            text(
                "TRUNCATE ai_usage, ai_budget_periods, ai_configuration, conversations, runtime_settings, whatsapp_chats, whatsapp_connections CASCADE"
            )
        )
    return factory
