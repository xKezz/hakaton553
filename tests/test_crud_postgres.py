import pytest
from sqlalchemy import select

from src.DB.database import async_session
from src.DB.crud import save_purchases, get_all_purchases
from src.DB.models import Purchase


@pytest.mark.asyncio
async def test_save_purchases_postgres():
    purchases = [
        {
            "purchase_id": "postgres_test_1001",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-01",
            "amount": 500.0,
        },
        {
            "purchase_id": "postgres_test_1002",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-08",
            "amount": 700.0,
        },
        {
            "purchase_id": "postgres_test_1003",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-15",
            "amount": 450.0,
        },
    ]

    async with async_session() as session:
        added = await save_purchases(session, purchases)

        assert added == 3

        added_again = await save_purchases(session, purchases)

        assert added_again == 0

        result = await session.execute(
            select(Purchase).where(
                Purchase.purchase_id.in_(
                    [
                        "postgres_test_1001",
                        "postgres_test_1002",
                        "postgres_test_1003",
                    ]
                )
            )
        )

        saved_purchases = result.scalars().all()

        assert len(saved_purchases) == 3

        await session.rollback()