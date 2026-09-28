import json

import pandas as pd

from src.segmentation.full_categorised import (
    build_bonus_report,
    save_bonus_report,
)


def test_build_bonus_report_has_five_groups(
    make_sample_history_df,
):
    report = build_bonus_report(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    assert (
        report["meta"]["requested_count_cat"]
        == 5
    )

    assert (
        report["meta"]["effective_count_cat"]
        == 5
    )

    assert len(report["categories"]) == 5
    assert len(report["clients"]) == 10

    categories = sorted(
        report["categories"],
        key=lambda item: int(
            item["segment_group"]
        ),
    )

    assert [
        category["proposed_bonus"]
        for category in categories
    ] == [
        300,
        200,
        100,
        50,
        0,
    ]


def test_build_bonus_report_category_counts_sum_to_clients(
    make_sample_history_df,
):
    report = build_bonus_report(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    assert sum(
        category["clients_count"]
        for category in report["categories"]
    ) == len(report["clients"])


def test_build_bonus_report_empty():
    data = pd.DataFrame(
        columns=[
            "client_id",
            "purchase_date",
            "amount",
        ]
    )

    report = build_bonus_report(
        data=data,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    assert report["categories"] == []
    assert report["clients"] == []


def test_save_bonus_report(
    tmp_path,
    make_sample_history_df,
):
    output_path = (
        tmp_path / "report.json"
    )

    save_bonus_report(
        str(output_path),
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    assert output_path.exists()

    with output_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        report = json.load(file)

    assert (
        report["meta"][
            "requested_count_cat"
        ]
        == 5
    )

    assert len(
        report["categories"]
    ) == 5
