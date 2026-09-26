# src/DB/db/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from datetime import datetime

# ==========================================
# 1. КАМПАНИИ
# ==========================================
class CampaignUploadResponse(BaseModel):
    campaign_id: UUID
    status: str
    message: str

class CampaignStatusResponse(BaseModel):
    campaign_id: UUID
    status: str
    progress: Optional[int] = None
    categories_count: Optional[int] = None
    total_clients: Optional[int] = None
    warnings: Optional[List[str]] = None

class ApproveResponse(BaseModel):
    campaign_id: UUID
    status: str
    total_clients: int
    total_bonus_budget: int
    message: str

# ==========================================
# 2. КАТЕГОРИИ
# ==========================================
class CategoryOut(BaseModel):
    id: UUID
    segment_group: int
    label: str
    clients_count: int
    avg_recency: float
    avg_frequency: float
    avg_monetary_score: float
    avg_amount: float
    proposed_bonus: int
    final_bonus: int
    reason: Optional[str] = None

    class Config:
        from_attributes = True  # Позволяет создавать схему из SQLAlchemy-объекта

class CategoriesListResponse(BaseModel):
    campaign_id: UUID
    ttl_days: int
    categories: List[CategoryOut]

class CategoryUpdate(BaseModel):
    final_bonus: int = Field(..., gt=0, description="Бонус должен быть больше 0")

class CategoryUpdateResponse(BaseModel):
    id: UUID
    final_bonus: int
    total_budget_impact: int