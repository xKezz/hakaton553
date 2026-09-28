from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.DB.models import (
    Campaign,
    CampaignCategory,
    CampaignTarget,
    Client,
    Purchase,
)


# =========================================================
# CLIENT
# =========================================================

async def get_client_by_max_id(
    session: AsyncSession,
    max_user_id: str,
) -> Client | None:
    result = await session.execute(
        select(Client).where(
            Client.max_user_id == max_user_id
        )
    )

    return result.scalar_one_or_none()


async def get_client_by_phone(
    session: AsyncSession,
    phone_e164: str,
) -> Client | None:
    result = await session.execute(
        select(Client).where(
            Client.phone_e164 == phone_e164
        )
    )

    return result.scalar_one_or_none()


async def create_client(
    session: AsyncSession,
    phone_e164: str,
    max_user_id: str | None = None,
) -> Client | None:
    existing_by_phone = await get_client_by_phone(
        session,
        phone_e164,
    )

    if existing_by_phone:
        if (
            max_user_id
            and not existing_by_phone.max_user_id
        ):
            existing_by_phone.max_user_id = max_user_id

        return existing_by_phone

    if max_user_id:
        existing_by_max_id = await get_client_by_max_id(
            session,
            max_user_id,
        )

        if existing_by_max_id:
            return existing_by_max_id

    client = Client(
        phone_e164=phone_e164,
        max_user_id=max_user_id,
    )

    session.add(client)
    await session.flush()

    return client


async def update_client_max_id(
    session: AsyncSession,
    phone_e164: str,
    max_user_id: str,
) -> Client | None:
    client = await get_client_by_phone(
        session,
        phone_e164,
    )

    if not client:
        return None

    client.max_user_id = max_user_id

    await session.flush()

    return client


# =========================================================
# PURCHASE
# =========================================================

async def get_existing_purchase_ids(
    session: AsyncSession,
    purchase_ids: Iterable[str],
) -> set[str]:
    purchase_ids = list(set(purchase_ids))

    if not purchase_ids:
        return set()

    result = await session.execute(
        select(Purchase.purchase_id).where(
            Purchase.purchase_id.in_(purchase_ids)
        )
    )

    return set(result.scalars().all())


async def save_purchases(
    session: AsyncSession,
    purchases: list[dict],
) -> int:
    if not purchases:
        return 0

    purchase_ids = [
        str(item["purchase_id"])
        for item in purchases
    ]

    existing_ids = await get_existing_purchase_ids(
        session,
        purchase_ids,
    )

    new_purchases = [
        item
        for item in purchases
        if str(item["purchase_id"])
        not in existing_ids
    ]

    if not new_purchases:
        return 0

    phones = {
        str(item["client_id"])
        for item in new_purchases
    }

    result = await session.execute(
        select(Client).where(
            Client.phone_e164.in_(phones)
        )
    )

    clients_by_phone = {
        client.phone_e164: client
        for client in result.scalars().all()
    }

    for phone in phones:
        if phone not in clients_by_phone:
            client = Client(
                phone_e164=phone,
                max_user_id=None,
            )

            session.add(client)
            clients_by_phone[phone] = client

    await session.flush()

    for item in new_purchases:
        client = clients_by_phone[
            str(item["client_id"])
        ]

        purchase = Purchase(
            purchase_id=str(item["purchase_id"]),
            client_id=client.id,
            purchase_date=datetime.strptime(
                str(item["purchase_date"]),
                "%Y-%m-%d",
            ),
            amount=float(item["amount"]),
        )

        session.add(purchase)

    await session.flush()

    return len(new_purchases)


async def get_all_purchases(
    session: AsyncSession,
) -> list[Purchase]:
    result = await session.execute(
        select(Purchase)
        .options(
            selectinload(Purchase.client)
        )
        .order_by(Purchase.purchase_date)
    )

    return list(result.scalars().all())


async def get_purchases_by_client(
    session: AsyncSession,
    client_id: int,
) -> list[Purchase]:
    result = await session.execute(
        select(Purchase)
        .where(
            Purchase.client_id == client_id
        )
        .order_by(Purchase.purchase_date)
    )

    return list(result.scalars().all())


# =========================================================
# CAMPAIGN
# =========================================================

async def create_campaign(
    session: AsyncSession,
    source_file_path: str | None = None,
    config: dict | None = None,
    total_clients: int = 0,
    at_risk_clients: int = 0,
    campaign_ends_at: datetime | None = None,
) -> Campaign:
    if campaign_ends_at is None:
        raise ValueError(
            "campaign_ends_at обязателен для создания кампании."
        )

    campaign = Campaign(
        source_file_path=source_file_path,
        config=config,
        total_clients=total_clients,
        at_risk_clients=at_risk_clients,
        campaign_ends_at=campaign_ends_at,
    )

    session.add(campaign)
    await session.flush()

    return campaign


async def get_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> Campaign | None:
    result = await session.execute(
        select(Campaign)
        .options(
            selectinload(Campaign.categories),
            selectinload(Campaign.targets),
        )
        .where(
            Campaign.id == campaign_id
        )
    )

    return result.scalar_one_or_none()


async def get_latest_campaign(
    session: AsyncSession,
) -> Campaign | None:
    result = await session.execute(
        select(Campaign)
        .options(
            selectinload(Campaign.categories),
            selectinload(Campaign.targets),
        )
        .order_by(
            Campaign.launched_at.desc()
        )
        .limit(1)
    )

    return result.scalar_one_or_none()


async def update_campaign_status(
    session: AsyncSession,
    campaign_id: int,
    status: str,
) -> Campaign | None:
    campaign = await get_campaign(
        session,
        campaign_id,
    )

    if not campaign:
        return None

    campaign.status = status

    await session.flush()

    return campaign


# =========================================================
# CAMPAIGN CATEGORY
# =========================================================

async def create_campaign_categories(
    session: AsyncSession,
    campaign_id: int,
    categories: list[dict],
) -> list[CampaignCategory]:
    result = []

    for category_data in categories:
        category = CampaignCategory(
            campaign_id=campaign_id,
            segment_group=str(
                category_data["segment_group"]
            ),
            label=category_data["label"],
            clients_count=category_data["clients_count"],
            avg_recency=category_data[
                "avg_recency"
            ],
            avg_frequency=category_data[
                "avg_frequency"
            ],
            avg_monetary_score=category_data[
                "avg_monetary_score"
            ],
            avg_amount=category_data[
                "avg_amount"
            ],
            proposed_bonus=category_data[
                "proposed_bonus"
            ],
            final_bonus=category_data[
                "final_bonus"
            ],
        )

        session.add(category)
        result.append(category)

    await session.flush()

    return result


async def get_campaign_categories(
    session: AsyncSession,
    campaign_id: int,
) -> list[CampaignCategory]:
    result = await session.execute(
        select(CampaignCategory)
        .where(
            CampaignCategory.campaign_id == campaign_id
        )
        .order_by(CampaignCategory.id)
    )

    return list(result.scalars().all())


async def update_category_bonus(
    session: AsyncSession,
    category_id: int,
    final_bonus: int,
) -> CampaignCategory | None:
    if final_bonus < 0:
        raise ValueError(
            "final_bonus не может быть отрицательным."
        )

    result = await session.execute(
        select(CampaignCategory).where(
            CampaignCategory.id == category_id
        )
    )

    category = result.scalar_one_or_none()

    if not category:
        return None

    category.final_bonus = final_bonus

    await session.flush()

    return category


# =========================================================
# CAMPAIGN TARGET
# =========================================================

async def create_campaign_targets(
    session: AsyncSession,
    targets: list[dict],
) -> list[CampaignTarget]:
    result = []

    for target_data in targets:
        bonus_amount = int(
            target_data.get(
                "bonus_amount",
                0,
            )
        )

        # Клиентов с нулевым бонусом
        # в campaign_target не записываем.
        if bonus_amount <= 0:
            continue

        target = CampaignTarget(
            campaign_id=target_data["campaign_id"],
            category_id=target_data["category_id"],
            client_id=target_data["client_id"],
            recency=target_data.get("recency"),
            frequency=target_data.get("frequency"),
            monetary_score=target_data.get(
                "monetary_score"
            ),
            avg_amount=target_data.get(
                "avg_amount"
            ),
            bonus_amount=bonus_amount,
            bonus_realised=target_data.get(
                "bonus_realised"
            ),
        )

        session.add(target)
        result.append(target)

    await session.flush()

    return result


async def get_campaign_targets(
    session: AsyncSession,
    campaign_id: int,
) -> list[CampaignTarget]:
    result = await session.execute(
        select(CampaignTarget)
        .where(
            CampaignTarget.campaign_id == campaign_id
        )
        .order_by(CampaignTarget.id)
    )

    return list(result.scalars().all())


async def update_target_bonus_realised(
    session: AsyncSession,
    target_id: int,
    bonus_realised: int | None,
) -> CampaignTarget | None:
    if (
        bonus_realised is not None
        and bonus_realised < 0
    ):
        raise ValueError(
            "bonus_realised не может быть отрицательным."
        )

    result = await session.execute(
        select(CampaignTarget).where(
            CampaignTarget.id == target_id
        )
    )

    target = result.scalar_one_or_none()

    if not target:
        return None

    target.bonus_realised = bonus_realised

    await session.flush()

    return target