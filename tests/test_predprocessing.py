import pandas as pd
import phonenumbers
import pytest

from src.data_process.predprocessing import Preprocessor


MAX_SUPPORTED_REGIONS = [
    "BY",
    "AZ",
    "AM",
    "KZ",
    "KG",
    "MD",
    "TJ",
    "UZ",
    "GE",
    "ID",
    "MY",
    "TH",
    "TR",
    "TM",
    "AE",
    "LA",
    "CU",
    "VN",
    "KH",
    "AF",
    "BO",
    "CO",
    "GD",
    "GM",
    "IN",
    "IQ",
    "KN",
    "KW",
    "LB",
    "MM",
    "NI",
    "PK",
    "PW",
    "QA",
    "SA",
    "VE",
    "TZ",
    "CD",
    "CG",
    "EG",
    "CN",
    "ZA",
    "BR"
]


@pytest.mark.parametrize("region", MAX_SUPPORTED_REGIONS)
def test_normalize_phone_for_max_supported_country(region):
    number = phonenumbers.example_number(region)

    assert number is not None

    expected = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.E164
    )

    raw_phone = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )

    result = Preprocessor.normalize_phone(
        raw_phone,
        default_region=region
    )

    assert result == expected


@pytest.mark.parametrize("region", MAX_SUPPORTED_REGIONS)
def test_normalize_national_phone_for_max_supported_country(region):
    number = phonenumbers.example_number(region)

    assert number is not None

    expected = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.E164
    )

    raw_phone = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.NATIONAL
    )

    result = Preprocessor.normalize_phone(
        raw_phone,
        default_region=region
    )

    assert result == expected


@pytest.mark.parametrize("region", MAX_SUPPORTED_REGIONS)
def test_normalize_00_phone_for_max_supported_country(region):
    number = phonenumbers.example_number(region)

    assert number is not None

    expected = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.E164
    )

    raw_phone = phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )

    raw_phone = "00" + raw_phone[1:]

    result = Preprocessor.normalize_phone(
        raw_phone,
        default_region=region
    )

    assert result == expected


def test_normalize_invalid_phone():
    result = Preprocessor.normalize_phone(
        "not_a_phone",
        default_region="RU"
    )

    assert result is None


def test_normalize_empty_phone():
    result = Preprocessor.normalize_phone(
        "",
        default_region="RU"
    )

    assert result is None


def test_normalize_none_phone():
    result = Preprocessor.normalize_phone(
        None,
        default_region="RU"
    )

    assert result is None


def test_process_phone_column():
    df = pd.DataFrame({
        "phone": [
            "+7 (999) 123-45-67",
            "+49 151 23456789",
            "+44 20 8366 1177"
        ]
    })

    processor = Preprocessor(df)

    result = processor.process_phone_column("phone")

    assert result.tolist() == [
        "+79991234567",
        "+4915123456789",
        "+442083661177"
    ]