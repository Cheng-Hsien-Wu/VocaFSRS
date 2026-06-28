import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from app.config import settings

def get_sqlite_db_path(database_url: str | None = None) -> str:
    if settings.database_path:
        return os.path.abspath(settings.database_path)

    db_url = database_url if database_url is not None else settings.database_url
    if db_url:
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if db_url.startswith(prefix):
                return os.path.abspath(db_url.replace(prefix, ""))
        raise RuntimeError("Only SQLite DATABASE_URL values are supported.")

    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vocab.db")


def get_database_url() -> str:
    db_path = get_sqlite_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


DATABASE_URL = get_database_url()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 10}
)

def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=FULL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=10000;")
    cursor.close()

from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def on_connect(dbapi_connection, connection_record):
    set_sqlite_pragma(dbapi_connection, connection_record)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
