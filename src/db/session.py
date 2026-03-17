"""Database session management with proper connection pooling."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    # Connection pooling — critical for Railway/cloud deploys where
    # connection limits are tight (typically 20–50).
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_session() -> Session:
    """Get a new database session from the connection pool."""
    return SessionLocal()
