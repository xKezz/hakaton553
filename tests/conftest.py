import os
import sys

import pandas as pd
import numpy as np
import pytest
import pytest_asyncio

from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import StaticPool


project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))


from src.DB.database import Base


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture
async def db_session():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def generate_csv():
    def _generate(
        rows: int,
        filename: str = "test_data.csv",
        corrupt: bool = False,
    ) -> str:

        file_path = f"tests/fixtures/{filename}"

        os.makedirs(
            os.path.dirname(file_path),
            exist_ok=True,
        )

        if corrupt:
            df = pd.DataFrame({
                "wrong_col": [1, 2],
                "bad_date": ["a", "b"],
            })

        else:
            end_date = datetime(2026, 6, 22)

            dates = [
                end_date
                - timedelta(
                    days=np.random.randint(10, 200)
                )
                for _ in range(rows)
            ]

            amounts = np.random.uniform(
                100.0,
                5000.0,
                rows,
            ).round(2)

            phones = [
                f"+7999{np.random.randint(1000000, 9999999)}"
                for _ in range(rows)
            ]

            purchase_ids = [
                f"purchase_{i}"
                for i in range(rows)
            ]

            df = pd.DataFrame({
                "purchase_id": purchase_ids,
                "phone": phones,
                "purchase_date": [
                    date.strftime("%Y-%m-%d")
                    for date in dates
                ],
                "amount": amounts,
            })

            df["phone"] = df["phone"].astype(str)
            df["purchase_id"] = df["purchase_id"].astype(str)

        df.to_csv(
            file_path,
            index=False,
            encoding="utf-8",
        )

        return file_path

    return _generate