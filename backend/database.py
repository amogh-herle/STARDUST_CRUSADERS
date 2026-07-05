"""
Database Setup — SQLAlchemy 2.0 async engine

Uses asyncpg driver for PostgreSQL.
All route handlers get an AsyncSession via the get_db dependency.

Connection pooling:
  pool_size=10, max_overflow=20 — handles concurrent investigator sessions
  pool_pre_ping=True            — drops stale connections silently
"""

import socket
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from config import settings


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
db_url = settings.DATABASE_URL
if "postgresql" in db_url:
    if "localhost" in db_url or "127.0.0.1" in db_url:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", 5432))
            s.close()
        except Exception:
            import os
            backend_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(backend_dir, "cidecode.db")
            db_url = f"sqlite+aiosqlite:///{db_path}"
            print(f"[WARNING] PostgreSQL not running on localhost:5432. Falling back to SQLite: {db_path}")

engine_kwargs = {
    "pool_pre_ping": True,
    "echo": settings.DEBUG,
}
if "sqlite" not in db_url:
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20

engine = create_async_engine(db_url, **engine_kwargs)

if "sqlite" in db_url:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,    # avoids lazy-load errors after commit
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Declarative base — all models inherit from this
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Utility: create all tables (used at startup in dev/demo mode)
# ---------------------------------------------------------------------------
async def create_tables():
    """Drop-and-recreate all tables. Only call in dev/demo mode."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
