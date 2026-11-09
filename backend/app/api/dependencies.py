from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database.session import session_factory


def database() -> Iterator[Session]:
    with session_factory().begin() as session:
        yield session
