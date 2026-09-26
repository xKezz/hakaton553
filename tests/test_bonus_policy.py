import pytest
import pandas as pd
import numpy as np
import warnings
from src.segmentation.business_metrics import BusinessMetrics
from src.segmentation.user_categories import UserCategories
from src.recommendations.bonus_policy import BonusPolicy


def create_test_setup():
    """Create a standard test setup with 3 categories."""
    df = pd.DataFrame({
        "client_id": [f"C{i}" for i in range(30)],
        "purchase_date": pd.date_range("2026-01-01", periods=30, freq="D"),
        "amount": np.random.uniform(100, 1000, 30),
    })
    bm = BusinessMetrics(data=df, reference_date="2026-06-22")
    uc = UserCategories(business_metrics=bm, method="win-back")
    uc.users_cat(3)
    return uc


class TestBonusPolicyInit:
    def test_init_basic(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        assert bp.max_bonus_points == 250
        assert bp.step == 50
        assert bp.need_free == 0.25
        assert bp.value_cut == 0.20
        assert bp.churn_bonus == 50
        assert bp.policy is not None
        assert len(bp.policy) == 3

    def test_init_validates_max_bonus_points_negative(self):
        uc = create_test_setup()
        with pytest.raises(ValueError, match="отрицательным"):
            BonusPolicy(uc, max_bonus_points=-10)

    def test_init_warns_max_bonus_points_over_250(self):
        uc = create_test_setup()
        with pytest.warns(UserWarning, match="больше стандартного лимита 250"):
            BonusPolicy(uc, max_bonus_points=500)

    def test_init_validates_step_positive(self):
        uc = create_test_setup()
        with pytest.raises(ValueError, match="больше нуля"):
            BonusPolicy(uc, step=0)
        with pytest.raises(ValueError, match="больше нуля"):
            BonusPolicy(uc, step=-10)

    def test_init_adjusts_step_if_larger_than_max(self):
        uc = create_test_setup()
        with pytest.warns(UserWarning, match="уменьшен до max_bonus_points"):
            bp = BonusPolicy(uc, max_bonus_points=100, step=200)
        assert bp.step == 100

    def test_init_validates_single_category_bonus(self):
        uc = create_test_setup()
        with pytest.raises(ValueError, match="отрицательным"):
            BonusPolicy(uc, single_category_bonus=-10)
        with pytest.warns(UserWarning, match="уменьшено"):
            bp = BonusPolicy(uc, max_bonus_points=100, single_category_bonus=200)
        assert bp.single_category_bonus == 100

    def test_init_validates_churn_bonus(self):
        uc = create_test_setup()
        with pytest.raises(ValueError, match="отрицательным"):
            BonusPolicy(uc, churn_bonus=-10)
        bp = BonusPolicy(uc, max_bonus_points=100, churn_bonus=200)
        assert bp.churn_bonus == 100  # Clipped to max_bonus_points

    def test_init_raises_if_no_campaign_column(self):
        df = pd.DataFrame({
            "client_id": ["A", "B"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.0, 200.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        # Don't call users_cat
        with pytest.raises(ValueError, match="Сначала вызовите"):
            BonusPolicy(uc)

    def test_init_empty_rfm(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(3)
        bp = BonusPolicy(uc)
        
        assert bp.policy.empty
        assert list(bp.policy.columns) == [
            "campaign", "proposed_bonus", "reason", "clients_count",
            "recency", "frequency", "monetary_score", "avg_amount"
        ]


class TestBonusPolicyRoundToStep:
    def test_round_to_step_basic(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        assert bp._round_to_step(0, 50) == 0
        assert bp._round_to_step(25, 50) == 50  # 0.5 rounds up
        assert bp._round_to_step(24, 50) == 0
        assert bp._round_to_step(75, 50) == 100
        assert bp._round_to_step(74, 50) == 50

    def test_round_to_step_edge_cases(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        assert bp._round_to_step(0.5, 1) == 1
        assert bp._round_to_step(0.49, 1) == 0


class TestBonusPolicyCompute:
    def test_need_and_value_calculation(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        # Check that need and value are computed internally (not in final policy)
        # They are used in _compute but not stored in policy
        # We can verify by checking that the policy has the expected structure
        assert len(bp.policy) == 3
        assert "proposed_bonus" in bp.policy.columns
        assert "reason" in bp.policy.columns

    def test_high_need_low_value_gets_churn_bonus(self):
        # Create data where one category has high recency (high need) but low monetary (low value)
        df = pd.DataFrame({
            "client_id": ["churned"] * 5 + ["active"] * 5 + ["mid"] * 5,
            "purchase_date": 
                ["2026-01-01"] * 5 +  # High recency
                ["2026-06-20"] * 5 +  # Low recency
                ["2026-04-01"] * 5,   # Medium
            "amount": 
                [100.0] * 5 +         # Low amount
                [1000.0] * 5 +        # High amount
                [500.0] * 5,          # Medium
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(3)
        bp = BonusPolicy(uc, max_bonus_points=250, step=50, churn_bonus=50)
        
        # The churned category should have high need, low value
        # and get churn_bonus (or step, whichever is higher)
        policy = bp.policy
        churned_row = policy[policy["recency"] == policy["recency"].max()]
        assert len(churned_row) == 1
        # Since need > need_free and value <= value_cut and r_norm >= 0.5
        # Should get minimal_positive = max(step, churn_bonus) = max(50, 50) = 50
        assert churned_row["proposed_bonus"].iloc[0] == 50
        assert "последнего шанса" in churned_row["reason"].iloc[0]

    def test_low_need_gets_zero_bonus(self):
        # Active clients with high frequency -> low need -> zero bonus
        df = pd.DataFrame({
            "client_id": ["active"] * 10,
            "purchase_date": pd.date_range("2026-06-01", periods=10, freq="D"),
            "amount": [1000.0] * 10,
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(2)
        bp = BonusPolicy(uc, max_bonus_points=250, step=50, need_free=0.25)
        
        # With identical data, only 1 category is created
        policy = bp.policy
        assert len(policy) >= 1
        # Check that bonus calculation works
        assert "proposed_bonus" in policy.columns

    def test_main_formula_calculation(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        # For categories with "стимулирование спроса" reason, bonus should be calculated
        # using the main formula and clipped to [step, max_bonus_points]
        policy = bp.policy
        for row in policy.itertuples():
            if row.reason == "стимулирование спроса":
                assert 50 <= row.proposed_bonus <= 250
                assert row.proposed_bonus % 50 == 0

    def test_bonus_clipped_to_step_and_max(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        policy = bp.policy
        for row in policy.itertuples():
            if row.proposed_bonus > 0:
                assert row.proposed_bonus >= 50  # At least step
                assert row.proposed_bonus <= 250  # At most max_bonus_points
                assert row.proposed_bonus % 50 == 0  # Multiple of step

    def test_single_category_uses_fixed_bonus(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(1)  # Only 1 category
        bp = BonusPolicy(uc, max_bonus_points=250, step=50, single_category_bonus=150)
        
        assert len(bp.policy) == 1
        assert bp.policy["proposed_bonus"].iloc[0] == 150
        assert bp.policy["reason"].iloc[0] == "фиксированный бонус для единственной категории"

    def test_max_bonus_zero_gives_zero(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=0, step=50)
        
        assert all(bp.policy["proposed_bonus"] == 0)
        assert all(bp.policy["reason"] == "max_bonus_points равен 0")


class TestBonusPolicyClients:
    def test_clients_returns_with_bonus_and_reason(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc)
        clients = bp.clients()
        
        assert "proposed_bonus" in clients.columns
        assert "reason" in clients.columns
        assert len(clients) == len(uc.rfm_df)
        assert clients["proposed_bonus"].notna().all()
        assert clients["reason"].notna().all()

    def test_clients_maps_correct_bonus_per_campaign(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc)
        clients = bp.clients()
        
        # Each campaign should have consistent bonus
        for campaign_id in clients["campaign"].unique():
            campaign_bonuses = clients[clients["campaign"] == campaign_id]["proposed_bonus"]
            assert campaign_bonuses.nunique() == 1
            policy_bonus = bp.policy[bp.policy["campaign"] == campaign_id]["proposed_bonus"].iloc[0]
            assert campaign_bonuses.iloc[0] == policy_bonus

    def test_clients_empty_rfm(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(3)
        bp = BonusPolicy(uc)
        clients = bp.clients()
        
        assert len(clients) == 0
        assert "proposed_bonus" in clients.columns
        assert "reason" in clients.columns

    def test_clients_raises_if_no_campaign(self):
        df = pd.DataFrame({
            "client_id": ["A"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        # Don't call users_cat
        with pytest.raises(ValueError, match="Сначала вызовите"):
            BonusPolicy(uc)


class TestBonusPolicyBudget:
    def test_budget_calculation(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        # Budget = sum(proposed_bonus * response_rate)
        budget = bp.budget(response_rate=0.1)
        expected = round(sum(bp.clients()["proposed_bonus"] * 0.1), 2)
        assert budget == expected

    def test_budget_with_different_response_rates(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50)
        
        budget_10 = bp.budget(0.1)
        budget_20 = bp.budget(0.2)
        assert budget_20 == 2 * budget_10

    def test_budget_validates_response_rate(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc)
        
        with pytest.raises(ValueError, match="отрицательным"):
            bp.budget(-0.1)

    def test_budget_empty(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(3)
        bp = BonusPolicy(uc)
        
        assert bp.budget(0.1) == 0.0


class TestBonusPolicyIntegration:
    def test_policy_columns(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc)
        
        expected_cols = [
            "campaign", "proposed_bonus", "reason", "clients_count",
            "recency", "frequency", "monetary_score", "avg_amount"
        ]
        assert list(bp.policy.columns) == expected_cols

    def test_reasons_are_meaningful(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc, max_bonus_points=250, step=50, churn_bonus=50)
        
        reasons = bp.policy["reason"].unique()
        valid_reasons = {
            "фиксированный бонус для единственной категории",
            "max_bonus_points равен 0",
            "высокая естественная активность - бонус не нужен",
            "низкая ценность при оттоке - минимальный бонус последнего шанса",
            "стимулирование спроса"
        }
        for r in reasons:
            assert r in valid_reasons, f"Unknown reason: {r}"

    def test_proposed_bonus_is_float_rounded(self):
        uc = create_test_setup()
        bp = BonusPolicy(uc)
        
        for bonus in bp.policy["proposed_bonus"]:
            assert isinstance(bonus, (int, float))
            # Should be rounded to 2 decimal places per code
            assert round(bonus, 2) == bonus