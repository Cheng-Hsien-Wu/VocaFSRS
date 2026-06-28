import asyncio
import os
from app.database import Base, engine, get_sqlite_db_path
# Import models to register them on Base.metadata
import app.models

async def reset_db():
    if os.getenv("VOCAB_ENV") == "production":
        raise RuntimeError("reset_db.py is disabled in production.")

    database_url = os.getenv("DATABASE_URL", "")
    db_target = database_url or get_sqlite_db_path()
    if "test" not in db_target and ":memory:" not in db_target:
        raise RuntimeError("reset_db.py requires a test or in-memory DATABASE_URL.")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Database metadata reset successful (tables recreated).")

if __name__ == "__main__":
    asyncio.run(reset_db())
