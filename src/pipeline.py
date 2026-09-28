import json

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

    async def run(
        self,
        session: AsyncSession,
        source_file_path: str = None,
        output_path: str = None,
        **kwargs,
    ) -> dict:
        clean_df = self.predprocess()

        await save_purchases(
            session,
            clean_df.to_dict(
                orient="records"
            ),
        )

        purchases = await get_all_purchases(
            session
        )

        history_df = (
            self._purchases_to_dataframe(
                purchases
            )
        )

        report = build_bonus_report(
            data=history_df,
            count_cat=5,
            **kwargs,
        )

        at_risk_clients = sum(
            category["clients_count"]
            for category in report["categories"]
            if int(
                category["segment_group"]
            ) < 4
        )

        campaign = await create_campaign(
            session,
            source_file_path=source_file_path,
            config=report["meta"],
            total_clients=len(
                report["clients"]
            ),
            at_risk_clients=at_risk_clients,
        )

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

        targets = []

        for client_data in report["clients"]:
            segment_group = int(
                client_data["segment_group"]
            )

            if segment_group >= 4:
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

            category = category_by_group[
                str(segment_group)
            ]

            targets.append(
                {
                    "campaign_id": campaign.id,
                    "category_id": category.id,
                    "client_id": client.id,
                    "phone_e164": client.phone_e164,
                    "max_user_id": client.max_user_id,
                    "recency": client_data["recency"],
                    "frequency": client_data["frequency"],
                    "monetary_score": (
                        client_data["monetary_score"]
                    ),
                    "avg_amount": client_data["avg_amount"],
                    "bonus_amount": (
                        category.final_bonus
                    ),
                    "notification_status": "PENDING",
                }
            )

        await create_campaign_targets(
            session,
            targets,
        )

        await update_campaign_status(
            session,
            campaign.id,
            "DRAFT",
        )

        report["campaign_id"] = campaign.id
        report["status"] = "DRAFT"

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