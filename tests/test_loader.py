import pandas as pd
import pytest

from src.data_process.loader import ColumnFinder


def test_normalize_column_name():
    assert ColumnFinder.normalize_column_name("Purchase ID") == "purchaseid"
    assert ColumnFinder.normalize_column_name("Номер телефона") == "номертелефона"


def test_find_columns():
    df = pd.DataFrame(
        {
            "purchase_id": ["1", "2"],
            "phone": ["+79990000001", "+79990000002"],
            "purchase_date": ["2026-09-01", "2026-09-02"],
            "amount": ["100", "200"],
        }
    )

    finder = ColumnFinder(df)

    assert finder.find_columns(["phone"]) == ["phone"]
    assert finder.find_columns(["purchase_id"]) == ["purchase_id"]


def test_find_purchase_id_column():
    df = pd.DataFrame(
        {
            "transaction_id": ["1001", "1002", "1003"],
            "phone": ["+79990000001", "+79990000002", "+79990000003"],
            "purchase_date": ["2026-09-01"] * 3,
            "amount": [100, 200, 300],
        }
    )

    assert ColumnFinder(df).find_purchase_id_column() == "transaction_id"


def test_find_phone_column():
    df = pd.DataFrame(
        {
            "transaction_number": ["1001", "1002", "1003"],
            "phone": ["+79990000001", "+79990000002", "+79990000003"],
        }
    )

    assert ColumnFinder(df).find_phone_column() == "phone"


def test_find_date_column():
    df = pd.DataFrame(
        {
            "purchase_id": ["1", "2", "3"],
            "date": ["2026-09-01", "2026-09-02", "2026-09-03"],
        }
    )

    assert ColumnFinder(df).find_date_column() == "date"


def test_find_amount_column():
    df = pd.DataFrame(
        {
            "purchase_id": ["1", "2"],
            "amount": ["100,50", "200,00"],
            "discount": ["10", "20"],
        }
    )

    assert ColumnFinder(df).find_amount_column() == "amount"


def test_missing_column_raises():
    df = pd.DataFrame({"phone": ["+79990000001"]})

    with pytest.raises(ValueError):
        ColumnFinder(df).find_purchase_id_column()
