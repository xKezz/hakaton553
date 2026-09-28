import json
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from src.DB.crud import (
    create_campaign,
    create_campaign_categories,
    create_campaign_targets,
    get_all_purchases,
    get_client_by_phone,
    save_purchases,
    update_campaign_status,
)
from src.data_process.loader import ColumnFinder
from src.data_process.predprocessing import Preprocessor
from src.segmentation.full_categorised import build_bonus_report


class Pipeline:
    DEFAULT_CAMPAIGN_DAYS = 30

    def __init__(
        self,
        df: pd.DataFrame,
        default_region: str = "RU",
    ):
        self.df = df.copy()
        self.default_region = default_region

        self.preprocessor = Preprocessor(
            self.df,
            default_region,
        )

        self.column_finder = ColumnFinder(
            self.df
        )

        self._purchase_id_col = None
        self._phone_col = None
        self._date_col = None
        self._amount_col = None

    # =====================================================
    # COLUMN DETECTION
    # =====================================================

    def _find_columns(self):
        self._purchase_id_col = (
            self.column_finder
            .find_purchase_id_column()
        )

        self._phone_col = (
            self.column_finder
            .find_phone_column()
        )

        self._date_col = (
            self.column_finder
            .find_date_column()
        )

        self._amount_col = (
            self.column_finder
            .find_amount_column()
        )

        if not self._purchase_id_col:
            raise ValueError(
                "Колонка с ID покупки не найдена."
            )

        if not self._phone_col:
            raise ValueError(
                "Колонка с телефонами не найдена."
            )

        if not self._date_col:
            raise ValueError(
                "Колонка с датами не найдена."
            )

        if not self._amount_col:
            raise ValueError(
                "Колонка с суммами не найдена."
            )

    # =====================================================
    # PREPROCESSING
    # =====================================================

    def predprocess(self) -> pd.DataFrame:
        if not self._purchase_id_col:
            self._find_columns()

        self.df["purchase_id"] = (
            self.preprocessor
            .process_purchase_id_column(
                self._purchase_id_col
            )
        )

        self.df["client_id"] = (
            self.preprocessor
            .process_phone_column(
                self._phone_col
            )
        )

        self.df["purchase_date"] = (
            self.preprocessor
            .process_date_column(
                self._date_col
            )
        )

        self.df["amount"] = (
            self.preprocessor
            .process_amount_column(
                self._amount_col
            )
        )

        self.df = self.df[
            [
                "purchase_id",
                "client_id",
                "purchase_date",
                "amount",
            ]
        ].dropna()

        if self.df.empty:
            raise ValueError(
                "После предобработки "
                "не осталось ни одной валидной строки."
            )

        return self.df

    # =====================================================
    # HISTORY FROM DB
    # =====================================================

    @staticmethod
    def _purchases_to_dataframe(
        purchases,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "purchase_id": purchase.purchase_id,
                    "client_id": (
                        purchase.client.phone_e164
                    ),
                    "purchase_date": (
                        purchase.purchase_date
                    ),
                    "amount": float(
                        purchase.amount
                    ),
                }
                for purchase in purchases
            ]
        )

    # =====================================================
    # MAIN PIPELINE
    # =====================================================

    async def run(
        self,
        session: AsyncSession,
        source_file_path: str | None = None,
        output_path: str | None = None,
        campaign_days: int = DEFAULT_CAMPAIGN_DAYS,
        campaign_ends_at: datetime | None = None,
        **kwargs,
    ) -> dict:

        # -------------------------------------------------
        # 1. Определяем срок действия кампании
        # -------------------------------------------------

        if campaign_ends_at is None:
            if campaign_days <= 0:
                raise ValueError(
                    "campaign_days должен быть больше нуля."
                )

            campaign_ends_at = (
                datetime.utcnow()
                + timedelta(
                    days=campaign_days
                )
            )

        # -------------------------------------------------
        # 2. Предобработка CSV
        # -------------------------------------------------

        clean_df = self.predprocess()

        # -------------------------------------------------
        # 3. Сохраняем новые покупки
        # -------------------------------------------------

        await save_purchases(
            session,
            clean_df.to_dict(
                orient="records"
            ),
        )

        # -------------------------------------------------
        # 4. Получаем всю накопленную историю
        # -------------------------------------------------

        purchases = await get_all_purchases(
            session
        )

        history_df = (
            self._purchases_to_dataframe(
                purchases
            )
        )

        # -------------------------------------------------
        # 5. Аналитика + 5 категорий + бонусы
        # -------------------------------------------------

        report = build_bonus_report(
            data=history_df,
            count_cat=5,
            **kwargs,
        )

        # -------------------------------------------------
        # 6. Считаем количество клиентов
        #    групп 0–3
        # -------------------------------------------------

        at_risk_clients = sum(
            category["clients_count"]
            for category in report["categories"]
            if int(
                category["segment_group"]
            ) < 4
        )

        # -------------------------------------------------
        # 7. Создаём кампанию
        # -------------------------------------------------

        campaign = await create_campaign(
            session,
            source_file_path=source_file_path,
            config=report["meta"],
            total_clients=len(
                report["clients"]
            ),
            at_risk_clients=at_risk_clients,
            campaign_ends_at=campaign_ends_at,
        )

        # -------------------------------------------------
        # 8. Сохраняем 5 категорий
        # -------------------------------------------------

        saved_categories = (
            await create_campaign_categories(
                session,
                campaign.id,
                report["categories"],
            )
        )

        category_by_group = {
            str(category.segment_group): category
            for category in saved_categories
        }

        # -------------------------------------------------
        # 9. Формируем targets
        # -------------------------------------------------

        targets = []

        for client_data in report["clients"]:
            segment_group = int(
                client_data["segment_group"]
            )

            # Группа 4 — стабильные клиенты.
            if segment_group >= 4:
                continue

            category = category_by_group[
                str(segment_group)
            ]

            # Нулевые бонусы в campaign_target
            # не сохраняем.
            if int(category.final_bonus) <= 0:
                continue

            client = await get_client_by_phone(
                session,
                client_data["client_id"],
            )

            if client is None:
                raise ValueError(
                    f"Клиент "
                    f"{client_data['client_id']} "
                    f"не найден в БД."
                )

            targets.append(
                {
                    "campaign_id": campaign.id,
                    "category_id": category.id,
                    "client_id": client.id,
                    "recency": client_data[
                        "recency"
                    ],
                    "frequency": client_data[
                        "frequency"
                    ],
                    "monetary_score": client_data[
                        "monetary_score"
                    ],
                    "avg_amount": client_data[
                        "avg_amount"
                    ],
                    "bonus_amount": category.final_bonus,
                    "bonus_realised": None,
                }
            )

        # -------------------------------------------------
        # 10. Сохраняем targets
        # -------------------------------------------------

        await create_campaign_targets(
            session,
            targets,
        )

        # -------------------------------------------------
        # 11. Кампания готова к запуску
        # -------------------------------------------------

        await update_campaign_status(
            session,
            campaign.id,
            "DRAFT",
        )

        report["campaign_id"] = campaign.id
        report["status"] = "DRAFT"
        report["campaign_ends_at"] = (
            campaign_ends_at.isoformat()
        )

        # -------------------------------------------------
        # 12. Сохраняем JSON-отчёт
        # -------------------------------------------------

        if output_path:
            with open(
                output_path,
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(
                    report,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

        return report