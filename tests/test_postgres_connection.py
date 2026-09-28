import pytest
from sqlalchemy import text

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.DB.database import DATABASE_URL


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)


@pytest.mark.asyncio
async def test_postgres_connection():
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_postgres_tables():
    expected_tables = {
        "client",
        "purchase",
        "campaign",
        "campaign_category",
        "campaign_target",
    }

    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
        )

        actual_tables = {
            row[0]
            for row in result
        }

    assert expected_tables.issubset(actual_tables)