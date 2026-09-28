import pandas as pd
import pytest

from src.segmentation.business_metrics import (
    BusinessMetrics,
    normalize_series,
)


def test_normalize_series():
    series = pd.Series([10, 20, 30])

    result = normalize_series(series)

    assert result.tolist() == [
        0.0,
        0.5,
        1.0,
    ]


def test_normalize_series_constant():
    series = pd.Series([5, 5, 5])

    result = normalize_series(series)

    assert result.tolist() == [
        0.5,
        0.5,
        0.5,
    ]


def test_business_metrics_basic(make_sample_history_df):
    bm = BusinessMetrics(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    metrics = bm.metrics()

    assert len(metrics) == 10
    assert metrics["client_id"].nunique() == 10
    assert metrics["recency"].min() >= 0
    assert metrics["frequency"].min() == 1
    assert metrics["frequency"].max() == 10
    assert metrics["total_amount"].gt(0).all()
    assert metrics["avg_amount"].gt(0).all()
    assert metrics["monetary_score"].between(
        0,
        1,
    ).all()


def test_business_metrics_reference_date():
    df = pd.DataFrame(
        {
            "client_id": [
                "+79990000001",
            ],
            "purchase_date": [
                "2026-09-20",
            ],
            "amount": [500],
        }
    )

    bm = BusinessMetrics(
        data=df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    row = bm.metrics().iloc[0]

    assert row["recency"] == 8
    assert row["frequency"] == 1
    assert row["total_amount"] == 500
    assert row["avg_amount"] == 500


def test_business_metrics_missing_column():
    df = pd.DataFrame(
        {
            "client_id": [
                "+79990000001",
            ],
            "amount": [500],
        }
    )

    with pytest.raises(ValueError):
        BusinessMetrics(data=df)
