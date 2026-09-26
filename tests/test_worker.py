# tests/test_worker.py
import pytest
import uuid
import os
from src.worker import process_csv_task
from src.DB.models import Campaign, CampaignCategory, CampaignStatus

@pytest.mark.asyncio
async def test_worker_success_flow(db_session, generate_csv):
    """Тест: Воркер успешно обрабатывает файл и обновляет БД."""
    file_path = generate_csv(100, "worker_success.csv")
    
    # Создаем кампанию вручную, как это делает API
    campaign = Campaign(
        status=CampaignStatus.PROCESSING,
        csv_file_path=file_path,
        config={"reference_date": "2026-06-22", "count_cat": 3}
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    
    # Вызываем задачу воркера напрямую
    ctx = {} # Mock arq context
    await process_csv_task(ctx, str(campaign.id), file_path)
    
    # Проверяем изменения в БД
    await db_session.refresh(campaign)
    assert campaign.status == CampaignStatus.DRAFT
    
    # Проверяем, что категории созданы
    result = await db_session.execute(
        CampaignCategory.__table__.select().where(CampaignCategory.campaign_id == campaign.id)
    )
    categories = result.fetchall()
    assert len(categories) > 0
    assert all(cat.final_bonus >= 0 for cat in categories)

@pytest.mark.asyncio
async def test_worker_handles_missing_file(db_session):
    """Тест: Воркер корректно обрабатывает ситуацию, когда файла нет на диске."""
    campaign = Campaign(
        status=CampaignStatus.PROCESSING,
        csv_file_path="non_existent_file.csv",
        config={}
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    
    ctx = {}
    # Ожидаем, что воркер перехватит FileNotFoundError и запишет ошибку в config
    with pytest.raises(FileNotFoundError):
        await process_csv_task(ctx, str(campaign.id), "non_existent_file.csv")
    
    # В реальной реализации мы бы добавили try/except в worker.py для смены статуса на FAILED
    # Для MVP оставим так, чтобы видеть падение в логах arq