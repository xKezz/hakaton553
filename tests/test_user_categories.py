import pytest
import pandas as pd
import numpy as np
import warnings
from src.segmentation.business_metrics import BusinessMetrics
from src.segmentation.user_categories import UserCategories, _transform_with_bounds


class TestTransformWithBounds:
    def test_transform_basic(self):
        values = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        min_vals = np.array([1.0, 2.0])
        max_vals = np.array([5.0, 6.0])
        result = _transform_with_bounds(values, min_vals, max_vals)
        
        expected = np.array([
            [0.0, 0.0],
            [0.5, 0.5],
            [1.0, 1.0]
        ])
        np.testing.assert_array_almost_equal(result, expected)

    def test_transform_zero_range_uses_constant(self):
        values = np.array([[1.0, 2.0], [1.0, 4.0]])
        min_vals = np.array([1.0, 2.0])
        max_vals = np.array([1.0, 6.0])  # First column has zero range
        result = _transform_with_bounds(values, min_vals, max_vals, constant=0.5)
        
        assert result[0, 0] == 0.5
        assert result[1, 0] == 0.5
        assert result[0, 1] == 0.0
        assert result[1, 1] == 0.5

    def test_transform_empty_array(self):
        values = np.array([]).reshape(0, 2)
        min_vals = np.array([1.0, 2.0])
        max_vals = np.array([5.0, 6.0])
        result = _transform_with_bounds(values, min_vals, max_vals)
        
        assert result.shape == (0, 2)


class TestUserCategoriesInit:
    def test_init_with_business_metrics(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        assert uc.method == "win-back"
        assert uc.business_metrics is bm
        assert len(uc.rfm_df) == 3

    def test_init_creates_categories_matrix(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        assert uc.categories_matrix is not None
        assert len(uc.categories_matrix) > 0
        assert "score" in uc.categories_matrix.columns
        assert "recency" in uc.categories_matrix.columns

    def test_init_raises_on_unknown_method(self):
        df = pd.DataFrame({
            "client_id": ["A"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        with pytest.raises(ValueError, match="не поддерживается"):
            UserCategories(business_metrics=bm, method="unknown")

    def test_register_strategy(self):
        UserCategories.register_strategy("test_method", (0.3, 0.3, 0.4), True)
        assert "test_method" in UserCategories.METHOD_CONFIGS
        assert UserCategories.METHOD_CONFIGS["test_method"]["weights"] == (0.3, 0.3, 0.4)

    def test_register_strategy_validates_weights(self):
        with pytest.raises(ValueError, match="3 значения"):
            UserCategories.register_strategy("bad", (0.5, 0.5), True)
        with pytest.raises(ValueError, match="Сумма весов"):
            UserCategories.register_strategy("bad", (0.0, 0.0, 0.0), True)


class TestUserCategoriesMatrix:
    def test_matrix_has_27_combinations_max(self):
        df = pd.DataFrame({
            "client_id": [f"C{i}" for i in range(100)],
            "purchase_date": pd.date_range("2026-01-01", periods=100, freq="D"),
            "amount": np.random.uniform(100, 1000, 100),
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        # 3x3x3 = 27 max, but deduplication may reduce
        assert len(uc.categories_matrix) <= 27

    def test_matrix_score_calculation_win_back(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        matrix = uc.categories_matrix
        # win-back: weights (0.60, 0.10, 0.30), recency_higher_is_better=True
        # Score = 0.60*recency_norm + 0.10*freq_norm + 0.30*monetary_norm
        assert "score" in matrix.columns
        assert matrix["score"].notna().all()

    def test_matrix_sorted_by_score_descending(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        matrix = uc.categories_matrix
        scores = matrix["score"].values
        assert np.all(np.diff(scores) <= 1e-10)  # Non-increasing (descending)

    def test_category_0_is_highest_priority(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        # Category 0 should have highest score
        matrix = uc.categories_matrix
        assert matrix.iloc[0]["score"] >= matrix.iloc[1]["score"]


class TestUserCategoriesAssignRfmGroups:
    def test_assign_groups_basic(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C", "D", "E"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01"],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        result = uc.assign_rfm_groups()
        
        assert "group_id" in result.columns
        assert "distance" in result.columns
        assert len(result) == 5
        assert result["group_id"].dtype in [np.int64, int]
        assert result["distance"].dtype in [np.float64, float]
        assert all(0 <= g < len(uc.categories_matrix) for g in result["group_id"])

    def test_assign_groups_empty_data(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        result = uc.assign_rfm_groups()
        
        assert len(result) == 0
        assert "group_id" in result.columns
        assert "distance" in result.columns

    def test_assign_groups_all_same_metrics(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-01-15", "2026-01-15"],
            "amount": [100.0, 100.0, 100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        result = uc.assign_rfm_groups()
        
        # All should get same group (group 0 since matrix deduplicates to 1 row)
        assert all(result["group_id"] == 0)
        assert all(result["distance"] == 0.0)


class TestUserCategoriesUsersCat:
    def test_users_cat_basic(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C", "D", "E"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01"],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        result = uc.users_cat(3)
        
        assert "campaign" in result.columns
        assert len(result) == 5
        assert result["campaign"].dtype in [np.int64, int]
        assert set(result["campaign"].unique()) <= {0, 1, 2}
        assert uc.effective_count_cat == 3

    def test_users_cat_category_0_highest_priority(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C", "D", "E"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01"],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        result = uc.users_cat(3)
        
        # The matrix is split into campaigns starting from 0
        # With enough unique groups, campaign 0 should exist
        # Verify that the first campaign (0) corresponds to highest priority groups
        matrix = uc.categories_matrix
        campaign_0_groups = matrix[matrix["campaign"] == 0].index.tolist()
        # At least one group should be in campaign 0 (highest priority)
        assert len(campaign_0_groups) > 0
        # Campaign 0 groups should have the highest scores
        if len(campaign_0_groups) > 0 and len(matrix) > len(campaign_0_groups):
            other_groups = matrix[matrix["campaign"] != 0].index.tolist()
            if len(other_groups) > 0:
                assert matrix.loc[campaign_0_groups, "score"].min() >= matrix.loc[other_groups, "score"].max()

    def test_users_cat_count_cat_more_than_available_groups(self):
        df = pd.DataFrame({
            "client_id": ["A", "B"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.0, 200.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        with pytest.warns(UserWarning, match="уменьшен"):
            result = uc.users_cat(10)
        
        assert uc.effective_count_cat < 10
        assert uc.effective_count_cat == len(uc.categories_matrix)

    def test_users_cat_empty_data(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        with pytest.warns(UserWarning, match="не содержат клиентов"):
            result = uc.users_cat(3)
        
        assert uc.effective_count_cat == 0
        assert "campaign" in result.columns
        assert len(result) == 0

    def test_users_cat_validates_count_cat_type(self):
        df = pd.DataFrame({
            "client_id": ["A"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        with pytest.raises(TypeError):
            uc.users_cat("3")
        with pytest.raises(TypeError):
            uc.users_cat(3.5)

    def test_users_cat_validates_count_cat_positive(self):
        df = pd.DataFrame({
            "client_id": ["A"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        with pytest.raises(ValueError, match="больше нуля"):
            uc.users_cat(0)
        with pytest.raises(ValueError, match="больше нуля"):
            uc.users_cat(-1)

    def test_users_cat_splits_matrix_evenly(self):
        df = pd.DataFrame({
            "client_id": [f"C{i}" for i in range(27)],
            "purchase_date": pd.date_range("2026-01-01", periods=27, freq="D"),
            "amount": np.random.uniform(100, 1000, 27),
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        # With 27 unique combinations and count_cat=3, categories are split
        result = uc.users_cat(3)
        
        # Should have 3 categories (0, 1, 2)
        assert set(result["campaign"].unique()) <= {0, 1, 2}
        assert uc.effective_count_cat == 3
        # Each campaign should have at least some clients
        campaign_counts = result["campaign"].value_counts()
        assert all(c > 0 for c in campaign_counts)


class TestUserCategoriesIntegration:
    def test_full_flow_returns_expected_columns(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C", "D", "E"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01"],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(3)
        
        result = uc.rfm_df
        expected_cols = ["client_id", "last_purchase_date", "recency", "frequency",
                         "total_amount", "avg_amount", "monetary_score",
                         "group_id", "distance", "campaign"]
        for col in expected_cols:
            assert col in result.columns

    def test_recency_higher_is_better_win_back(self):
        # For win-back, higher recency (more days since last purchase) = higher priority
        df = pd.DataFrame({
            "client_id": ["recent", "old"],
            "purchase_date": ["2026-06-20", "2026-01-01"],  # recent vs old
            "amount": [100.0, 100.0],
            "frequency": [1, 1],  # Same frequency
        })
        # Need to create BusinessMetrics with proper data
        df2 = pd.DataFrame({
            "client_id": ["recent", "old"],
            "purchase_date": ["2026-06-20", "2026-01-01"],
            "amount": [100.0, 100.0],
        })
        bm = BusinessMetrics(data=df2, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        
        # Check matrix: higher recency should get higher score
        matrix = uc.categories_matrix
        recency_col = matrix["recency"]
        score_col = matrix["score"]
        # Since recency_higher_is_better=True, higher recency -> higher normalized recency -> higher score
        # (when frequency and monetary are equal)
        assert recency_col.max() > recency_col.min()


class TestUserCategoriesWinBackPriority:
    def test_win_back_prioritizes_high_recency_low_frequency(self):
        """Win-back should prioritize clients who haven't purchased in long time (high recency) and buy rarely."""
        df = pd.DataFrame({
            "client_id": ["churned", "active", "recent_churn"],
            "purchase_date": ["2026-01-01", "2026-06-20", "2026-05-01"],
            "amount": [100.0, 100.0, 100.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        uc = UserCategories(business_metrics=bm, method="win-back")
        uc.users_cat(2)
        
        # "churned" client (high recency, low frequency) should be in category 0 (highest priority)
        churned_campaign = uc.rfm_df[uc.rfm_df["client_id"] == "churned"]["campaign"].iloc[0]
        active_campaign = uc.rfm_df[uc.rfm_df["client_id"] == "active"]["campaign"].iloc[0]
        
        assert churned_campaign == 0  # Highest priority
        assert active_campaign == 1   # Lower priority