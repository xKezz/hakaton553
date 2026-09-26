# tests/test_volumes.py
import pytest
import asyncio
from src.DB.models import CampaignStatus

@pytest.mark.asyncio
async def test_upload_empty_csv(client, generate_csv):
    """Тест: Загрузка пустого CSV должна корректно обрабатываться."""
    file_path = generate_csv(0, "empty.csv")
    
    with open(file_path, "rb") as f:
        response = await client.post("/api/v1/campaigns/upload", files={"file": ("empty.csv", f, "text/csv")})
    
    # API должен принять файл (202), но воркер позже должен обработать это как ошибку или 0 категорий
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "PROCESSING"

@pytest.mark.asyncio
async def test_upload_corrupted_csv(client, generate_csv, db_session):
    """Тест: Загрузка CSV без нужных колонок. Воркер должен упасть, статус не станет DRAFT."""
    file_path = generate_csv(10, "corrupt.csv", corrupt=True)
    
    with open(file_path, "rb") as f:
        response = await client.post("/api/v1/campaigns/upload", files={"file": ("corrupt.csv", f, "text/csv")})
    
    assert response.status_code == 202
    campaign_id = response.json()["campaign_id"]
    
    # Имитируем работу воркера (в реальности он сделает это сам)
    from src.worker import process_csv_task
    with pytest.raises(ValueError): # Ожидаем ошибку от ColumnFinder
        await process_csv_task({}, campaign_id, file_path)

@pytest.mark.asyncio
async def test_upload_small_dataset(client, generate_csv, db_session):
    """Тест: Маленький валидный набор данных (10 строк)."""
    file_path = generate_csv(10, "small.csv")
    
    with open(file_path, "rb") as f:
        response = await client.post("/api/v1/campaigns/upload", files={"file": ("small.csv", f, "text/csv")})
    
    assert response.status_code == 202
    campaign_id = response.json()["campaign_id"]
    
    # Запускаем воркер вручную для теста
    from src.worker import process_csv_task
    await process_csv_task({}, campaign_id, file_path)
    
    # Проверяем, что статус сменился на DRAFT
    response_status = await client.get(f"/api/v1/campaigns/{campaign_id}/status")
    assert response_status.json()["status"] == "DRAFT"
    assert response_status.json()["total_clients"] > 0

@pytest.mark.asyncio
async def test_upload_large_dataset_performance(client, generate_csv, db_session):
    """Тест: Большой набор данных (5 000 строк). Проверяем, что API не блокируется."""
    file_path = generate_csv(5000, "large.csv")
    
    # Замеряем время ответа API (должно быть < 1 секунды, так как это просто сохранение файла)
    import time
    start_time = time.time()
    
    with open(file_path, "rb") as f:
        response = await client.post("/api/v1/campaigns/upload", files={"file": ("large.csv", f, "text/csv")})
    
    elapsed = time.time() - start_time
    assert response.status_code == 202
    assert elapsed < 1.0, f"API отвечал слишком долго: {elapsed} сек"
    
    campaign_id = response.json()["campaign_id"]
    
    # Проверяем статус через API (SQLite не поддерживает UUID нативно для прямого запроса)
    response_status = await client.get(f"/api/v1/campaigns/{campaign_id}/status")
    assert response_status.json()["status"] == "PROCESSING"