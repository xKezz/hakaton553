import os

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from src.DB.crud import (
    get_all_purchases,
    save_purchases,
)
from src.DB.database import Base


@pytest.mark.asyncio
async def test_postgres_crud_roundtrip():
    database_url = os.getenv(
        "TEST_DATABASE_URL"
    )

    if not database_url:
        pytest.skip(
            "TEST_DATABASE_URL не задан. "
            "PostgreSQL integration test пропущен."
        )

    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.drop_all
        )
        await conn.run_sync(
            Base.metadata.create_all
        )

    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as session:
        added = await save_purchases(
            session,
            [
                {
                    "purchase_id":
                        "postgres-test-1",
                    "client_id":
                        "+79990000001",
                    "purchase_date":
                        "2026-09-01",
                    "amount": 123.45,
                }
            ],
        )

        assert added == 1

        purchases = (
            await get_all_purchases(
                session
            )
        )

        assert len(purchases) == 1

        assert (
            purchases[0].purchase_id
            == "postgres-test-1"
        )

        assert (
            purchases[0]
            .client.phone_e164
            == "+79990000001"
        )

    await engine.dispose()
