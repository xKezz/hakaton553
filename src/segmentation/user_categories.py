from __future__ import annotations

import warnings
from itertools import product
from typing import Optional

import numpy as np
import pandas as pd

from business_metrics import BusinessMetrics, normalize_series

METRIC_COLS = ["recency", "frequency", "monetary_score"]


def _transform_with_bounds(
    values: np.ndarray,
    min_vals: np.ndarray,
    max_vals: np.ndarray,
    constant: float = 0.5,
) -> np.ndarray:
    """
    Минимаксное преобразование массива по заранее заданным границам.
    Если диапазон по колонке нулевой, используем константу.
    """
    values = np.asarray(values, dtype=float)

    if values.size == 0:
        return values.copy()

    denom = (max_vals - min_vals).astype(float)
    out = np.empty_like(values, dtype=float)

    for j in range(values.shape[1]):
        if not np.isfinite(denom[j]) or denom[j] == 0.0:
            out[:, j] = constant
        else:
            out[:, j] = (values[:, j] - min_vals[j]) / denom[j]

    return out


class UserCategories:
    """
    Класс для построения матрицы категорий и назначения клиентов по категориям.

    Сейчас поддерживается метод `win-back`.

    Для расширения можно зарегистрировать новую стратегию:

        UserCategories.register_strategy(
            name="upsell",
            weights=(0.2, 0.3, 0.5),
            recency_higher_is_better=False,
        )
    """

    METHOD_CONFIGS = {
        "win-back": {
            "weights": (0.60, 0.10, 0.30),
            "recency_higher_is_better": True,
        },
    }

    @classmethod
    def register_strategy(
        cls,
        name: str,
        weights: tuple[float, float, float],
        recency_higher_is_better: bool = True,
    ) -> None:
        weights = tuple(float(w) for w in weights)

        if len(weights) != 3:
            raise ValueError("weights должны содержать 3 значения.")

        if sum(weights) <= 0:
            raise ValueError("Сумма весов должна быть больше нуля.")

        cls.METHOD_CONFIGS[name] = {
            "weights": weights,
            "recency_higher_is_better": bool(recency_higher_is_better),
        }

    def __init__(
        self,
        path_to_data: Optional[str] = None,
        data: Optional[pd.DataFrame] = None,
        business_metrics: Optional[BusinessMetrics] = None,
        reference_date: Optional[pd.Timestamp] = None,
        method: str = "win-back",
    ):
        if business_metrics is None:
            business_metrics = BusinessMetrics(
                path_data=path_to_data,
                data=data,
                reference_date=reference_date,
            )

        self.business_metrics = business_metrics
        self.method = method

        self.rfm_df = business_metrics.metrics_df.copy()

        self.requested_count_cat: Optional[int] = None
        self.effective_count_cat: Optional[int] = None

        self.categories_matrix = self.get_categories_matrix(method)

    def _warn(self, message: str) -> None:
        warnings.warn(message, UserWarning, stacklevel=3)

    def get_categories_matrix(self, method: Optional[str] = None) -> pd.DataFrame:
        """
        Строит матрицу категорий из комбинаций:
        3 уровня recency × 3 уровня frequency × 3 уровня monetary_score.
        """
        method = method or self.method

        config = self.METHOD_CONFIGS.get(method)
        if config is None:
            available = sorted(self.METHOD_CONFIGS.keys())
            raise ValueError(
                f"Метод '{method}' не поддерживается. Доступные методы: {available}."
            )

        columns = METRIC_COLS + ["score"]

        if self.rfm_df.empty:
            return pd.DataFrame(columns=columns)

        levels = {}

        for col in METRIC_COLS:
            s = self.rfm_df[col].astype(float)
            levels[col] = [
                float(s.min()),
                float(s.mean()),
                float(s.max()),
            ]

        rows = list(
            product(
                levels["recency"],
                levels["frequency"],
                levels["monetary_score"],
            )
        )

        matrix = pd.DataFrame(rows, columns=METRIC_COLS)

        # Убираем дубли, например, если все значения одинаковые
        matrix = matrix.round(12).drop_duplicates(subset=METRIC_COLS).reset_index(drop=True)

        recency_norm = normalize_series(matrix["recency"])
        frequency_norm = normalize_series(matrix["frequency"])
        monetary_norm = normalize_series(matrix["monetary_score"])

        # Для win-back высокий recency повышает приоритет.
        # Если будущая стратегия требует обратного, можно установить
        # recency_higher_is_better=False.
        if not config["recency_higher_is_better"]:
            recency_norm = 1.0 - recency_norm

        weights = np.asarray(config["weights"], dtype=float)

        if weights.sum() > 0:
            weights = weights / weights.sum()

        matrix["score"] = (
            weights[0] * recency_norm
            + weights[1] * frequency_norm
            + weights[2] * monetary_norm
        ).round(12)

        # Категория 0 — самая приоритетная
        matrix = matrix.sort_values(
            ["score", "recency", "frequency", "monetary_score"],
            ascending=False,
            kind="mergesort",
        ).reset_index(drop=True)

        return matrix

    def assign_rfm_groups(self) -> pd.DataFrame:
        """
        Назначает каждому клиенту ближайшую группу из матрицы категорий.
        """
        rfm = self.rfm_df.copy()

        if rfm.empty:
            rfm["group_id"] = pd.Series(dtype="int64")
            rfm["distance"] = pd.Series(dtype="float64")
            self.rfm_df = rfm
            return rfm

        if self.categories_matrix.empty:
            self._warn("Невозможно построить матрицу категорий.")
            rfm["group_id"] = 0
            rfm["distance"] = 0.0
            self.rfm_df = rfm
            return rfm

        client_values = rfm[METRIC_COLS].to_numpy(dtype=float)

        min_vals = client_values.min(axis=0)
        max_vals = client_values.max(axis=0)

        client_scaled = _transform_with_bounds(client_values, min_vals, max_vals)

        matrix_values = self.categories_matrix[METRIC_COLS].to_numpy(dtype=float)
        matrix_scaled = _transform_with_bounds(matrix_values, min_vals, max_vals)

        distances = np.sqrt(
            ((client_scaled[:, None, :] - matrix_scaled[None, :, :]) ** 2).sum(axis=2)
        )

        rfm["group_id"] = distances.argmin(axis=1).astype(int)
        rfm["distance"] = distances.min(axis=1).astype(float)

        self.rfm_df = rfm

        return rfm

    def users_cat(self, count_cat: int) -> pd.DataFrame:
        """
        Разбивает доступные группы на `count_cat` маркетинговых категорий
        и назначает категории клиентам.

        Категория 0 — самая приоритетная.
        """
        if not isinstance(count_cat, (int, np.integer)):
            raise TypeError("count_cat должен быть целым числом.")

        count_cat = int(count_cat)

        if count_cat <= 0:
            raise ValueError("count_cat должен быть больше нуля.")

        self.requested_count_cat = count_cat

        rfm = self.rfm_df.copy()

        if rfm.empty:
            self.effective_count_cat = 0
            self._warn("Входные данные не содержат клиентов.")
            rfm["campaign"] = pd.Series(dtype="int64")
            self.rfm_df = rfm
            return rfm

        available = len(self.categories_matrix)

        if available == 0:
            self._warn("Невозможно построить категории. Возвращаем результат без категорий.")
            self.effective_count_cat = 0
            rfm["campaign"] = 0
            self.rfm_df = rfm
            return rfm

        effective = min(count_cat, available)

        if effective < count_cat:
            self._warn(
                f"count_cat уменьшен до {effective} количества доступных уникальных групп."
            )

        self.effective_count_cat = effective

        matrix = self.categories_matrix.copy()

        chunks = np.array_split(np.arange(len(matrix)), effective)

        campaign = np.empty(len(matrix), dtype=int)

        for idx, chunk in enumerate(chunks):
            campaign[chunk] = idx

        matrix["campaign"] = campaign
        self.categories_matrix = matrix

        if "group_id" not in rfm.columns:
            self.assign_rfm_groups()
            rfm = self.rfm_df.copy()

        rfm["campaign"] = (
            rfm["group_id"]
            .map(self.categories_matrix["campaign"])
            .astype(int)
        )

        self.rfm_df = rfm

        return rfm