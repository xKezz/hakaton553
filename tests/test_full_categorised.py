import json

import pandas as pd

from src.segmentation.full_categorised import build_bonus_report, save_bonus_report


EXPECTED_BONUSES = {
    0: 300,
    1: 200,
    2: 100,
    3: 50,
    4: 0,
}


def test_build_bonus_report_has_five_groups(make_sample_history_df):
    report = build_bonus_report(
        data=make_sample_history_df,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    assert report["meta"]["requested_count_cat"] == 5
    assert report["meta"]["effective_count_cat"] == 5
    assert len(report["categories"]) == 5
    assert len(report["clients"]) == 10

    categories = sorted(
        report["categories"],
        key=lambda item: int(item["segment_group"]),
    )

    assert [int(category["segment_group"]) for category in categories] == [
        0,
        1,
        2,
        3,
        4,
    ]

    for category in categories:
        group = int(category["segment_group"])
        clients_count = category["clients_count"]
        proposed_bonus = category["proposed_bonus"]
        final_bonus = category["final_bonus"]

        assert 0 <= proposed_bonus <= 300
        assert proposed_bonus % 50 == 0
        assert 0 <= final_bonus <= 300
        assert final_bonus % 50 == 0

        if clients_count > 0:
            assert proposed_bonus == EXPECTED_BONUSES[group]
            assert final_bonus == proposed_bonus
        else:
            assert final_bonus == 0

    assert categories[-1]["segment_group"] == 4
    assert categories[-1]["final_bonus"] == 0


def test_build_bonus_report_category_counts_sum_to_clients(
    make_sample_history_df,
):
    report = build_bonus_report(
        data=make_sample_history_df,
        reference_date=pd.Timestamp("2026-09-28"),
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
        reference_date=pd.Timestamp("2026-09-28"),
    )

    assert report["categories"] == []
    assert report["clients"] == []


def test_save_bonus_report(tmp_path, make_sample_history_df):
    output_path = tmp_path / "report.json"

    save_bonus_report(
        str(output_path),
        data=make_sample_history_df,
        reference_date=pd.Timestamp("2026-09-28"),
    )

    assert output_path.exists()

    with output_path.open("r", encoding="utf-8") as file:
        report = json.load(file)

    assert report["meta"]["requested_count_cat"] == 5
    assert len(report["categories"]) == 5
