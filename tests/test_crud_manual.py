import pytest
from sqlalchemy import select

from src.DB.crud import save_purchases, get_all_purchases
from src.DB.models import Purchase


@pytest.mark.asyncio
async def test_save_purchases_without_duplicates(db_session):
    first_batch = [
        {
            "purchase_id": "1001",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-01",
            "amount": 500.0,
        },
        {
            "purchase_id": "1002",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-08",
            "amount": 700.0,
        },
    ]

    second_batch = [
        {
            "purchase_id": "1001",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-01",
            "amount": 500.0,
        },
        {
            "purchase_id": "1002",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-08",
            "amount": 700.0,
        },
        {
            "purchase_id": "1003",
            "client_id": "+79991234567",
            "purchase_date": "2026-09-15",
            "amount": 450.0,
        },
    ]

    added_first = await save_purchases(
        db_session,
        first_batch,
    )

    assert added_first == 2

    added_second = await save_purchases(
        db_session,
        second_batch,
    )

    assert added_second == 1

    purchases = await get_all_purchases(
        db_session,
    )

    assert len(purchases) == 3

    purchase_ids = {
        purchase.purchase_id
        for purchase in purchases
    }

    assert purchase_ids == {
        "1001",
        "1002",
        "1003",
    }