import pytest
import pandas as pd
import numpy as np
from src.data_process.loader import ColumnFinder


class TestColumnFinderNormalize:
    def test_normalize_column_name_basic(self):
        assert ColumnFinder.normalize_column_name("Phone") == "phone"
        assert ColumnFinder.normalize_column_name("phone") == "phone"
        assert ColumnFinder.normalize_column_name("PHONE") == "phone"

    def test_normalize_column_name_with_spaces_and_underscores(self):
        assert ColumnFinder.normalize_column_name("phone number") == "phonenumber"
        assert ColumnFinder.normalize_column_name("phone_number") == "phonenumber"
        assert ColumnFinder.normalize_column_name("Phone Number") == "phonenumber"
        assert ColumnFinder.normalize_column_name("phone-number") == "phonenumber"

    def test_normalize_column_name_special_chars(self):
        assert ColumnFinder.normalize_column_name("phone@#$number") == "phonenumber"
        assert ColumnFinder.normalize_column_name("телефон_клиента") == "телефонклиента"


class TestColumnFinderFindColumns:
    def test_find_columns_single_match(self):
        df = pd.DataFrame({"phone": [1, 2], "email": [3, 4]})
        finder = ColumnFinder(df)
        result = finder.find_columns(["phone"])
        assert result == ["phone"]

    def test_find_columns_multiple_matches(self):
        df = pd.DataFrame({"phone": [1, 2], "mobile_number": [3, 4], "email": [5, 6]})
        finder = ColumnFinder(df)
        result = finder.find_columns(["phone", "mobile"])
        assert set(result) == {"phone", "mobile_number"}

    def test_find_columns_no_match(self):
        df = pd.DataFrame({"email": [1, 2], "address": [3, 4]})
        finder = ColumnFinder(df)
        result = finder.find_columns(["phone"])
        assert result == []

    def test_find_columns_case_insensitive(self):
        df = pd.DataFrame({"Phone": [1, 2], "MOBILE": [3, 4]})
        finder = ColumnFinder(df)
        result = finder.find_columns(["phone"])
        assert "Phone" in result

    def test_find_columns_partial_match(self):
        df = pd.DataFrame({"client_phone_number": [1, 2]})
        finder = ColumnFinder(df)
        result = finder.find_columns(["phone"])
        assert "client_phone_number" in result


class TestColumnFinderPhone:
    def test_find_phone_column_exact_match(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result == "phone"

    def test_find_phone_column_russian_keyword(self):
        df = pd.DataFrame({
            "телефон": ["+79991234567", "+79997654321"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result == "телефон"

    def test_find_phone_column_multiple_candidates_picks_best(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "mobile": ["+79991111111", "+79992222222"],
            "contact_number": ["123", "456"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result in ["phone", "mobile"]

    def test_find_phone_column_validates_phone_format(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "bad_phone": ["123", "abc"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result == "phone"

    def test_find_phone_column_international_formats(self):
        df = pd.DataFrame({
            "phone": ["+14155552671", "+442071234567", "+493012345678"],
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result == "phone"

    def test_find_phone_column_various_formats(self):
        df = pd.DataFrame({
            "phone": ["89991234567", "79991234567", "+7 999 123 45 67", "8 (999) 123-45-67"],
        })
        finder = ColumnFinder(df)
        result = finder.find_phone_column()
        assert result == "phone"

    def test_find_phone_column_no_valid_phone_raises(self):
        df = pd.DataFrame({
            "phone": ["abc", "def"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        # When no valid phones, should raise ValueError
        with pytest.raises(ValueError, match="нет валидных данных"):
            finder.find_phone_column()

    def test_find_phone_column_empty_dataframe_raises(self):
        df = pd.DataFrame({"phone": pd.Series([], dtype=str), "email": pd.Series([], dtype=str)})
        finder = ColumnFinder(df)
        # Empty dataframe should raise ValueError
        with pytest.raises(ValueError, match="нет валидных данных"):
            finder.find_phone_column()


class TestColumnFinderDate:
    def test_find_date_column_exact_match(self):
        df = pd.DataFrame({
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "purchase_date"

    def test_find_date_column_russian_keywords(self):
        df = pd.DataFrame({
            "дата покупки": ["2026-01-15", "2026-02-20"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "дата покупки"

    def test_find_date_column_various_formats(self):
        df = pd.DataFrame({
            "date": ["2026-01-15", "15.02.2026", "20260301", "2026-04-05T10:30:00+03:00"],
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "date"

    def test_find_date_column_yyyymmdd_format(self):
        df = pd.DataFrame({
            "date": ["20260115", "20260220", "20260301"],
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "date"

    def test_find_date_column_priority_keywords(self):
        df = pd.DataFrame({
            "date": ["2026-01-15", "2026-02-20"],
            "order_date": ["2026-01-16", "2026-02-21"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "order_date"

    def test_find_date_column_invalid_dates_low_score(self):
        df = pd.DataFrame({
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "bad_date": ["not a date", "also not"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_date_column()
        assert result == "purchase_date"

    def test_find_date_column_no_valid_dates_raises(self):
        df = pd.DataFrame({
            "date": ["not a date", "also not"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        with pytest.raises(ValueError, match="нет валидных данных"):
            finder.find_date_column()


class TestColumnFinderAmount:
    def test_find_amount_column_exact_match(self):
        df = pd.DataFrame({
            "amount": [100.50, 200.75],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "amount"

    def test_find_amount_column_russian_keywords(self):
        df = pd.DataFrame({
            "сумма покупки": [100.50, 200.75],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "сумма покупки"

    def test_find_amount_column_comma_decimal(self):
        df = pd.DataFrame({
            "amount": ["100,50", "200,75"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "amount"

    def test_find_amount_column_with_spaces(self):
        df = pd.DataFrame({
            "amount": ["1 000.50", "2 000.75"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "amount"

    def test_find_amount_column_excludes_discount_refund(self):
        df = pd.DataFrame({
            "amount": [100.50, 200.75],
            "discount": [10.0, 20.0],
            "refund": [5.0, 0.0],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "amount"
        assert result != "discount"
        assert result != "refund"

    def test_find_amount_column_russian_exclude_keywords(self):
        df = pd.DataFrame({
            "сумма покупки": [100.50, 200.75],
            "скидка": [10.0, 20.0],
            "возврат": [5.0, 0.0],
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "сумма покупки"

    def test_find_amount_column_priority_keywords(self):
        df = pd.DataFrame({
            "total_amount": [100.50, 200.75],
            "amount": [100.50, 200.75],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "total_amount"

    def test_find_amount_column_no_valid_amount_raises(self):
        df = pd.DataFrame({
            "amount": ["abc", "def"],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        with pytest.raises(ValueError, match="нет валидных данных"):
            finder.find_amount_column()

    def test_find_amount_column_integer_values(self):
        df = pd.DataFrame({
            "amount": [100, 200],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        result = finder.find_amount_column()
        assert result == "amount"


class TestColumnFinderIntegration:
    def test_find_all_three_columns(self):
        df = pd.DataFrame({
            "client_phone": ["+79991234567", "+79997654321"],
            "purchase_date": ["2026-01-15", "2026-02-20"],
            "total_amount": [100.50, 200.75],
            "email": ["a@b.com", "c@d.com"]
        })
        finder = ColumnFinder(df)
        phone = finder.find_phone_column()
        date = finder.find_date_column()
        amount = finder.find_amount_column()
        assert phone == "client_phone"
        assert date == "purchase_date"
        assert amount == "total_amount"

    def test_find_columns_with_extra_irrelevant_columns(self):
        df = pd.DataFrame({
            "phone": ["+79991234567", "+79997654321"],
            "date": ["2026-01-15", "2026-02-20"],
            "amount": [100.50, 200.75],
            "first_name": ["Ivan", "Petr"],
            "last_name": ["Ivanov", "Petrov"],
            "city": ["Moscow", "SPb"],
            "loyalty_id": ["L001", "L002"],
        })
        finder = ColumnFinder(df)
        phone = finder.find_phone_column()
        date = finder.find_date_column()
        amount = finder.find_amount_column()
        assert phone == "phone"
        assert date == "date"
        assert amount == "amount"

    def test_normalize_column_name_cyrillic(self):
        # Normalize removes non-word chars, so "дата покупки" -> "датапокупки"
        assert "дата" in ColumnFinder.normalize_column_name("дата покупки")
        assert "покупки" in ColumnFinder.normalize_column_name("дата покупки")
        assert "телефон" in ColumnFinder.normalize_column_name("номер телефона")
        assert "сумма" in ColumnFinder.normalize_column_name("сумма чека")