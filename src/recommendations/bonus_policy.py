from __future__ import annotations
import math
import warnings
import numpy as np
import pandas as pd

from segmentation.business_metrics import normalize_series

class BonusPolicy:
    """
    Автоматический расчёт бонусных баллов для каждой маркетинговой категории.
    Адаптировано из DiscountPolicy согласно Приложению А ТЗ.
    """

    def __init__(
        self,
        cat_users,
        max_bonus_points: float = 250.0,
        step: float = 50.0,
        need_free: float = 0.25,
        value_cut: float = 0.20,
        churn_bonus: float = 50.0,
        single_category_bonus: float | None = None,
    ):
        self.rfm = cat_users.rfm_df.copy()

        self.max_bonus_points = float(max_bonus_points)
        if self.max_bonus_points < 0:
            raise ValueError("max_bonus_points не может быть отрицательным.")

        if self.max_bonus_points > 250.0:
            warnings.warn(
                f"Внимание: max_bonus_points = {self.max_bonus_points} больше стандартного лимита 250.",
                UserWarning,
                stacklevel=2,
            )

        self.step = float(step)
        if self.step <= 0:
            raise ValueError("step должен быть больше нуля.")

        if self.step > self.max_bonus_points:
            warnings.warn(
                "step больше max_bonus_points, step уменьшен до max_bonus_points.",
                UserWarning,
                stacklevel=2,
            )
            self.step = self.max_bonus_points

        self.single_category_bonus = (
            None if single_category_bonus is None else float(single_category_bonus)
        )

        if self.single_category_bonus is not None:
            if self.single_category_bonus < 0:
                raise ValueError("single_category_bonus не может быть отрицательным.")
            if self.single_category_bonus > self.max_bonus_points:
                warnings.warn(
                    "single_category_bonus больше max_bonus_points. Значение уменьшено.",
                    UserWarning,
                    stacklevel=2,
                )
                self.single_category_bonus = self.max_bonus_points

        self.need_free = float(need_free)
        self.value_cut = float(value_cut)

        self.churn_bonus = float(churn_bonus)
        if self.churn_bonus < 0:
            raise ValueError("churn_bonus не может быть отрицательным.")
        self.churn_bonus = min(self.churn_bonus, self.max_bonus_points)

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
                "proposed_bonus",
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
        """Округление до шага в большую сторону при 0.5 и выше."""
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

        # Формулы из Приложения А
        g["need"] = 0.6 * g["r_norm"] + 0.4 * (1.0 - g["f_norm"])
        g["value"] = 0.6 * g["m_norm"] + 0.4 * g["f_norm"]

        one_category = len(g) == 1
        bonuses = []
        reasons = []

        for row in g.itertuples(index=False):
            if one_category and self.single_category_bonus is not None:
                b = float(np.clip(self.single_category_bonus, 0.0, self.max_bonus_points))
                reason = "фиксированный бонус для единственной категории"

            elif self.max_bonus_points == 0.0:
                b = 0.0
                reason = "max_bonus_points равен 0"

            elif row.need <= self.need_free:
                b = 0.0
                reason = "высокая естественная активность - бонус не нужен"

            elif row.value <= self.value_cut and row.r_norm >= 0.5:
                # Минимальный бонус последнего шанса
                minimal_positive = max(self.step, self.churn_bonus)
                b = float(np.clip(minimal_positive, 0.0, self.max_bonus_points))
                reason = "низкая ценность при оттоке - минимальный бонус последнего шанса"

            else:
                # Основная формула стимулирования спроса
                raw = self.max_bonus_points * row.need * (0.5 + 0.5 * row.value)
                b = self._round_to_step(raw, self.step)
                
                # Применяем clamp(bonus, step, max_bonus_points) как в ТЗ
                b = float(np.clip(b, self.step, self.max_bonus_points))
                reason = "стимулирование спроса"

            bonuses.append(round(b, 2))
            reasons.append(reason)

        g["proposed_bonus"] = bonuses
        g["reason"] = reasons

        return g[
            [
                "campaign",
                "proposed_bonus",
                "reason",
                "clients_count",
                "recency",
                "frequency",
                "monetary_score",
                "avg_amount",
            ]
        ]

    def clients(self) -> pd.DataFrame:
        """Возвращает клиентов с назначенным бонусом и причиной."""
        df = self.rfm.copy()

        if df.empty:
            if "campaign" not in df.columns:
                df["campaign"] = pd.Series(dtype="int64")
            if "proposed_bonus" not in df.columns:
                df["proposed_bonus"] = pd.Series(dtype="float64")
            if "reason" not in df.columns:
                df["reason"] = pd.Series(dtype="object")
            return df

        if "campaign" not in df.columns:
            raise ValueError("Сначала вызовите cat_users.users_cat(n).")

        if self.policy.empty:
            df["proposed_bonus"] = 0.0
            df["reason"] = "нет доступных категорий"
            return df

        policy = self.policy.set_index("campaign")
        df["proposed_bonus"] = df["campaign"].map(policy["proposed_bonus"])
        df["reason"] = df["campaign"].map(policy["reason"])

        if df["proposed_bonus"].isna().any():
            warnings.warn(
                "Найдены клиенты без назначенного бонуса. Для них установлен бонус 0.",
                UserWarning,
                stacklevel=2,
            )
            df["proposed_bonus"] = df["proposed_bonus"].fillna(0.0)
            df["reason"] = df["reason"].fillna("нет категории")

        return df

    def budget(self, response_rate: float = 0.1) -> float:
        """
        Оценка бюджета акции в бонусных баллах.
        
        """
        if response_rate < 0:
            raise ValueError("response_rate не может быть отрицательным.")

        df = self.clients()

        if df.empty or "proposed_bonus" not in df.columns:
            return 0.0

        # Считаем просто сумму ожидаемых бонусов с учетом конверсии (response_rate)
        return round(
            float(
                (df["proposed_bonus"].astype(float) * float(response_rate)).sum()
            ),
            2,
        )