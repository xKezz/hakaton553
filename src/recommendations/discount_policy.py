from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

from ..segmentation.business_metrics import normalize_series


class DiscountPolicy:
    """
    Автоматический расчёт скидки для каждой маркетинговой категории.
    """

    def __init__(
        self,
        cat_users,
        max_discount: float = 30.0,
        step: float = 5.0,
        need_free: float = 0.25,
        value_cut: float = 0.20,
        churn_discount: float = 5.0,
        single_category_discount: float | None = None,
    ):
        self.rfm = cat_users.rfm_df.copy()

        self.max_discount = float(max_discount)

        if self.max_discount < 0:
            raise ValueError("max_discount не может быть отрицательным.")

        if self.max_discount > 30.0:
            warnings.warn(
                f"Внимание: max_discount = {self.max_discount} больше стандартного лимита 30%.",
                UserWarning,
                stacklevel=2,
            )

        self.step = float(step)

        if self.step <= 0:
            raise ValueError("step должен быть больше нуля.")

        if self.step > self.max_discount:
            warnings.warn(
                "step больше max_discount, step уменьшен до max_discount.",
                UserWarning,
                stacklevel=2,
            )
            self.step = self.max_discount

        self.single_category_discount = (
            None
            if single_category_discount is None
            else float(single_category_discount)
        )

        if self.single_category_discount is not None:
            if self.single_category_discount < 0:
                raise ValueError("single_category_discount не может быть отрицательным.")

            if self.single_category_discount > self.max_discount:
                warnings.warn(
                    "single_category_discount больше max_discount. "
                    "Значение уменьшено до max_discount.",
                    UserWarning,
                    stacklevel=2,
                )
                self.single_category_discount = self.max_discount

        self.need_free = float(need_free)
        self.value_cut = float(value_cut)

        self.churn_discount = float(churn_discount)

        if self.churn_discount < 0:
            raise ValueError("churn_discount не может быть отрицательным.")

        self.churn_discount = min(self.churn_discount, self.max_discount)

        if self.rfm.empty:
            self.policy = self._empty_policy()
            return

        if "campaign" not in self.rfm.columns:
            raise ValueError("Сначала вызовите cat_users.users_cat(n).")

        self.policy = self._compute()

    @staticmethod
    def _empty_policy() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "campaign",
                "discount",
                "reason",
                "clients_count",
                "recency",
                "frequency",
                "monetary_score",
                "avg_amount",
            ]
        )

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        """
        Округление до шага в большую сторону при 0.5 и выше.
        """
        if step <= 0:
            return float(value)

        return float(math.floor(float(value) / step + 0.5) * step)

    def _compute(self) -> pd.DataFrame:
        g = self.rfm.groupby("campaign", as_index=False).agg(
            clients_count=("client_id", "size"),
            recency=("recency", "median"),
            frequency=("frequency", "median"),
            monetary_score=("monetary_score", "median"),
            avg_amount=("avg_amount", "mean"),
        )

        if g.empty:
            return self._empty_policy()

        g["r_norm"] = normalize_series(g["recency"])
        g["f_norm"] = normalize_series(g["frequency"])
        g["m_norm"] = normalize_series(g["monetary_score"])

        g["need"] = 0.6 * g["r_norm"] + 0.4 * (1.0 - g["f_norm"])
        g["value"] = 0.6 * g["m_norm"] + 0.4 * g["f_norm"]

        one_category = len(g) == 1

        discounts = []
        reasons = []

        for row in g.itertuples(index=False):
            if one_category and self.single_category_discount is not None:
                d = float(np.clip(self.single_category_discount, 0.0, self.max_discount))
                reason = "фиксированная скидка для единственной категории"

            elif self.max_discount == 0.0:
                d = 0.0
                reason = "max_discount равен 0"

            elif row.need <= self.need_free:
                d = 0.0
                reason = "высокая естественная активность - скидка не нужна"

            elif row.value <= self.value_cut and row.r_norm >= 0.5:
                minimal_positive = max(self.step, self.churn_discount)
                d = float(np.clip(minimal_positive, 0.0, self.max_discount))
                reason = "низкая ценность при оттоке - минимальная скидка последнего шанса"

            else:
                raw = self.max_discount * row.need * (0.5 + 0.5 * row.value)
                d = self._round_to_step(raw, self.step)

                lower_bound = self.step if self.step > 0 else 0.0
                d = float(np.clip(d, lower_bound, self.max_discount))

                reason = "стимулирование спроса"

            d = float(np.clip(d, 0.0, self.max_discount))

            discounts.append(round(d, 2))
            reasons.append(reason)

        g["discount"] = discounts
        g["reason"] = reasons

        return g[
            [
                "campaign",
                "discount",
                "reason",
                "clients_count",
                "recency",
                "frequency",
                "monetary_score",
                "avg_amount",
            ]
        ]

    def clients(self) -> pd.DataFrame:
        """
        Возвращает клиентов с назначенной скидкой и причиной.
        """
        df = self.rfm.copy()

        if df.empty:
            if "campaign" not in df.columns:
                df["campaign"] = pd.Series(dtype="int64")
            if "discount" not in df.columns:
                df["discount"] = pd.Series(dtype="float64")
            if "reason" not in df.columns:
                df["reason"] = pd.Series(dtype="object")
            return df

        if "campaign" not in df.columns:
            raise ValueError("Сначала вызовите cat_users.users_cat(n).")

        if self.policy.empty:
            df["discount"] = 0.0
            df["reason"] = "нет доступных категорий"
            return df

        policy = self.policy.set_index("campaign")

        df["discount"] = df["campaign"].map(policy["discount"])
        df["reason"] = df["campaign"].map(policy["reason"])

        if df["discount"].isna().any():
            warnings.warn(
                "Найдены клиенты без назначенной скидки. Для них установлена скидка 0.",
                UserWarning,
                stacklevel=2,
            )
            df["discount"] = df["discount"].fillna(0.0)
            df["reason"] = df["reason"].fillna("нет категории")

        return df

    def budget(self, response_rate: float = 0.1) -> float:
        """
        Оценка бюджета акции.

        База бюджета — средний чек (`avg_amount`).
        """
        if response_rate < 0:
            raise ValueError("response_rate не может быть отрицательным.")

        df = self.clients()

        if df.empty or "avg_amount" not in df.columns or "discount" not in df.columns:
            return 0.0

        return round(
            float(
                (
                    df["avg_amount"].astype(float)
                    * df["discount"].astype(float)
                    / 100.0
                    * float(response_rate)
                ).sum()
            ),
            2,
        )