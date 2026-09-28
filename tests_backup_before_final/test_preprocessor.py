import pandas as pd

from src.data_process.predprocessing import Preprocessor


def test_normalize_phone_ru_local():
    assert (
        Preprocessor.normalize_phone("89990000001")
        == "+79990000001"
    )


def test_normalize_phone_international():
    assert (
        Preprocessor.normalize_phone("+79990000001")
        == "+79990000001"
    )


def test_normalize_phone_invalid():
    assert Preprocessor.normalize_phone("123") is None
    assert Preprocessor.normalize_phone("") is None


def test_normalize_purchase_id():
    assert (
        Preprocessor.normalize_purchase_id(12345)
        == "12345"
    )
    assert (
        Preprocessor.normalize_purchase_id("12345.0")
        == "12345"
    )
    assert (
        Preprocessor.normalize_purchase_id(" abc-123 ")
        == "abc-123"
    )
    assert (
        Preprocessor.normalize_purchase_id(None)
        is None
    )


def test_normalize_date_iso():
    assert (
        Preprocessor.normalize_date("2026-03-01")
        == "2026-03-01"
    )

    assert (
        Preprocessor.normalize_date(
            "2026-03-01 14:30:00"
        )
        == "2026-03-01"
    )


def test_normalize_amount():
    assert (
        Preprocessor.normalize_amount("1 234,50")
        == 1234.5
    )
    assert (
        Preprocessor.normalize_amount("500")
        == 500.0
    )
    assert (
        Preprocessor.normalize_amount("")
        is None
    )


def test_process_columns():
    df = pd.DataFrame(
        {
            "purchase_id": ["1.0", "2"],
            "phone": [
                "89990000001",
                "+79990000002",
            ],
            "date": [
                "2026-03-01",
                "2026-03-02",
            ],
            "amount": [
                "100,50",
                "200",
            ],
        }
    )

    processor = Preprocessor(df)

    assert (
        processor
        .process_purchase_id_column(
            "purchase_id"
        )
        .tolist()
        == ["1", "2"]
    )

    assert (
        processor
        .process_phone_column("phone")
        .tolist()
        == [
            "+79990000001",
            "+79990000002",
        ]
    )

    assert (
        processor
        .process_date_column("date")
        .tolist()
        == [
            "2026-03-01",
            "2026-03-02",
        ]
    )

    assert (
        processor
        .process_amount_column("amount")
        .tolist()
        == [100.5, 200.0]
    )
