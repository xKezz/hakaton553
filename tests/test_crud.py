import pytest

from src.DB.crud import (
    create_campaign,
    create_campaign_categories,
    create_campaign_targets,
    create_client,
    get_all_purchases,
    get_campaign,
    get_client_by_max_id,
    get_client_by_phone,
    get_existing_purchase_ids,
    get_latest_campaign,
    save_purchases,
    update_campaign_status,
    update_category_bonus,
    update_client_max_id,
    update_target_notification_status,
)


@pytest.mark.asyncio
async def test_client_crud(session):
    client = await create_client(
        session,
        "+79990000001",
    )

    assert client.id is not None
    assert client.max_user_id is None

    assert (
        await get_client_by_phone(
            session,
            "+79990000001",
        )
    ).id == client.id

    client = await update_client_max_id(
        session,
        "+79990000001",
        "max-1",
    )

    assert client.max_user_id == "max-1"

    assert (
        await get_client_by_max_id(
            session,
            "max-1",
        )
    ).id == client.id


@pytest.mark.asyncio
async def test_save_purchases_without_duplicates(session):
    purchases = [
        {
            "purchase_id": "p1",
            "client_id": "+79990000001",
            "purchase_date": "2026-09-01",
            "amount": 100,
        },
        {
            "purchase_id": "p2",
            "client_id": "+79990000001",
            "purchase_date": "2026-09-02",
            "amount": 200,
        },
    ]

    assert await save_purchases(session, purchases) == 2
    assert await save_purchases(session, purchases) == 0

    all_purchases = await get_all_purchases(session)

    assert len(all_purchases) == 2
    assert all_purchases[0].client.phone_e164 == "+79990000001"

    existing = await get_existing_purchase_ids(
        session,
        ["p1", "p2", "p3"],
    )

    assert existing == {"p1", "p2"}


@pytest.mark.asyncio
async def test_campaign_category_target_crud(session):
    await save_purchases(
        session,
        [
            {
                "purchase_id": "p1",
                "client_id": "+79990000001",
                "purchase_date": "2026-09-01",
                "amount": 100,
            }
        ],
    )

    campaign = await create_campaign(
        session,
        source_file_path="test.csv",
        config={"count_cat": 5},
        total_clients=1,
        at_risk_clients=1,
    )

    categories = await create_campaign_categories(
        session,
        campaign.id,
        [
            {
                "segment_group": 0,
                "label": "priority_0",
                "clients_count": 1,
                "avg_recency": 10,
                "avg_frequency": 2,
                "avg_monetary_score": 0.8,
                "avg_amount": 500,
                "proposed_bonus": 300,
                "final_bonus": 300,
                "reason": "test",
            }
        ],
    )

    assert len(categories) == 1

    client = await get_client_by_phone(
        session,
        "+79990000001",
    )

    targets = await create_campaign_targets(
        session,
        [
            {
                "campaign_id": campaign.id,
                "category_id": categories[0].id,
                "client_id": client.id,
                "phone_e164": client.phone_e164,
                "max_user_id": None,
                "recency": 10,
                "frequency": 2,
                "monetary_score": 0.8,
                "avg_amount": 500,
                "bonus_amount": 300,
                "notification_status": "PENDING",
            }
        ],
    )

    assert len(targets) == 1
    assert targets[0].bonus_amount == 300

    updated_category = await update_category_bonus(
        session,
        categories[0].id,
        200,
    )

    assert updated_category.final_bonus == 200

    updated_target = await update_target_notification_status(
        session,
        targets[0].id,
        "SENT",
    )

    assert updated_target.notification_status == "SENT"

    campaign = await update_campaign_status(
        session,
        campaign.id,
        "DRAFT",
    )

    assert campaign.status == "DRAFT"

    loaded = await get_campaign(
        session,
        campaign.id,
    )

    assert len(loaded.categories) == 1
    assert len(loaded.targets) == 1
    assert loaded.categories[0].final_bonus == 200
    assert loaded.targets[0].notification_status == "SENT"


@pytest.mark.asyncio
async def test_latest_campaign(session):
    first = await create_campaign(
        session,
        source_file_path="first.csv",
    )

    second = await create_campaign(
        session,
        source_file_path="second.csv",
    )

    latest = await get_latest_campaign(session)

    assert latest is not None
    assert latest.id == second.id
    assert latest.source_file_path == "second.csv"
