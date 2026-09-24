from __future__ import annotations

import json
import warnings
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from .segmentation.business_metrics import BusinessMetrics
from .recommendations.discount_policy import DiscountPolicy
from .segmentation.user_categories import UserCategories


def build_discount_report(
    path_data: Optional[str] = None,
    data: Optional[pd.DataFrame] = None,
    reference_date: Optional[pd.Timestamp] = None,
    count_cat: int = 3,
    method: str = "win-back",
    max_discount: float = 30.0,
    step: float = 5.0,
    need_free: float = 0.25,
    value_cut: float = 0.20,
    churn_discount: float = 5.0,
    single_category_discount: Optional[float] = None,
    include_client_ids: bool = True,
) -> Dict[str, Any]:
    """
    Собирает итоговый отчёт в формате словаря, готового к выгрузке в JSON.
    """
    requested_count_cat = int(count_cat)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        bm = BusinessMetrics(
            path_data=path_data,
            data=data,
            reference_date=reference_date,
        )

        uc = UserCategories(
            business_metrics=bm,
            method=method,
        )

        uc.users_cat(requested_count_cat)

        dp = DiscountPolicy(
            uc,
            max_discount=max_discount,
            step=step,
            need_free=need_free,
            value_cut=value_cut,
            churn_discount=churn_discount,
            single_category_discount=single_category_discount,
        )

    seen = set()
    warning_messages = []

    for w in caught:
        msg = str(w.message)
        if msg not in seen:
            seen.add(msg)
            warning_messages.append(msg)

    method_config = UserCategories.METHOD_CONFIGS.get(method, {})
    weights = method_config.get("weights", (0.0, 0.0, 0.0))

    score_formula = (
        f"{weights[0]:.2f}*recency_norm + "
        f"{weights[1]:.2f}*frequency_norm + "
        f"{weights[2]:.2f}*monetary_norm"
    )

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_date": bm.reference_date.date().isoformat(),
        "method": method,
        "max_discount": float(dp.max_discount),
        "step": float(dp.step),
        "requested_count_cat": requested_count_cat,
        "effective_count_cat": int(uc.effective_count_cat or 0),
        "monetary_formula": "0.40*total_norm + 0.40*avg_norm + 0.20*frequency_norm",
        "score_formula": score_formula,
        "budget_base": "avg_amount",
        "warnings": warning_messages,
    }

    if bm.metrics_df.empty:
        return {
            "meta": meta,
            "categories": [],
            "clients": [],
        }

    clients_df = dp.clients()

    if "discount" in clients_df.columns:
        clients_df["discount"] = clients_df["discount"].fillna(0.0)

    if "reason" in clients_df.columns:
        clients_df["reason"] = clients_df["reason"].fillna("нет категории")

    clients_df = clients_df.sort_values("client_id").reset_index(drop=True)

    policy_by_campaign = (
        dp.policy.set_index("campaign")
        if not dp.policy.empty
        else pd.DataFrame()
    )

    campaign_values = (
        set(int(x) for x in clients_df["campaign"].unique())
        if not clients_df.empty
        else set()
    )

    effective_count = int(uc.effective_count_cat or 0)
    campaign_ids = sorted(set(range(effective_count)) | campaign_values)

    categories = []

    for campaign_int in campaign_ids:
        group = clients_df[clients_df["campaign"] == campaign_int]

        if not group.empty:
            if not policy_by_campaign.empty and campaign_int in policy_by_campaign.index:
                policy_row = policy_by_campaign.loc[campaign_int]
                discount = float(policy_row["discount"])
                reason = str(policy_row["reason"])
            else:
                discount = float(group["discount"].iloc[0])
                reason = str(group["reason"].iloc[0])

            category = {
                "campaign": campaign_int,
                "label": f"priority_{campaign_int}",
                "discount": discount,
                "reason": reason,
                "clients_count": int(len(group)),
                "avg_recency": float(group["recency"].mean()),
                "avg_frequency": float(group["frequency"].mean()),
                "avg_monetary_score": float(group["monetary_score"].mean()),
                "avg_amount": float(group["avg_amount"].mean()),
            }

            if include_client_ids:
                category["client_ids"] = sorted(group["client_id"].astype(str).tolist())

            categories.append(category)

        else:
            category = {
                "campaign": campaign_int,
                "label": f"priority_{campaign_int}",
                "discount": 0.0,
                "reason": "нет клиентов в категории",
                "clients_count": 0,
                "avg_recency": 0.0,
                "avg_frequency": 0.0,
                "avg_monetary_score": 0.0,
                "avg_amount": 0.0,
            }

            if include_client_ids:
                category["client_ids"] = []

            categories.append(category)

    clients = []

    for row in clients_df.itertuples(index=False):
        clients.append(
            {
                "client_id": str(row.client_id),
                "campaign": int(row.campaign),
                "discount": float(row.discount),
                "reason": str(row.reason),
                "recency": int(row.recency),
                "frequency": int(row.frequency),
                "total_amount": float(row.total_amount),
                "avg_amount": float(row.avg_amount),
                "monetary_score": float(row.monetary_score),
            }
        )

    return {
        "meta": meta,
        "categories": categories,
        "clients": clients,
    }


def save_discount_report(path: str, **kwargs) -> Dict[str, Any]:
    """
    Собирает отчёт и сохраняет его в JSON-файл.
    """
    report = build_discount_report(**kwargs)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report