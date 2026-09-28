import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Index,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


ID_TYPE = BigInteger().with_variant(Integer(), "sqlite")


# =========================================================
# ENUMS
# =========================================================

class CampaignStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    NOT_REGISTERED = "NOT_REGISTERED"
    FAILED = "FAILED"


# =========================================================
# CLIENT
# =========================================================

class Client(Base):
    __tablename__ = "client"

    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )

    phone_e164: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
    )

    max_user_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
    )

    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    purchases: Mapped[list["Purchase"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )

    campaign_targets: Mapped[list["CampaignTarget"]] = relationship(
        back_populates="client",
    )


# =========================================================
# PURCHASE
# =========================================================

class Purchase(Base):
    __tablename__ = "purchase"

    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )

    purchase_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey(
            "client.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    purchase_date: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    amount: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    client: Mapped["Client"] = relationship(
        back_populates="purchases",
    )

    __table_args__ = (
        Index(
            "idx_purchase_client_id",
            "client_id",
        ),
        Index(
            "idx_purchase_date",
            "purchase_date",
        ),
        Index(
            "idx_purchase_client_date",
            "client_id",
            "purchase_date",
        ),
    )


# =========================================================
# CAMPAIGN
# =========================================================

class Campaign(Base):
    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default=CampaignStatus.PROCESSING.value,
        nullable=False,
    )

    source_file_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    total_clients: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    at_risk_clients: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    config: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    categories: Mapped[list["CampaignCategory"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    targets: Mapped[list["CampaignTarget"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


# =========================================================
# CAMPAIGN CATEGORY
# =========================================================

class CampaignCategory(Base):
    __tablename__ = "campaign_category"

    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey(
            "campaign.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    segment_group: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    label: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    clients_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    avg_recency: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    avg_frequency: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    avg_monetary_score: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    avg_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    proposed_bonus: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    final_bonus: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    campaign: Mapped["Campaign"] = relationship(
        back_populates="categories",
    )

    targets: Mapped[list["CampaignTarget"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "idx_campaign_category_campaign_id",
            "campaign_id",
        ),
    )


# =========================================================
# CAMPAIGN TARGET
# =========================================================

class CampaignTarget(Base):
    __tablename__ = "campaign_target"

    id: Mapped[int] = mapped_column(
        ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey(
            "campaign.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey(
            "campaign_category.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    client_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "client.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    phone_e164: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    max_user_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    recency: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    frequency: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    monetary_score: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    avg_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    bonus_amount: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    notification_status: Mapped[str] = mapped_column(
        String(30),
        default=NotificationStatus.PENDING.value,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship(
        back_populates="targets",
    )

    category: Mapped["CampaignCategory"] = relationship(
        back_populates="targets",
    )

    client: Mapped["Client | None"] = relationship(
        back_populates="campaign_targets",
    )

    __table_args__ = (
        Index(
            "idx_campaign_target_campaign_id",
            "campaign_id",
        ),
        Index(
            "idx_campaign_target_category_id",
            "category_id",
        ),
        Index(
            "idx_campaign_target_client_id",
            "client_id",
        ),
    )