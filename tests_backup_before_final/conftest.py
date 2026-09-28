from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from src.DB.database import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as db:
        yield db
        await db.rollback()

    await engine.dispose()


@pytest.fixture
def make_sample_raw_df() -> pd.DataFrame:
    rows = []
    purchase_number = 1
    reference_date = datetime(2026, 9, 28)

    for client_number in range(1, 11):
        phone = f"+799900000{client_number:02d}"
        frequency = client_number
        last_date = reference_date - timedelta(days=client_number)

        for purchase_index in range(frequency):
            purchase_date = last_date - timedelta(
                days=2 * purchase_index
            )

            rows.append(
                {
                    "purchase_id": str(purchase_number),
                    "phone": phone,
                    "purchase_date": purchase_date.strftime(
                        "%Y-%m-%d"
                    ),
                    "amount": float(client_number * 100),
                }
            )

            purchase_number += 1

    return pd.DataFrame(rows)


@pytest.fixture
def make_sample_history_df(make_sample_raw_df) -> pd.DataFrame:
    df = make_sample_raw_df.copy()
    df["purchase_date"] = pd.to_datetime(
        df["purchase_date"]
    )
    return df
