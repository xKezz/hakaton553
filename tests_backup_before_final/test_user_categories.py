import pandas as pd
import pytest

from src.segmentation.business_metrics import (
    BusinessMetrics,
)
from src.segmentation.user_categories import (
    UserCategories,
)


def test_categories_matrix_has_expected_structure(
    make_sample_history_df,
):
    bm = BusinessMetrics(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    uc = UserCategories(
        business_metrics=bm,
        method="win-back",
    )

    matrix = uc.categories_matrix

    assert {
        "recency",
        "frequency",
        "monetary_score",
        "score",
    }.issubset(matrix.columns)

    assert len(matrix) == 27
    assert matrix["score"].is_monotonic_decreasing


def test_users_cat_creates_five_groups(
    make_sample_history_df,
):
    bm = BusinessMetrics(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    uc = UserCategories(
        business_metrics=bm,
        method="win-back",
    )

    result = uc.users_cat(5)

    assert uc.requested_count_cat == 5
    assert uc.effective_count_cat == 5

    assert set(
        result["campaign"].unique()
    ).issubset(
        {0, 1, 2, 3, 4}
    )

    assert set(
        uc.categories_matrix["campaign"].unique()
    ) == {
        0,
        1,
        2,
        3,
        4,
    }


def test_users_cat_invalid_count(
    make_sample_history_df,
):
    bm = BusinessMetrics(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    uc = UserCategories(
        business_metrics=bm
    )

    with pytest.raises(ValueError):
        uc.users_cat(0)

    with pytest.raises(TypeError):
        uc.users_cat(2.5)


def test_unknown_strategy(
    make_sample_history_df,
):
    bm = BusinessMetrics(
        data=make_sample_history_df,
        reference_date=pd.Timestamp(
            "2026-09-28"
        ),
    )

    with pytest.raises(ValueError):
        UserCategories(
            business_metrics=bm,
            method="unknown",
        )
