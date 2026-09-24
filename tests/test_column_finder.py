import pandas as pd
import pytest

from src.data_process.loader import ColumnFinder


# ============================================================
# PHONE
# ============================================================

@pytest.mark.parametrize(
    "phone_values",
    [
        # Обычные российские номера
        [
            "+79991234567",
            "+79995550102",
            "+79998887766"
        ],

        # Телефон с пробелами, скобками и дефисами
        [
            "+7 (999) 123-45-67",
            "+7 (999) 555-01-02",
            "+7 (999) 888-77-66"
        ],

        # Номера без "+"
        [
            "79991234567",
            "79995550102",
            "79998887766"
        ],

        # Международные номера
        [
            "+49 151 23456789",
            "+44 20 7946 0958",
            "+33 6 12 34 56 78"
        ],

        # Смешанные форматы
        [
            "+79991234567",
            "8 (999) 555-01-02",
            "+49 151 23456789",
            "+7 999 888-77-66"
        ],

        # Есть пропуски
        [
            "+79991234567",
            None,
            "+79995550102",
            None
        ]
    ]
)
def test_phone_column_with_different_data(phone_values):
    df = pd.DataFrame({
        "client_id": range(len(phone_values)),
        "phone": phone_values,
        "customer_number": ["123456"] * len(phone_values),
        "purchase_date": ["2026-09-01"] * len(phone_values),
        "amount": [100] * len(phone_values)
    })

    finder = ColumnFinder(df)

    assert finder.find_phone_column() == "phone"


def test_phone_column_wins_over_customer_number():
    df = pd.DataFrame({
        "customer_number": [
            "12345678901",
            "12345678902",
            "12345678903"
        ],
        "PhoneNumber": [
            "+79991234567",
            "+79995550102",
            "+79998887766"
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_phone_column() == "PhoneNumber"


def test_phone_column_with_some_invalid_values():
    df = pd.DataFrame({
        "phone": [
            "+79991234567",
            "abc",
            "+79995550102",
            None,
            "+79998887766"
        ],
        "customer_number": [
            "123456",
            "234567",
            "345678",
            "456789",
            "567890"
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_phone_column() == "phone"


# ============================================================
# DATE
# ============================================================

@pytest.mark.parametrize(
    "date_values",
    [
        # ISO
        [
            "2026-09-01",
            "2026-09-02",
            "2026-09-03"
        ],

        # DD.MM.YYYY
        [
            "01.09.2026",
            "02.09.2026",
            "03.09.2026"
        ],

        # YYYYMMDD
        [
            "20260901",
            "20260902",
            "20260903"
        ],

        # ISO с временем
        [
            "2026-09-01T08:42:00",
            "2026-09-02T09:15:00",
            "2026-09-03T10:30:00"
        ],

        # ISO с timezone
        [
            "2026-09-01T08:42:00+03:00",
            "2026-09-02T09:15:00+03:00",
            "2026-09-03T10:30:00+03:00"
        ],

        # Смешанные форматы
        [
            "2026-09-01",
            "02.09.2026",
            "20260903",
            "2026-09-04T08:42:00+03:00"
        ],

        # Часть значений испорчена
        [
            "2026-09-01",
            "invalid_date",
            "20260903",
            "not_a_date"
        ]
    ]
)
def test_date_column_with_different_data(date_values):
    df = pd.DataFrame({
        "client_id": range(len(date_values)),
        "purchase_date": date_values,
        "amount": [100] * len(date_values)
    })

    finder = ColumnFinder(df)

    assert finder.find_date_column() == "purchase_date"


def test_date_column_selection_between_candidates():
    df = pd.DataFrame({
        "purchase_date": [
            "2026-09-01",
            "invalid",
            "invalid",
            "invalid"
        ],
        "transaction_date": [
            "2026-09-01",
            "2026-09-02",
            "2026-09-03",
            "2026-09-04"
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_date_column() == "transaction_date"


# ============================================================
# AMOUNT
# ============================================================

@pytest.mark.parametrize(
    ("column_name", "amount_values"),
    [
        # Float
        (
            "purchase_amount",
            [390.00, 450.50, 550.25]
        ),

        # Integer
        (
            "purchase_amount",
            [390, 450, 550]
        ),

        # Строки с точкой
        (
            "purchase_amount",
            ["390.00", "450.50", "550.25"]
        ),

        # Строки с запятой
        (
            "purchase_amount",
            ["390,00", "450,50", "550,25"]
        ),

        # Русское название
        (
            "сумма",
            [390, 450, 550]
        ),

        # TotalAmount
        (
            "TotalAmount",
            [390, 450, 550]
        ),

        # total_amount
        (
            "total_amount",
            [390, 450, 550]
        ),

        # Purchase Amount
        (
            "Purchase Amount",
            [390, 450, 550]
        )
    ]
)
def test_amount_column_with_different_data(
    column_name,
    amount_values
):
    df = pd.DataFrame({
        "client_id": range(len(amount_values)),
        column_name: amount_values,
        "purchase_date": [
            "2026-09-01"
        ] * len(amount_values)
    })

    finder = ColumnFinder(df)

    assert finder.find_amount_column() == column_name


def test_amount_column_wins_over_discount():
    df = pd.DataFrame({
        "purchase_amount": [
            390,
            450,
            550
        ],
        "discount_amount": [
            50,
            30,
            20
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_amount_column() == "purchase_amount"


def test_amount_column_wins_over_generic_amount():
    df = pd.DataFrame({
        "purchase_amount": [
            390,
            450,
            550
        ],
        "Amount": [
            390,
            450,
            550
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_amount_column() == "purchase_amount"


def test_amount_column_with_invalid_values():
    df = pd.DataFrame({
        "purchase_amount": [
            "390",
            "invalid",
            "450",
            "550"
        ],
        "discount_amount": [
            "50",
            "30",
            "20",
            "15"
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_amount_column() == "purchase_amount"


# ============================================================
# ALL THREE AT ONCE
# ============================================================

def test_all_columns_are_selected_correctly():
    df = pd.DataFrame({
        "ID": [1001, 1002, 1003],

        "customer_number": [
            "123456",
            "123457",
            "123458"
        ],

        "PhoneNumber": [
            "+7 (999) 123-45-67",
            "+7 (999) 555-01-02",
            "+7 (999) 888-77-66"
        ],

        "purchase_date": [
            "2026-09-01T08:42:00+03:00",
            "02.09.2026",
            "20260903"
        ],

        "transaction_date": [
            "invalid",
            "invalid",
            "invalid"
        ],

        "TotalAmount": [
            "1250.50",
            "850.00",
            "420.00"
        ],

        "discount_amount": [
            "50.00",
            "30.00",
            "20.00"
        ]
    })

    finder = ColumnFinder(df)

    assert finder.find_phone_column() == "PhoneNumber"
    assert finder.find_date_column() == "purchase_date"
    assert finder.find_amount_column() == "TotalAmount"


# ============================================================
# NO REQUIRED COLUMN
# ============================================================

def test_no_phone_column():
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "purchase_date": [
            "2026-09-01",
            "2026-09-02",
            "2026-09-03"
        ],
        "amount": [390, 450, 550]
    })

    finder = ColumnFinder(df)

    with pytest.raises(ValueError):
        finder.find_phone_column()


def test_no_date_column():
    df = pd.DataFrame({
        "phone": [
            "+79991234567",
            "+79995550102",
            "+79998887766"
        ],
        "amount": [390, 450, 550]
    })

    finder = ColumnFinder(df)

    with pytest.raises(ValueError):
        finder.find_date_column()


def test_no_amount_column():
    df = pd.DataFrame({
        "phone": [
            "+79991234567",
            "+79995550102",
            "+79998887766"
        ],
        "purchase_date": [
            "2026-09-01",
            "2026-09-02",
            "2026-09-03"
        ]
    })

    finder = ColumnFinder(df)

    with pytest.raises(ValueError):
        finder.find_amount_column()