import pandas as pd
import phonenumbers
from loader import ColumnFinder

class Preprocessor:

    def __init__(self, df: pd.DataFrame, default_region: str = "RU"):
        self.df = df
        self.default_region = default_region

    @staticmethod
    def normalize_phone(
        phone: str,
        default_region: str = "RU"
    ) -> str | None:
        """
        Приводит номер телефона к международному формату E.164.

        Возвращает:
            str | None: нормализованный номер или None,
            если номер некорректный.
        """
        if pd.isna(phone):
            return None

        phone = str(phone).strip()

        if not phone:
            return None

        try:
            if phone.startswith("00"):
                phone = "+" + phone[2:]

            if phone.startswith("+"):
                number = phonenumbers.parse(phone, None)
            else:
                number = phonenumbers.parse(phone, default_region)

            if not phonenumbers.is_valid_number(number):
                return None

            return phonenumbers.format_number(
                number,
                phonenumbers.PhoneNumberFormat.E164
            )

        except phonenumbers.NumberParseException:
            return None

    def process_phone_column(self, column: str) -> pd.Series:
        """
        Нормализует значения указанной колонки с телефонами.

        Возвращает:
            pd.Series: колонка с номерами в формате E.164.
        """
        return self.df[column].apply(
            self.normalize_phone,
            default_region=self.default_region
        )

    @staticmethod
    def normalize_date(date: str) -> str | None:
        """
        Приводит дату к формату YYYY-MM-DD.

        Возвращает:
            str | None: нормализованную дату или None,
            если значение не удалось распознать.
        """
        if pd.isna(date):
            return None

        date = str(date).strip()

        if not date:
            return None

        if date.isdigit() and len(date) == 8:
            parsed_date = pd.to_datetime(
                date,
                format="%Y%m%d",
                errors="coerce"
            )
        else:
            parsed_date = pd.to_datetime(
                date,
                format="mixed",
                dayfirst=True,
                errors="coerce"
            )

        if pd.isna(parsed_date):
            return None

        return parsed_date.strftime("%Y-%m-%d")

    def process_date_column(self, column: str) -> pd.Series:
        """
        Нормализует значения указанной колонки с датами.

        Возвращает:
            pd.Series: колонка с датами в формате YYYY-MM-DD.
        """
        return self.df[column].apply(self.normalize_date)

    @staticmethod
    def normalize_amount(amount) -> float | None:
        """
        Приводит сумму покупки к числовому типу.

        Возвращает:
            float | None: сумма или None,
            если значение не удалось преобразовать.
        """
        if pd.isna(amount):
            return None

        amount = str(amount).strip()

        if not amount:
            return None

        amount = amount.replace(" ", "").replace(",", ".")

        try:
            return float(amount)
        except ValueError:
            return None

    def process_amount_column(self, column: str) -> pd.Series:
        """
        Нормализует значения указанной колонки с суммами.

        Возвращает:
            pd.Series: колонка с числовыми значениями суммы.
        """
        return self.df[column].apply(self.normalize_amount)


if __name__ == "__main__":
    from loader import ColumnFinder

    df = pd.read_csv(
        "../../data/source_files/for_predprocess.csv"
    )

    finder = ColumnFinder(df)

    phone_column = finder.find_phone_column()
    date_column = finder.find_date_column()
    amount_column = finder.find_amount_column()

    processor = Preprocessor(df)

    normalized_phone = processor.process_phone_column(
        phone_column
    )

    normalized_date = processor.process_date_column(
        date_column
    )

    normalized_amount = processor.process_amount_column(
        amount_column
    )

    processed_df = pd.DataFrame({
        "client_id": normalized_phone,
        "purchase_date": normalized_date,
        "amount": normalized_amount
    })

    processed_df.to_csv(
        "../../data/processed_files/processed_data.csv",
        index=False
    )

    print(processed_df)