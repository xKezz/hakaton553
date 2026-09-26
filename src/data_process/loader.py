import re

import pandas as pd
from typing import Callable, List


class ColumnFinder:

    PHONE_KEYWORDS = {
        "phone": 10,
        "телефон": 10,
        "номер телефона": 10,
        "mobile": 10,
        "number": 2,
        "номер": 2
    }

    DATE_KEYWORDS = {
        "дата покупки": 10,
        "purchase_date": 10,
        "purchase date": 10,
        "дата транзакции": 9,
        "transaction_date": 9,
        "transaction date": 9,
        "дата продажи": 9,
        "sale_date": 9,
        "sale date": 9,
        "дата заказа": 6,
        "order_date": 6,
        "order date": 6,
        "date": 3,
        "дата": 3
    }

    AMOUNT_KEYWORDS = {
        "сумма покупки": 10,
        "сумма заказа": 10,
        "сумма чека": 10,
        "сумма транзакции": 10,
        "purchase_amount": 10,
        "order_amount": 10,
        "transaction_amount": 10,
        "total_amount": 9,
        "amount": 8,
        "итого": 7,
        "сумма": 6,
        "стоимость": 5,
        "выручка": 4
    }

    AMOUNT_EXCLUDE_KEYWORDS = {
        "discount",
        "скидка",
        "refund",
        "возврат",
        "tax",
        "налог"
    }

    def __init__(self, df):
        self.df = df

    @staticmethod
    def normalize_column_name(name: str) -> str:
        """
        Нормализует название колонки для сравнения.

        Убирает пробелы, подчёркивания и другие разделители.

        Возвращает:
            str: нормализованное название.
        """
        return re.sub(r"[\W_]+", "", name.lower())

    def find_columns(self, keywords: List[str]) -> List[str]:
        """
        Находит колонки, в названии которых содержится
        хотя бы одно из переданных ключевых слов.

        Возвращает:
            List[str]: список подходящих колонок.
        """
        return [
            column
            for column in self.df.columns
            if any(
                self.normalize_column_name(keyword)
                in self.normalize_column_name(column)
                for keyword in keywords
            )
        ]

    def find_best_column(
        self,
        keywords: List[str],
        score_function: Callable[[str], float]
    ) -> str:
        """
        Находит наиболее подходящую колонку среди кандидатов.

        Сначала находит кандидатов по названию,
        затем рассчитывает score для каждой колонки
        и выбирает колонку с максимальным score.

        Возвращает:
            str: название лучшей колонки.
            
        Raises:
            ValueError: если нет кандидатов или лучший score равен 0.
        """

        candidates = self.find_columns(keywords)

        if not candidates:
            raise ValueError("Подходящая колонка не найдена")

        scores = {
            column: score_function(column)
            for column in candidates
        }

        print("Кандидаты:")

        for column, score in scores.items():
            print(f"{column}: {score:.2f}")

        best_column = max(scores, key=scores.get)
        best_score = scores[best_column]
        
        if best_score <= 0:
            raise ValueError("Подходящая колонка не найдена (нет валидных данных)")
        
        return best_column

    def find_phone_column(self) -> str:
        """
        Находит колонку с номерами телефонов.

        Учитывает:
        - ключевые слова в названии колонки;
        - долю значений длиной от 9 до 15 цифр.

        Возвращает:
            str: название найденной колонки.
        """

        def phone_score(column: str) -> float:
            values = self.df[column].dropna().astype(str)

            if values.empty:
                return 0.0

            valid_ratio = (
                values
                .str.replace(r"\D", "", regex=True)
                .str.len()
                .between(9, 15)
                .mean()
            )

            column_name = self.normalize_column_name(column)

            keyword_score = max(
                (
                    score
                    for keyword, score in self.PHONE_KEYWORDS.items()
                    if self.normalize_column_name(keyword) in column_name
                ),
                default=0
            )

            return float(keyword_score * valid_ratio)

        phone_column = self.find_best_column(
            list(self.PHONE_KEYWORDS.keys()),
            phone_score
        )

        print(f"\nКолонка с номером телефона: {phone_column}")

        return phone_column

    def find_date_column(self) -> str:
        """
        Находит целевую колонку с датой.

        Учитывает:
        - приоритет ключевого слова в названии;
        - долю значений, которые удалось распознать как даты.

        Возвращает:
            str: название найденной колонки.
        """

        def date_score(column: str) -> float:
            values = self.df[column].dropna().astype(str).str.strip()

            if values.empty:
                return 0.0

            # Маска валидных дат.
            valid_dates = pd.Series(
                False,
                index=values.index
            )

            # Обычные даты и даты со временем:
            # 2026-09-01
            # 01.09.2026
            # 2026-01-05T08:42:00+03:00
            non_numeric = ~values.str.fullmatch(r"\d+")

            parsed_dates = pd.to_datetime(
                values.loc[non_numeric],
                format="mixed",
                dayfirst=True,
                errors="coerce",
                utc=True
            )
            valid_dates.loc[non_numeric] = parsed_dates.notna()
            yyyymmdd = values.str.fullmatch(r"\d{8}")

            parsed_yyyymmdd = pd.to_datetime(
                values.loc[yyyymmdd],
                format="%Y%m%d",
                errors="coerce",
                utc=True
            )

            valid_dates.loc[yyyymmdd] = parsed_yyyymmdd.notna()

            valid_ratio = valid_dates.mean()

            column_name = self.normalize_column_name(column)

            keyword_score = max(
                (
                    score
                    for keyword, score in self.DATE_KEYWORDS.items()
                    if self.normalize_column_name(keyword) in column_name
                ),
                default=0
            )

            return float(keyword_score * valid_ratio)

        date_column = self.find_best_column(
            list(self.DATE_KEYWORDS.keys()),
            date_score
        )

        print(f"\nКолонка с датой: {date_column}")

        return date_column

    def find_amount_column(self) -> str:
        """
        Находит колонку с суммой покупки.
        Учитывает:
        - ключевые слова в названии колонки;
        - долю значений, которые удалось преобразовать в число.

        Возвращает:
            str: название найденной колонки.
        """

        def amount_score(column: str) -> float:
            values = self.df[column].dropna()

            if values.empty:
                return 0.0

            numeric_values = pd.to_numeric(
                values.astype(str).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
                errors="coerce"
            )

            valid_ratio = numeric_values.notna().mean()

            column_name = self.normalize_column_name(column)

            if any(
                self.normalize_column_name(keyword) in column_name
                for keyword in self.AMOUNT_EXCLUDE_KEYWORDS
            ):
                return 0.0

            keyword_score = max(
                (
                    score
                    for keyword, score in self.AMOUNT_KEYWORDS.items()
                    if self.normalize_column_name(keyword) in column_name
                ),
                default=0
            )

            return float(keyword_score * valid_ratio)

        amount_column = self.find_best_column(
            list(self.AMOUNT_KEYWORDS.keys()),
            amount_score
        )

        print(f"\nКолонка с суммой покупки: {amount_column}")

        return amount_column