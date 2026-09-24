from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd

CLIENT_ID_COL = "client_id"
DATE_COL = "purchase_date"
AMOUNT_COL = "amount"
REQUIRED_COLUMNS = (CLIENT_ID_COL, DATE_COL, AMOUNT_COL)


def normalize_series(series: pd.Series, constant: float = 0.5) -> pd.Series:
    """
    Минимаксная нормировка серии.

    Если все значения одинаковые или нормировка невозможна,
    возвращаем константу `constant`.
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)

    if s.empty:
        return s.copy()

    min_val = float(s.min())
    max_val = float(s.max())
    rng = max_val - min_val

    if not np.isfinite(rng) or rng == 0.0:
        return pd.Series(constant, index=s.index, dtype=float)

    return (s - min_val) / rng


class BusinessMetrics:
    """
    Класс для расчёта базовых клиентских метрик:

    - recency
    - frequency
    - total_amount
    - avg_amount
    - monetary_score
    """

    REQUIRED_COLUMNS = REQUIRED_COLUMNS

    def __init__(
        self,
        path_data: Optional[str] = None,
        data: Optional[pd.DataFrame] = None,
        reference_date: Optional[pd.Timestamp] = None,
    ):
        self.path_data = path_data

        self.reference_date = (
            pd.Timestamp(reference_date).normalize()
            if reference_date is not None
            else pd.Timestamp.today().normalize()
        )

        self.data = self._load(data, path_data)
        self.metrics_df = self._build_metrics()

    def _warn(self, message: str) -> None:
        warnings.warn(message, UserWarning, stacklevel=3)

    def _load(
        self,
        data: Optional[pd.DataFrame],
        path_data: Optional[str],
    ) -> pd.DataFrame:
        if data is not None:
            df = data.copy()
        elif path_data is not None:
            df = pd.read_csv(path_data, dtype={CLIENT_ID_COL: str})
        else:
            raise ValueError("Не указан источник данных: передайте path_data или data.")

        missing = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(
                f"Во входных данных отсутствуют обязательные колонки: {missing}."
            )

        if df.empty:
            return df.loc[:, self.REQUIRED_COLUMNS].copy()

        # client_id храним как строку, чтобы не потерять '+' и ведущие нули
        df[CLIENT_ID_COL] = df[CLIENT_ID_COL].astype("string").str.strip()
        df.loc[
            df[CLIENT_ID_COL].str.lower().isin(["nan", "none", ""]),
            CLIENT_ID_COL,
        ] = pd.NA

        # Парсим дату
        try:
            df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="raise")
        except Exception as exc:
            raise ValueError("Некорректная дата в колонке purchase_date.") from exc

        # Парсим сумму
        try:
            df[AMOUNT_COL] = pd.to_numeric(df[AMOUNT_COL], errors="raise")
        except Exception as exc:
            raise ValueError("Некорректный числовой формат в колонке amount.") from exc

        # Проверяем пустые/некорректные значения
        amount_invalid = ~np.isfinite(df[AMOUNT_COL].astype(float))
        missing_mask = (
            df[CLIENT_ID_COL].isna()
            | df[DATE_COL].isna()
            | amount_invalid
        )

        if missing_mask.any():
            self._warn(
                "В данных есть пустые/некорректные значения client_id, "
                "purchase_date или amount. Такие строки исключены."
            )
            df = df[~missing_mask]

        if df.empty:
            return df.loc[:, self.REQUIRED_COLUMNS].copy()

        return df.loc[:, self.REQUIRED_COLUMNS].reset_index(drop=True)

    def _build_metrics(self) -> pd.DataFrame:
        columns = [
            CLIENT_ID_COL,
            "last_purchase_date",
            "recency",
            "frequency",
            "total_amount",
            "avg_amount",
            "monetary_score",
        ]

        df = self.data.copy()

        if df.empty:
            return pd.DataFrame(columns=columns)

        # Будущие даты клипим до reference_date
        future_mask = df[DATE_COL] > self.reference_date
        if future_mask.any():
            self._warn(
                "Найдены покупки с датой позже reference_date. "
                "Они будут обрезаны до даты отсчёта."
            )

        df["effective_date"] = df[DATE_COL].clip(upper=self.reference_date)

        metrics = df.groupby(CLIENT_ID_COL, as_index=False).agg(
            last_purchase_date=("effective_date", "max"),
            frequency=(AMOUNT_COL, "count"),
            total_amount=(AMOUNT_COL, "sum"),
            avg_amount=(AMOUNT_COL, "mean"),
        )

        metrics["recency"] = (
            self.reference_date - metrics["last_purchase_date"]
        ).dt.days

        metrics["recency"] = metrics["recency"].clip(lower=0).astype(int)

        # Комбинированный показатель покупательской способности
        metrics["monetary_score"] = (
            0.40 * normalize_series(metrics["total_amount"])
            + 0.40 * normalize_series(metrics["avg_amount"])
            + 0.20 * normalize_series(metrics["frequency"])
        )

        metrics["monetary_score"] = metrics["monetary_score"].clip(0.0, 1.0).round(12)

        metrics = metrics.sort_values(CLIENT_ID_COL).reset_index(drop=True)

        return metrics[columns]

    def metrics(self) -> pd.DataFrame:
        """
        Возвращает таблицу клиентских метрик.
        """
        return self.metrics_df.copy()