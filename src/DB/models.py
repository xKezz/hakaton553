# src/DB/db/models.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

# ==========================================
# 1. ENUMS (Статусы и Роли)
# ==========================================
class CampaignStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"  # CSV загружен, идёт расчёт
    DRAFT = "DRAFT"            # Расчёт завершён, бизнес просматривает
    APPROVED = "APPROVED"      # Бизнес подтвердил, идёт начисление
    RUNNING = "RUNNING"        # Бонусы начислены, кампания активна
    COMPLETED = "COMPLETED"    # Все бонусы либо использованы, либо сгорели

class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"        # Ожидает начисления в API лояльности
    ACCRUED = "ACCRUED"        # Успешно начислено
    USED = "USED"              # Клиент потратил (пришёл вебхук)
    EXPIRED = "EXPIRED"        # Сгорело (списано планировщиком)
    FAILED = "FAILED"          # Ошибка при начислении

class AdminRole(str, enum.Enum):
    OWNER = "owner"
    VIEWER = "viewer"

# ==========================================
# 2. МОДЕЛИ ТАБЛИЦ
# ==========================================

class BusinessAdmin(Base):
    __tablename__ = "business_admins"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    max_user_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    role: Mapped[AdminRole] = mapped_column(default=AdminRole.OWNER)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[CampaignStatus] = mapped_column(default=CampaignStatus.PROCESSING)
    ttl_days: Mapped[int] = mapped_column(Integer, default=14)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    csv_file_path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Связь 1:N с категориями
    categories: Mapped[list["CampaignCategory"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    # Связь 1:N с транзакциями
    transactions: Mapped[list["BonusTransaction"]] = relationship(back_populates="campaign")


class CampaignCategory(Base):
    __tablename__ = "campaign_categories"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    
    # Поля из JSON-отчёта пайплайна
    segment_group: Mapped[int] = mapped_column(Integer)        # 0-26 (приоритет)
    label: Mapped[str] = mapped_column(String(100))
    clients_count: Mapped[int] = mapped_column(Integer)
    avg_recency: Mapped[float] = mapped_column(Float)
    avg_frequency: Mapped[float] = mapped_column(Float)
    avg_monetary_score: Mapped[float] = mapped_column(Float)
    avg_amount: Mapped[float] = mapped_column(Float)
    proposed_bonus: Mapped[int] = mapped_column(Integer)       # Что предложил DS
    final_bonus: Mapped[int] = mapped_column(Integer)          # Что одобрил бизнес
    reason: Mapped[str] = mapped_column(Text, nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="categories")

    __table_args__ = (
        Index("idx_cat_campaign", "campaign_id"),
    )


class BonusTransaction(Base):
    __tablename__ = "bonus_transactions"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_categories.id"))
    client_id: Mapped[str] = mapped_column(String(20))         # Телефон в E.164
    amount: Mapped[int] = mapped_column(Integer)
    external_txn_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=True)
    status: Mapped[TransactionStatus] = mapped_column(default=TransactionStatus.PENDING)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="transactions")

    __table_args__ = (
        Index("idx_txn_status_expires", "status", "expires_at"),
        Index("idx_txn_category", "category_id"),
        Index("idx_txn_external", "external_txn_id"),
    )