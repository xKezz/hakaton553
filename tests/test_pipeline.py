import json

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.DB.models import Campaign, CampaignCategory, CampaignTarget, Purchase
from src.pipeline import Pipeline


EXPECTED_BONUSES = {
    0: 300,
    1: 200,
    2: 100,
    3: 50,
    4: 0,
}


@pytest.mark.asyncio
async def test_pipeline_full_flow(session, make_sample_raw_df, tmp_path):
    output_path = tmp_path / "pipeline_report.json"

    pipeline = Pipeline(make_sample_raw_df)

    report = await pipeline.run(
        session,
        source_file_path="data/source_files/test.csv",
        output_path=str(output_path),
        reference_date=pd.Timestamp("2026-09-28"),
    )

    assert report["status"] == "DRAFT"
    assert isinstance(report["campaign_id"], int)

    assert report["meta"]["requested_count_cat"] == 5
    assert len(report["categories"]) == 5
    assert len(report["clients"]) == 10

    purchases = (
        await session.execute(
            select(Purchase)
        )
    ).scalars().all()

    assert len(purchases) == sum(range(1, 11))

    campaigns = (
        await session.execute(
            select(Campaign)
        )
    ).scalars().all()

    assert len(campaigns) == 1
    assert campaigns[0].status == "DRAFT"
    assert campaigns[0].source_file_path == "data/source_files/test.csv"
    assert campaigns[0].total_clients == 10

    categories = (
        await session.execute(
            select(CampaignCategory)
            .where(
                CampaignCategory.campaign_id == campaigns[0].id
            )
        )
    ).scalars().all()

    assert len(categories) == 5

    bonus_by_group = {
        int(category.segment_group): category.final_bonus
        for category in categories
    }

    assert set(bonus_by_group) == {0, 1, 2, 3, 4}

    for category in categories:
        group = int(category.segment_group)

        if category.clients_count > 0:
            assert category.final_bonus == EXPECTED_BONUSES[group]
        else:
            assert category.final_bonus == 0

    targets = (
        await session.execute(
            select(CampaignTarget)
            .options(selectinload(CampaignTarget.category))
            .where(
                CampaignTarget.campaign_id == campaigns[0].id
            )
        )
    ).scalars().all()

    assert all(
        int(target.category.segment_group) < 4
        for target in targets
    )

    assert all(
        target.notification_status == "PENDING"
        for target in targets
    )

    assert all(
        target.bonus_amount
        == bonus_by_group[
            int(target.category.segment_group)
        ]
        for target in targets
    )

    assert campaigns[0].at_risk_clients == len(targets)

    assert output_path.exists()

    with output_path.open("r", encoding="utf-8") as file:
        saved_report = json.load(file)

    assert saved_report["campaign_id"] == campaigns[0].id


@pytest.mark.asyncio
async def test_pipeline_does_not_duplicate_existing_purchase_history(
    session,
    make_sample_raw_df,
):
    pipeline = Pipeline(make_sample_raw_df)

    first_report = await pipeline.run(
        session,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    first_count = (
        await session.execute(
            select(Purchase)
        )
    ).scalars().all()

    second_pipeline = Pipeline(make_sample_raw_df)

    second_report = await second_pipeline.run(
        session,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    second_count = (
        await session.execute(
            select(Purchase)
        )
    ).scalars().all()

    assert len(second_count) == len(first_count)
    assert first_report["campaign_id"] != second_report["campaign_id"]
    assert second_report["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_pipeline_accumulates_history(
    session,
    make_sample_raw_df,
):
    first_df = make_sample_raw_df.iloc[:10].copy()
    second_df = make_sample_raw_df.iloc[10:20].copy()

    first = Pipeline(first_df)

    await first.run(
        session,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    second = Pipeline(second_df)

    await second.run(
        session,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    purchases = (
        await session.execute(
            select(Purchase)
        )
    ).scalars().all()

    assert len(purchases) == 20


def test_pipeline_preprocess_returns_four_columns(
    make_sample_raw_df,
):
    pipeline = Pipeline(make_sample_raw_df)

    result = pipeline.predprocess()

    assert list(result.columns) == [
        "purchase_id",
        "client_id",
        "purchase_date",
        "amount",
    ]
    assert not result.empty
