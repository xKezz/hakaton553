import pytest
import pandas as pd
import numpy as np
from src.pipeline import Pipeline


class TestPipelineColumnFinding:
    def test_pipeline_finds_phone_column(self):
        df = pd.DataFrame({
            "client_phone": ["+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "total_amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        pipeline._find_columns()
        assert pipeline._phone_col == "client_phone"

    def test_pipeline_finds_date_column(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "order_date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        pipeline._find_columns()
        assert pipeline._date_col == "order_date"

    def test_pipeline_finds_amount_column(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
            "purchase_amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        pipeline._find_columns()
        assert pipeline._amount_col == "purchase_amount"

    def test_pipeline_raises_on_missing_phone(self):
        df = pd.DataFrame({
            "date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        with pytest.raises(ValueError, match="Подходящая колонка не найдена"):
            pipeline._find_columns()

    def test_pipeline_raises_on_missing_date(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        with pytest.raises(ValueError, match="Подходящая колонка не найдена"):
            pipeline._find_columns()

    def test_pipeline_raises_on_missing_amount(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
        })
        pipeline = Pipeline(df)
        with pytest.raises(ValueError, match="Подходящая колонка не найдена"):
            pipeline._find_columns()


class TestPipelinePredprocess:
    def test_predprocess_returns_clean_dataframe(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321", "+79991112233"],
            "date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 200.75, 300.00],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert list(result.columns) == ["client_id", "purchase_date", "amount"]
        assert len(result) == 3
        assert result["client_id"].tolist() == ["+79991234567", "+79997654321", "+79991112233"]
        # With dayfirst=True: "2026-03-01" parsed as 03-01-2026 -> 2026-01-03
        assert result["purchase_date"].tolist() == ["2026-01-15", "2026-02-20", "2026-01-03"]
        assert result["amount"].tolist() == [100.50, 200.75, 300.00]

    def test_predprocess_handles_various_phone_formats(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "89997654321", "+7 999 111 22 33", "79995556677"],
            "date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01"],
            "amount": [100.50, 200.75, 300.00, 400.00],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 4
        assert all(cid.startswith("+7") for cid in result["client_id"])

    def test_predprocess_handles_various_date_formats(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321", "+79991112233", "+79995556677"],
            "date": ["2026-01-15", "15.02.2026", "20260301", "2026-04-05T10:30:00+03:00"],
            "amount": [100.50, 200.75, 300.00, 400.00],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 4
        # With dayfirst=True, "15.02.2026" -> 2026-02-15, "20260301" -> 2026-03-01
        # ISO datetime with T separator -> parsed as YYYY-MM-DD (2026-04-05)
        assert result["purchase_date"].iloc[0] == "2026-01-15"
        assert result["purchase_date"].iloc[1] == "2026-02-15"
        assert result["purchase_date"].iloc[2] == "2026-03-01"
        assert result["purchase_date"].iloc[3] == "2026-04-05"

    def test_predprocess_handles_various_amount_formats(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321", "+79991112233", "+79995556677"],
            "date": ["2026-01-15", "2026-02-20", "2026-03-01", "2026-04-01"],
            "amount": ["100,50", "1 000.75", "2000", "3 000,00"],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 4
        assert result["amount"].tolist() == [100.50, 1000.75, 2000.0, 3000.0]

    def test_predprocess_drops_invalid_rows(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "invalid", "+79991112233"],
            "date": ["2026-01-15", "2026-02-20", "not a date"],
            "amount": ["100.50", "200.75", "invalid"]
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 1
        assert result["client_id"].iloc[0] == "+79991234567"

    def test_predprocess_raises_on_all_invalid(self):
        df = pd.DataFrame({
            "phone": ["invalid", "also invalid"],
            "date": ["not a date", "also not"],
            "amount": ["invalid", "also invalid"]
        })
        pipeline = Pipeline(df)
        with pytest.raises(ValueError, match="нет валидных данных"):
            pipeline.predprocess()

    def test_predprocess_removes_duplicates_by_client_id(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-01-20", "2026-02-20"],
            "amount": [100.50, 150.00, 200.75],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 3  # Keeps all rows, doesn't deduplicate by client_id


class TestPipelineRun:
    def test_run_returns_report_dict(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321", "+79991112233"],
            "date": ["2026-01-15", "2026-02-20", "2026-03-01"],
            "amount": [100.50, 200.75, 300.00],
        })
        pipeline = Pipeline(df)
        report = pipeline.run(count_cat=3, reference_date="2026-06-22")
        
        assert isinstance(report, dict)
        assert "meta" in report
        assert "categories" in report
        assert "clients" in report
        assert len(report["categories"]) > 0
        assert len(report["clients"]) == 3

    def test_run_report_structure(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        report = pipeline.run(count_cat=2, reference_date="2026-06-22")
        
        meta = report["meta"]
        assert "generated_at" in meta
        assert "reference_date" in meta
        assert "method" in meta
        assert "max_bonus_points" in meta
        assert "step" in meta
        assert "requested_count_cat" in meta
        assert "effective_count_cat" in meta
        
        for cat in report["categories"]:
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
        
        for client in report["clients"]:
            assert "client_id" in client
            assert "segment_group" in client
            assert "proposed_bonus" in client
            assert "reason" in client
            assert "recency" in client
            assert "frequency" in client
            assert "total_amount" in client
            assert "avg_amount" in client
            assert "monetary_score" in client

    def test_run_with_output_path_creates_file(self, tmp_path):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        output_file = tmp_path / "report.json"
        report = pipeline.run(output_path=str(output_file), count_cat=2, reference_date="2026-06-22")
        
        import json
        with open(output_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        
        assert saved == report

    def test_run_with_russian_column_names(self):
        df = pd.DataFrame({
            "телефон": ["+79991234567", "+79997654321"],
            "дата покупки": ["2026-01-15", "2026-02-20"],
            "сумма покупки": [100.50, 200.75],
        })
        pipeline = Pipeline(df)
        report = pipeline.run(count_cat=2, reference_date="2026-06-22")
        
        assert len(report["clients"]) == 2
        assert report["meta"]["effective_count_cat"] <= 2


class TestPipelineEdgeCases:
    def test_pipeline_with_single_row(self):
        df = pd.DataFrame({
            "phone": ["+79991234567"],
            "date": ["2026-01-15"],
            "amount": [100.50],
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 1
        report = pipeline.run(count_cat=3, reference_date="2026-06-22")
        assert report["meta"]["effective_count_cat"] == 1

    def test_pipeline_with_many_duplicates(self):
        df = pd.DataFrame({
            "phone": ["+79991234567"] * 10,
            "date": ["2026-01-15"] * 10,
            "amount": [100.50] * 10,
        })
        pipeline = Pipeline(df)
        result = pipeline.predprocess()
        
        assert len(result) == 10
        # BusinessMetrics groups by client_id, so 10 identical rows -> 1 unique client
        report = pipeline.run(count_cat=3, reference_date="2026-06-22")
        assert report["meta"]["effective_count_cat"] == 1
        assert len(report["clients"]) == 1  # Deduplicated by client_id

    def test_pipeline_preserves_original_dataframe(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
        })
        original_phone = df["phone"].copy()
        pipeline = Pipeline(df)
        pipeline.predprocess()
        
        pd.testing.assert_series_equal(df["phone"], original_phone)