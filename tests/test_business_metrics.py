import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from src.segmentation.business_metrics import BusinessMetrics, normalize_series


class TestNormalizeSeries:
    def test_normalize_basic(self):
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = normalize_series(s)
        expected = pd.Series([0.0, 0.25, 0.5, 0.75, 1.0])
        pd.testing.assert_series_equal(result, expected)

    def test_normalize_all_same_values(self):
        s = pd.Series([10.0, 10.0, 10.0])
        result = normalize_series(s)
        expected = pd.Series([0.5, 0.5, 0.5])
        pd.testing.assert_series_equal(result, expected)

    def test_normalize_single_value(self):
        s = pd.Series([42.0])
        result = normalize_series(s)
        expected = pd.Series([0.5])
        pd.testing.assert_series_equal(result, expected)

    def test_normalize_empty_series(self):
        s = pd.Series([], dtype=float)
        result = normalize_series(s)
        assert len(result) == 0

    def test_normalize_with_nan(self):
        s = pd.Series([10.0, np.nan, 30.0])
        result = normalize_series(s)
        assert pd.isna(result.iloc[1])
        assert result.iloc[0] == 0.0
        assert result.iloc[2] == 1.0

    def test_normalize_constant_returns_half(self):
        s = pd.Series([5.0, 5.0, 5.0])
        result = normalize_series(s, constant=0.3)
        expected = pd.Series([0.3, 0.3, 0.3])
        pd.testing.assert_series_equal(result, expected)


class TestBusinessMetricsInit:
    def test_init_with_dataframe(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        assert len(bm.metrics_df) == 2
        assert list(bm.metrics_df.columns) == [
            "client_id", "last_purchase_date", "recency", "frequency",
            "total_amount", "avg_amount", "monetary_score"
        ]

    def test_init_with_reference_date(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.50],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        assert bm.reference_date == pd.Timestamp("2026-06-22")

    def test_init_default_reference_date_today(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.50],
        })
        bm = BusinessMetrics(data=df)
        assert bm.reference_date.date() == pd.Timestamp.today().normalize().date()

    def test_init_raises_on_missing_columns(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "amount": [100.50],
        })
        with pytest.raises(ValueError, match="отсутствуют обязательные колонки"):
            BusinessMetrics(data=df)

    def test_init_raises_on_no_data_source(self):
        with pytest.raises(ValueError, match="Не указан источник данных"):
            BusinessMetrics()


class TestBusinessMetricsRecency:
    def test_recency_calculation(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321"],
            "purchase_date": ["2026-06-01", "2026-05-15"],
            "amount": [100.50, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        # 2026-06-22 - 2026-06-01 = 21 days
        # 2026-06-22 - 2026-05-15 = 38 days
        recency_values = metrics.set_index("client_id")["recency"]
        assert recency_values["+79991234567"] == 21
        assert recency_values["+79997654321"] == 38

    def test_recency_clipped_at_zero_for_future_dates(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-07-01"],  # Future date
            "amount": [100.50],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        # Future date clipped to reference_date, recency = 0
        assert metrics["recency"].iloc[0] == 0

    def test_recency_future_date_warning(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-07-01"],
            "amount": [100.50],
        })
        with pytest.warns(UserWarning, match="позже reference_date"):
            BusinessMetrics(data=df, reference_date="2026-06-22")


class TestBusinessMetricsFrequency:
    def test_frequency_counts_purchases(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 150.00, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        freq = metrics.set_index("client_id")["frequency"]
        assert freq["+79991234567"] == 2
        assert freq["+79997654321"] == 1


class TestBusinessMetricsTotalAmount:
    def test_total_amount_sums_purchases(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 150.00, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        total = metrics.set_index("client_id")["total_amount"]
        assert total["+79991234567"] == 250.50
        assert total["+79997654321"] == 200.75


class TestBusinessMetricsAvgAmount:
    def test_avg_amount_mean_of_purchases(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.00, 200.00, 300.00],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        avg = metrics.set_index("client_id")["avg_amount"]
        assert avg["+79991234567"] == 150.00
        assert avg["+79997654321"] == 300.00


class TestBusinessMetricsMonetaryScore:
    def test_monetary_score_formula(self):
        df = pd.DataFrame({
            "client_id": ["A", "B", "C"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 500.0, 1000.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        # monetary_score = 0.40 * total_norm + 0.40 * avg_norm + 0.20 * freq_norm
        # All clients have frequency=1, so freq_norm = 0.5 for all
        # total and avg are same for single-purchase clients
        scores = metrics.set_index("client_id")["monetary_score"]
        
        # Should be sorted by client_id
        assert list(metrics["client_id"]) == ["A", "B", "C"]
        assert all(0 <= s <= 1 for s in scores)

    def test_monetary_score_bounded_0_1(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert all(0 <= s <= 1 for s in metrics["monetary_score"])

    def test_monetary_score_rounded(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-01-15"],
            "amount": [100.50],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        # Should be rounded to 12 decimal places
        score = metrics["monetary_score"].iloc[0]
        assert round(score, 12) == score


class TestBusinessMetricsGrouping:
    def test_groups_by_client_id(self):
        df = pd.DataFrame({
            "client_id": ["A", "A", "B", "B", "B"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01"],
            "amount": [100.0, 200.0, 50.0, 150.0, 250.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 2
        assert set(metrics["client_id"]) == {"A", "B"}

    def test_sorts_by_client_id(self):
        df = pd.DataFrame({
            "client_id": ["C", "A", "B"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.0, 200.0, 300.0],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert list(metrics["client_id"]) == ["A", "B", "C"]


class TestBusinessMetricsInvalidData:
    def test_drops_rows_with_nan_client_id(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", np.nan, "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 200.75, 300.00],
        })
        with pytest.warns(UserWarning, match="пустые/некорректные значения"):
            bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 2

    def test_drops_rows_with_nan_date(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321", "+79991112233"],
            "purchase_date": ["2026-01-15", np.nan, "2026-03-01"],
            "amount": [100.50, 200.75, 300.00],
        })
        with pytest.warns(UserWarning, match="пустые/некорректные значения"):
            bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 2

    def test_drops_rows_with_invalid_amount(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321", "+79991112233"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, np.inf, 300.00],
        })
        with pytest.warns(UserWarning, match="пустые/некорректные значения"):
            bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 2

    def test_returns_empty_on_all_invalid(self):
        df = pd.DataFrame({
            "client_id": [np.nan, np.nan],
            "purchase_date": [np.nan, np.nan],
            "amount": [np.nan, np.nan],
        })
        with pytest.warns(UserWarning):
            bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 0

    def test_raises_on_invalid_date_format(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["not a date"],
            "amount": [100.50],
        })
        with pytest.raises(ValueError, match="Некорректная дата"):
            BusinessMetrics(data=df, reference_date="2026-06-22")

    def test_raises_on_invalid_amount_format(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-01-15"],
            "amount": ["not a number"],
        })
        with pytest.raises(ValueError, match="Некорректный числовой формат"):
            BusinessMetrics(data=df, reference_date="2026-06-22")

    def test_client_id_stored_as_string(self):
        df = pd.DataFrame({
            "client_id": [1234567890, 9876543210],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert metrics["client_id"].dtype == "string" or metrics["client_id"].dtype == object
        assert metrics["client_id"].iloc[0] == "1234567890"


class TestBusinessMetricsEdgeCases:
    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert len(metrics) == 0
        assert list(metrics.columns) == [
            "client_id", "last_purchase_date", "recency", "frequency",
            "total_amount", "avg_amount", "monetary_score"
        ]

    def test_last_purchase_date_is_max_effective_date(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79991234567"],
            "purchase_date": ["2026-01-15", "2026-06-01"],
            "amount": [100.50, 200.75],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        last_date = metrics.set_index("client_id")["last_purchase_date"]
        assert last_date["+79991234567"] == pd.Timestamp("2026-06-01")

    def test_future_purchase_clipped_in_last_purchase_date(self):
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-07-01"],
            "amount": [100.50],
        })
        bm = BusinessMetrics(data=df, reference_date="2026-06-22")
        metrics = bm.metrics()
        
        assert metrics["last_purchase_date"].iloc[0] == pd.Timestamp("2026-06-22")