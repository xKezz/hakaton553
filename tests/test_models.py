from src.DB.database import Base
from src.DB.models import (
    Campaign,
    CampaignCategory,
    CampaignTarget,
    Client,
    Purchase,
)


def test_expected_tables_exist():
    assert set(Base.metadata.tables) == {
        "client",
        "purchase",
        "campaign",
        "campaign_category",
        "campaign_target",
    }


def test_client_max_user_id_is_nullable():
    column = Client.__table__.c.max_user_id

    assert column.nullable is True
    assert column.unique is True


def test_purchase_schema():
    assert Purchase.__table__.c.purchase_id.unique is True
    assert Purchase.__table__.c.purchase_id.nullable is False
    assert Purchase.__table__.c.client_id.nullable is False


def test_campaign_schema():
    assert Campaign.__table__.c.config.nullable is True
    assert Campaign.__table__.c.status.nullable is False


def test_campaign_target_schema():
    assert CampaignTarget.__table__.c.client_id.nullable is False

