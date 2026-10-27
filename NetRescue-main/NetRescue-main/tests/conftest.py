"""tests/conftest.py — Shared pytest fixtures (SQLite in-memory, no PostgreSQL needed)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netrescue.core.models import Base


@pytest.fixture(scope="function")
def engine():
    """Create a fresh in-memory SQLite engine per test function."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture(scope="function")
def db_session(engine):
    """Provide a transactional SQLAlchemy session, rolled back after each test."""
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
