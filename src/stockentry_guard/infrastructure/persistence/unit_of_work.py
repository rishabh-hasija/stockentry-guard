"""One short transaction for product mutations and their associated events."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from stockentry_guard.domain.errors import TransactionAbortedError
from stockentry_guard.infrastructure.persistence.repositories import (
    SQLAlchemyEventRepository,
    SQLAlchemyProductRepository,
)


class SQLAlchemyUnitOfWork:
    """Single-use context: explicit commit, otherwise rollback, always close."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._used = False
        self._finished = False

    def _active_session(self) -> Session:
        if self._session is None or self._finished:
            raise RuntimeError("Unit of work has no active transaction")
        return self._session

    @property
    def products(self) -> SQLAlchemyProductRepository:
        return SQLAlchemyProductRepository(self._active_session())

    @property
    def events(self) -> SQLAlchemyEventRepository:
        return SQLAlchemyEventRepository(self._active_session())

    def __enter__(self) -> Self:
        if self._used:
            raise RuntimeError("Create a new unit of work for each transaction")
        self._used = True
        self._session = self._session_factory()
        self._session.begin()
        return self

    def commit(self) -> None:
        session = self._active_session()
        try:
            if session.info.get("write_failed"):
                raise TransactionAbortedError("A failed write aborted this unit of work")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self._finished = True

    def rollback(self) -> None:
        self._active_session().rollback()
        self._finished = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            try:
                self._session.rollback()
            finally:
                self._session.close()
                self._session = None
                self._finished = True
