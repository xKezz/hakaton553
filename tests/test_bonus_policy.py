from types import SimpleNamespace

import pandas as pd
import pytest

from src.recommendations.bonus_policy import BonusPolicy


def make_campaign_df():
    return pd.DataFrame(
        {
            "client_id": [
                "+79990000001",
                "+79990000002",
                "+79990000003",
                "+79990000004",
                "+79990000005",
            ],
            "campaign": [0, 1, 2, 3, 4],
            "recency": [20, 15, 10, 5, 1],
            "frequency": [1, 2, 3, 4, 5],
            "monetary_score": [0.2, 0.4, 0.6, 0.8, 1.0],
            "avg_amount": [100, 200, 300, 400, 500],
            "total_amount": [100, 400, 900, 1600, 2500],
        }
    )


def test_bonus_policy_returns_expected_five_levels():
    category_users = SimpleNamespace(
        rfm_df=make_campaign_df()
    )

    policy = BonusPolicy(
        category_users,
        max_bonus_points=300,
        step=50,
    )

    result = policy.policy.sort_values("campaign")

    assert result["proposed_bonus"].tolist() == [
        300.0,
        200.0,
        100.0,
        50.0,
        0.0,
    ]


def test_bonus_policy_clients_receive_campaign_bonus():
    category_users = SimpleNamespace(
        rfm_df=make_campaign_df()
    )

    policy = BonusPolicy(
        category_users,
        max_bonus_points=300,
        step=50,
    )

    clients = policy.clients().sort_values("campaign")

    assert clients["proposed_bonus"].tolist() == [
        300.0,
        200.0,
        100.0,
        50.0,
        0.0,
    ]


def test_bonus_policy_empty():
    category_users = SimpleNamespace(
        rfm_df=pd.DataFrame()
    )

    policy = BonusPolicy(category_users)

    assert policy.policy.empty
    assert policy.clients().empty


def test_invalid_parameters():
    category_users = SimpleNamespace(
        rfm_df=make_campaign_df()
    )

    with pytest.raises(ValueError):
        BonusPolicy(category_users, max_bonus_points=-1)

    with pytest.raises(ValueError):
        BonusPolicy(category_users, step=0)
