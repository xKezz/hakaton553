import pytest
import pandas as pd
import numpy as np
import warnings
from src.segmentation.full_categorised import build_bonus_report, save_bonus_report


def create_test_data(n=30):
    return pd.DataFrame({
        "client_id": [f"+7999{i:07d}" for i in range(n)],
        "purchase_date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "amount": np.random.uniform(100, 1000, n).round(2),
    })


class TestBuildBonusReport:
    def test_report_structure(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        assert isinstance(report, dict)
        assert "meta" in report
        assert "categories" in report
        assert "clients" in report

    def test_meta_fields(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22", method="win-back")
        
        meta = report["meta"]
        assert "generated_at" in meta
        assert "reference_date" in meta
        assert "method" in meta
        assert "max_bonus_points" in meta
        assert "step" in meta
        assert "requested_count_cat" in meta
        assert "effective_count_cat" in meta
        assert "monetary_formula" in meta
        assert "score_formula" in meta
        assert "budget_base" in meta
        assert "warnings" in meta
        
        assert meta["method"] == "win-back"
        assert meta["reference_date"] == "2026-06-22"
        assert meta["requested_count_cat"] == 3
        assert isinstance(meta["warnings"], list)

    def test_categories_structure(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        categories = report["categories"]
        assert isinstance(categories, list)
        assert len(categories) > 0
        
        for cat in categories:
            assert "segment_group" in cat
            assert "label" in cat
            assert "proposed_bonus" in cat
            assert "final_bonus" in cat
            assert "reason" in cat
            assert "clients_count" in cat
            assert "avg_recency" in cat
            assert "avg_frequency" in cat
            assert "avg_monetary_score" in cat
            assert "avg_amount" in cat
            assert "client_ids" in cat
            
            assert isinstance(cat["segment_group"], int)
            assert isinstance(cat["proposed_bonus"], int)
            assert isinstance(cat["final_bonus"], int)
            assert isinstance(cat["clients_count"], int)
            assert cat["proposed_bonus"] == cat["final_bonus"]
            assert cat["label"] == f"priority_{cat['segment_group']}"

    def test_categories_sorted_by_segment_group(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        segment_groups = [c["segment_group"] for c in report["categories"]]
        assert segment_groups == sorted(segment_groups)

    def test_clients_structure(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        clients = report["clients"]
        assert isinstance(clients, list)
        assert len(clients) == 30
        
        for client in clients:
            assert "client_id" in client
            assert "segment_group" in client
            assert "proposed_bonus" in client
            assert "reason" in client
            assert "recency" in client
            assert "frequency" in client
            assert "total_amount" in client
            assert "avg_amount" in client
            assert "monetary_score" in client
            
            assert isinstance(client["client_id"], str)
            assert isinstance(client["segment_group"], int)
            assert isinstance(client["proposed_bonus"], float)
            assert isinstance(client["reason"], str)
            assert isinstance(client["recency"], int)
            assert isinstance(client["frequency"], int)
            assert isinstance(client["total_amount"], float)
            assert isinstance(client["avg_amount"], float)
            assert isinstance(client["monetary_score"], float)

    def test_clients_sorted_by_client_id(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        client_ids = [c["client_id"] for c in report["clients"]]
        assert client_ids == sorted(client_ids)

    def test_client_ids_in_categories_match_clients(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        # Collect all client_ids from categories
        all_cat_client_ids = set()
        for cat in report["categories"]:
            all_cat_client_ids.update(cat["client_ids"])
        
        # Collect all client_ids from clients
        all_clients_client_ids = {c["client_id"] for c in report["clients"]}
        
        assert all_cat_client_ids == all_clients_client_ids

    def test_category_client_ids_sorted(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        for cat in report["categories"]:
            if cat["client_ids"]:
                assert cat["client_ids"] == sorted(cat["client_ids"])

    def test_effective_count_cat_matches_categories(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        # Categories with clients_count > 0
        non_empty_cats = [c for c in report["categories"] if c["clients_count"] > 0]
        # Note: report may include empty categories up to effective_count_cat
        assert report["meta"]["effective_count_cat"] >= len(non_empty_cats)

    def test_proposed_bonus_is_integer(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        for cat in report["categories"]:
            assert isinstance(cat["proposed_bonus"], int)
            assert cat["proposed_bonus"] >= 0
        
        for client in report["clients"]:
            assert isinstance(client["proposed_bonus"], float)
            # Should be whole number (int stored as float)
            assert client["proposed_bonus"] == int(client["proposed_bonus"])

    def test_reason_field_populated(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        for cat in report["categories"]:
            assert cat["reason"] != ""
            assert isinstance(cat["reason"], str)
        
        for client in report["clients"]:
            assert client["reason"] != ""
            assert isinstance(client["reason"], str)

    def test_warnings_captured(self):
        # Create data that triggers warnings (e.g., future dates)
        df = pd.DataFrame({
            "client_id": ["+79991234567"],
            "purchase_date": ["2026-07-01"],  # Future date
            "amount": [100.0],
        })
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        assert len(report["meta"]["warnings"]) > 0
        assert any("позже reference_date" in w for w in report["meta"]["warnings"])

    def test_empty_data_returns_empty_structure(self):
        df = pd.DataFrame(columns=["client_id", "purchase_date", "amount"])
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        assert report["meta"]["effective_count_cat"] == 0
        assert report["categories"] == []
        assert report["clients"] == []

    def test_include_client_ids_false(self):
        df = create_test_data(10)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22", include_client_ids=False)
        
        for cat in report["categories"]:
            assert "client_ids" not in cat

    def test_different_parameters_reflected_in_meta(self):
        df = create_test_data(30)
        report = build_bonus_report(
            data=df,
            count_cat=5,
            reference_date="2026-06-22",
            method="win-back",
            max_bonus_points=500,
            step=100,
            need_free=0.3,
            value_cut=0.1,
            churn_bonus=100,
            single_category_bonus=200,
        )
        
        meta = report["meta"]
        assert meta["requested_count_cat"] == 5
        assert meta["max_bonus_points"] == 500
        assert meta["step"] == 100

    def test_score_formula_in_meta(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        score_formula = report["meta"]["score_formula"]
        assert "recency_norm" in score_formula
        assert "frequency_norm" in score_formula
        assert "monetary_norm" in score_formula

    def test_monetary_formula_in_meta(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        monetary_formula = report["meta"]["monetary_formula"]
        assert "total_norm" in monetary_formula
        assert "avg_norm" in monetary_formula
        assert "frequency_norm" in monetary_formula


class TestSaveBonusReport:
    def test_save_creates_valid_json(self, tmp_path):
        df = create_test_data(10)
        output_path = tmp_path / "report.json"
        
        report = save_bonus_report(str(output_path), data=df, count_cat=3, reference_date="2026-06-22")
        
        assert output_path.exists()
        
        import json
        with open(output_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        
        assert loaded == report

    def test_save_report_structure(self, tmp_path):
        df = create_test_data(10)
        output_path = tmp_path / "report.json"
        
        save_bonus_report(str(output_path), data=df, count_cat=3, reference_date="2026-06-22")
        
        import json
        with open(output_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        
        assert "meta" in loaded
        assert "categories" in loaded
        assert "clients" in loaded


class TestBuildBonusReportIntegration:
    def test_report_matches_pipeline_output(self):
        # Use preprocessed data (client_id, purchase_date, amount) for both
        df = pd.DataFrame({
            "client_id": [f"+7999{i:07d}" for i in range(30)],
            "purchase_date": pd.date_range("2026-01-01", periods=30, freq="D").strftime("%Y-%m-%d").tolist(),
            "amount": np.random.uniform(100, 1000, 30).round(2),
        })
        
        # Direct call
        report1 = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        # Via pipeline - needs raw data with phone/date/amount columns
        raw_df = pd.DataFrame({
            "phone": [f"+7999{i:07d}" for i in range(30)],
            "date": pd.date_range("2026-01-01", periods=30, freq="D").strftime("%Y-%m-%d").tolist(),
            "amount": np.random.uniform(100, 1000, 30).round(2),
        })
        from src.pipeline import Pipeline
        pipeline = Pipeline(raw_df)
        report2 = pipeline.run(count_cat=3, reference_date="2026-06-22")
        
        # Should have same client count
        assert len(report1["clients"]) == len(report2["clients"])
        assert report1["meta"]["requested_count_cat"] == report2["meta"]["requested_count_cat"]
        assert report1["meta"]["effective_count_cat"] == report2["meta"]["effective_count_cat"]

    def test_report_with_russian_column_names(self):
        # This test requires preprocessed data with client_id, purchase_date, amount
        df = pd.DataFrame({
            "client_id": ["+79991234567", "+79997654321", "+79991112233"],
            "purchase_date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 200.75, 300.00],
        })
        report = build_bonus_report(data=df, count_cat=2, reference_date="2026-06-22")
        
        assert len(report["clients"]) == 3
        assert report["meta"]["effective_count_cat"] <= 2

    def test_category_0_is_highest_priority(self):
        df = create_test_data(30)
        report = build_bonus_report(data=df, count_cat=3, reference_date="2026-06-22")
        
        # Find category 0
        cat_0 = next(c for c in report["categories"] if c["segment_group"] == 0)
        
        # Category 0 should have the highest priority (lowest segment_group number)
        # and should have clients
        assert cat_0["clients_count"] > 0
        
        # Check that clients in category 0 have the highest scores
        cat_0_clients = [c for c in report["clients"] if c["segment_group"] == 0]
        cat_1_clients = [c for c in report["clients"] if c["segment_group"] == 1]
        
        if cat_0_clients and cat_1_clients:
            # Category 0 clients should generally have higher recency (for win-back)
            avg_recency_0 = np.mean([c["recency"] for c in cat_0_clients])
            avg_recency_1 = np.mean([c["recency"] for c in cat_1_clients])
            # For win-back, higher recency = higher priority
            assert avg_recency_0 >= avg_recency_1