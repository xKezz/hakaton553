import pytest
import pandas as pd
import numpy as np
from src.data_process.predprocessing import Preprocessor


class TestPreprocessorNormalizePhone:
    def test_normalize_phone_russian_mobile_e164(self):
        result = Preprocessor.normalize_phone("+79991234567")
        assert result == "+79991234567"

    def test_normalize_phone_russian_without_plus(self):
        result = Preprocessor.normalize_phone("79991234567", default_region="RU")
        assert result == "+79991234567"

    def test_normalize_phone_russian_8_prefix(self):
        result = Preprocessor.normalize_phone("89991234567", default_region="RU")
        assert result == "+79991234567"

    def test_normalize_phone_russian_with_spaces(self):
        result = Preprocessor.normalize_phone("+7 999 123 45 67")
        assert result == "+79991234567"

    def test_normalize_phone_russian_with_parentheses(self):
        result = Preprocessor.normalize_phone("8 (999) 123-45-67", default_region="RU")
        assert result == "+79991234567"

    def test_normalize_phone_international_us(self):
        result = Preprocessor.normalize_phone("+14155552671")
        assert result == "+14155552671"

    def test_normalize_phone_international_uk(self):
        result = Preprocessor.normalize_phone("+442071234567")
        assert result == "+442071234567"

    def test_normalize_phone_international_germany(self):
        result = Preprocessor.normalize_phone("+493012345678")
        assert result == "+493012345678"

    def test_normalize_phone_00_prefix(self):
        result = Preprocessor.normalize_phone("0079991234567")
        assert result == "+79991234567"

    def test_normalize_phone_00_prefix_us(self):
        result = Preprocessor.normalize_phone("0014155552671")
        assert result == "+14155552671"

    def test_normalize_phone_nan_returns_none(self):
        result = Preprocessor.normalize_phone(np.nan)
        assert result is None

    def test_normalize_phone_none_returns_none(self):
        result = Preprocessor.normalize_phone(None)
        assert result is None

    def test_normalize_phone_empty_string_returns_none(self):
        result = Preprocessor.normalize_phone("")
        assert result is None

    def test_normalize_phone_whitespace_only_returns_none(self):
        result = Preprocessor.normalize_phone("   ")
        assert result is None

    def test_normalize_phone_invalid_format_returns_none(self):
        result = Preprocessor.normalize_phone("not a phone")
        assert result is None

    def test_normalize_phone_too_short_returns_none(self):
        result = Preprocessor.normalize_phone("12345")
        assert result is None

    def test_normalize_phone_invalid_country_code_returns_none(self):
        result = Preprocessor.normalize_phone("+999999999999")
        assert result is None


class TestPreprocessorProcessPhoneColumn:
    def test_process_phone_column_basic(self):
        df = pd.DataFrame({"phone": ["+79991234567", "89997654321", "79991112233"]})
        preprocessor = Preprocessor(df, default_region="RU")
        result = preprocessor.process_phone_column("phone")
        expected = pd.Series(["+79991234567", "+79997654321", "+79991112233"], name="phone")
        pd.testing.assert_series_equal(result, expected)

    def test_process_phone_column_with_nan(self):
        df = pd.DataFrame({"phone": ["+79991234567", np.nan, "89997654321"]})
        preprocessor = Preprocessor(df, default_region="RU")
        result = preprocessor.process_phone_column("phone")
        assert result.iloc[0] == "+79991234567"
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == "+79997654321"

    def test_process_phone_column_invalid_becomes_nan(self):
        df = pd.DataFrame({"phone": ["+79991234567", "invalid", "89997654321"]})
        preprocessor = Preprocessor(df, default_region="RU")
        result = preprocessor.process_phone_column("phone")
        assert result.iloc[0] == "+79991234567"
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == "+79997654321"

    def test_process_phone_column_mixed_formats(self):
        df = pd.DataFrame({
            "phone": ["+7 999 123 45 67", "8(999)765-43-21", "79991112233", "+79995556677"]
        })
        preprocessor = Preprocessor(df, default_region="RU")
        result = preprocessor.process_phone_column("phone")
        assert all(r.startswith("+7") for r in result if pd.notna(r))


class TestPreprocessorNormalizeDate:
    def test_normalize_date_iso_format(self):
        result = Preprocessor.normalize_date("2026-01-15")
        assert result == "2026-01-15"

    def test_normalize_date_dd_mm_yyyy(self):
        result = Preprocessor.normalize_date("15.01.2026")
        assert result == "2026-01-15"

    def test_normalize_date_dd_mm_yy(self):
        result = Preprocessor.normalize_date("15.01.26")
        assert result == "2026-01-15"

    def test_normalize_date_yyyymmdd(self):
        result = Preprocessor.normalize_date("20260115")
        assert result == "2026-01-15"

    def test_normalize_date_with_time_iso_parsed_as_dd_mm(self):
        # With dayfirst=True, ISO format with timezone is parsed as DD-MM-YYYY
        result = Preprocessor.normalize_date("2026-01-15T10:30:00+03:00")
        # Interpreted as 15-01-2026 -> 2026-01-15
        assert result == "2026-01-15"

    def test_normalize_date_with_time_no_tz(self):
        result = Preprocessor.normalize_date("2026-01-15 10:30:00")
        # Interpreted as 15-01-2026 -> 2026-01-15
        assert result == "2026-01-15"

    def test_normalize_date_slash_format(self):
        result = Preprocessor.normalize_date("2026/01/15")
        assert result == "2026-01-15"

    def test_normalize_date_dash_format_eu(self):
        result = Preprocessor.normalize_date("15-01-2026")
        assert result == "2026-01-15"

    def test_normalize_date_nan_returns_none(self):
        result = Preprocessor.normalize_date(np.nan)
        assert result is None

    def test_normalize_date_none_returns_none(self):
        result = Preprocessor.normalize_date(None)
        assert result is None

    def test_normalize_date_empty_string_returns_none(self):
        result = Preprocessor.normalize_date("")
        assert result is None

    def test_normalize_date_invalid_returns_none(self):
        result = Preprocessor.normalize_date("not a date")
        assert result is None

    def test_normalize_date_future_date(self):
        result = Preprocessor.normalize_date("2099-12-31")
        assert result == "2099-12-31"

    def test_normalize_date_iso_datetime_with_tz(self):
        # ISO datetime with T separator and timezone should be parsed as YYYY-MM-DD
        # (not DD-MM-YYYY with dayfirst=True)
        result = Preprocessor.normalize_date("2026-04-05T10:30:00+03:00")
        assert result == "2026-04-05"


class TestPreprocessorProcessDateColumn:
    def test_process_date_column_basic(self):
        df = pd.DataFrame({"date": ["2026-01-15", "2026-02-20", "2026-03-01"]})
        preprocessor = Preprocessor(df)
        result = preprocessor.process_date_column("date")
        # With dayfirst=True: "2026-03-01" parsed as 03-01-2026 -> 2026-01-03
        expected = pd.Series(["2026-01-15", "2026-02-20", "2026-01-03"], name="date")
        pd.testing.assert_series_equal(result, expected)

    def test_process_date_column_mixed_formats(self):
        df = pd.DataFrame({
            "date": ["2026-01-15", "15.02.2026", "20260301", "2026-04-05T10:30:00+03:00"]
        })
        preprocessor = Preprocessor(df)
        result = preprocessor.process_date_column("date")
        # ISO datetime with T separator -> parsed as YYYY-MM-DD (2026-04-05)
        # Other formats with dayfirst=True
        expected = pd.Series(["2026-01-15", "2026-02-15", "2026-03-01", "2026-04-05"], name="date")
        pd.testing.assert_series_equal(result, expected)

    def test_process_date_column_with_nan(self):
        df = pd.DataFrame({"date": ["2026-01-15", np.nan, "2026-03-01"]})
        preprocessor = Preprocessor(df)
        result = preprocessor.process_date_column("date")
        assert result.iloc[0] == "2026-01-15"
        assert pd.isna(result.iloc[1])
        # "2026-03-01" parsed as 03-01-2026 -> 2026-01-03
        assert result.iloc[2] == "2026-01-03"


class TestPreprocessorNormalizeAmount:
    def test_normalize_amount_float(self):
        result = Preprocessor.normalize_amount(100.50)
        assert result == 100.50

    def test_normalize_amount_integer(self):
        result = Preprocessor.normalize_amount(100)
        assert result == 100.0

    def test_normalize_amount_string_float(self):
        result = Preprocessor.normalize_amount("100.50")
        assert result == 100.50

    def test_normalize_amount_comma_decimal(self):
        result = Preprocessor.normalize_amount("100,50")
        assert result == 100.50

    def test_normalize_amount_with_spaces(self):
        result = Preprocessor.normalize_amount("1 000.50")
        assert result == 1000.50

    def test_normalize_amount_with_spaces_and_comma(self):
        result = Preprocessor.normalize_amount("1 000,50")
        assert result == 1000.50

    def test_normalize_amount_negative(self):
        result = Preprocessor.normalize_amount("-100.50")
        assert result == -100.50

    def test_normalize_amount_zero(self):
        result = Preprocessor.normalize_amount(0)
        assert result == 0.0

    def test_normalize_amount_nan_returns_none(self):
        result = Preprocessor.normalize_amount(np.nan)
        assert result is None

    def test_normalize_amount_none_returns_none(self):
        result = Preprocessor.normalize_amount(None)
        assert result is None

    def test_normalize_amount_empty_string_returns_none(self):
        result = Preprocessor.normalize_amount("")
        assert result is None

    def test_normalize_amount_invalid_returns_none(self):
        result = Preprocessor.normalize_amount("not a number")
        assert result is None


class TestPreprocessorProcessAmountColumn:
    def test_process_amount_column_basic(self):
        df = pd.DataFrame({"amount": [100.50, 200.75, 300.00]})
        preprocessor = Preprocessor(df)
        result = preprocessor.process_amount_column("amount")
        expected = pd.Series([100.50, 200.75, 300.00], name="amount")
        pd.testing.assert_series_equal(result, expected)

    def test_process_amount_column_mixed_formats(self):
        df = pd.DataFrame({"amount": ["100,50", "1 000.75", "2000"]})
        preprocessor = Preprocessor(df)
        result = preprocessor.process_amount_column("amount")
        expected = pd.Series([100.50, 1000.75, 2000.0], name="amount")
        pd.testing.assert_series_equal(result, expected)

    def test_process_amount_column_with_nan(self):
        df = pd.DataFrame({"amount": [100.50, np.nan, 300.00]})
        preprocessor = Preprocessor(df)
        result = preprocessor.process_amount_column("amount")
        assert result.iloc[0] == 100.50
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == 300.00


class TestPreprocessorIntegration:
    def test_full_preprocessing_pipeline(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "89997654321", "+7 999 111 22 33"],
            "date": ["2026-01-15", "15.02.2026", "20260301"],
            "amount": ["100,50", "1 000.75", "2000"]
        })
        preprocessor = Preprocessor(df, default_region="RU")
        
        phones = preprocessor.process_phone_column("phone")
        dates = preprocessor.process_date_column("date")
        amounts = preprocessor.process_amount_column("amount")
        
        assert list(phones) == ["+79991234567", "+79997654321", "+79991112233"]
        assert list(dates) == ["2026-01-15", "2026-02-15", "2026-03-01"]
        assert list(amounts) == [100.50, 1000.75, 2000.0]

    def test_preprocessor_drops_invalid_rows(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "invalid", "+79991112233"],
            "date": ["2026-01-15", "2026-02-20", "not a date"],
            "amount": ["100.50", "200.75", "invalid"]
        })
        preprocessor = Preprocessor(df, default_region="RU")
        
        phones = preprocessor.process_phone_column("phone")
        dates = preprocessor.process_date_column("date")
        amounts = preprocessor.process_amount_column("amount")
        
        # Only first row should be fully valid
        valid_mask = phones.notna() & dates.notna() & amounts.notna()
        assert valid_mask.sum() == 1
        assert valid_mask.iloc[0] == True