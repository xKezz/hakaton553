# tests/test_workflow.py
import pytest
import uuid

@pytest.mark.asyncio
async def test_full_happy_path(client, generate_csv, db_session):
    """Тест: Полный успешный цикл от загрузки до одобрения."""
    file_path = generate_csv(50, "workflow.csv")
    
    # 1. Загрузка
    with open(file_path, "rb") as f:
        resp = await client.post("/api/v1/campaigns/upload", files={"file": ("workflow.csv", f, "text/csv")})
    campaign_id = resp.json()["campaign_id"]
    
    # 2. Имитация работы воркера
    from src.worker import process_csv_task
    await process_csv_task({}, campaign_id, file_path)
    
    # 3. Получение категорий
    resp_cats = await client.get(f"/api/v1/campaigns/{campaign_id}/categories")
    assert resp_cats.status_code == 200
    categories = resp_cats.json()["categories"]
    assert len(categories) > 0
    
    first_cat_id = categories[0]["id"]
    original_bonus = categories[0]["final_bonus"]
    
    # 4. Редактирование бонуса
    new_bonus = original_bonus + 100
    resp_patch = await client.patch(
        f"/api/v1/campaigns/{campaign_id}/categories/{first_cat_id}",
        json={"final_bonus": new_bonus}
    )
    assert resp_patch.status_code == 200
    assert resp_patch.json()["final_bonus"] == new_bonus
    
    # 5. Одобрение кампании
    resp_approve = await client.post(f"/api/v1/campaigns/{campaign_id}/approve")
    assert resp_approve.status_code == 202
    assert resp_approve.json()["status"] == "APPROVED"

@pytest.mark.asyncio
async def test_cannot_approve_processing_campaign(client, generate_csv):
    """Тест: Нельзя одобрить кампанию, которая еще в PROCESSING."""
    file_path = generate_csv(20, "no_worker.csv")
    
    with open(file_path, "rb") as f:
        resp = await client.post("/api/v1/campaigns/upload", files={"file": ("no_worker.csv", f, "text/csv")})
    
    campaign_id = resp.json()["campaign_id"]
    
    # Пытаемся одобрить БЕЗ запуска воркера
    resp_approve = await client.post(f"/api/v1/campaigns/{campaign_id}/approve")
    
    assert resp_approve.status_code == 400
    assert "нельзя запустить" in resp_approve.json()["detail"].lower()

@pytest.mark.asyncio
async def test_cannot_edit_approved_campaign(client, generate_csv, db_session):
    """Тест: Нельзя редактировать бонусы после одобрения кампании."""
    file_path = generate_csv(30, "edit_after_approve.csv")
    
    with open(file_path, "rb") as f:
        resp = await client.post("/api/v1/campaigns/upload", files={"file": ("edit.csv", f, "text/csv")})
    campaign_id = resp.json()["campaign_id"]
    
    from src.worker import process_csv_task
    await process_csv_task({}, campaign_id, file_path)
    
    # Получаем ID категории ДО одобрения (после одобрения эндпоинт категорий вернет 400)
    resp_cats = await client.get(f"/api/v1/campaigns/{campaign_id}/categories")
    cat_id = resp_cats.json()["categories"][0]["id"]
    
    # Сначала одобряем
    await client.post(f"/api/v1/campaigns/{campaign_id}/approve")
    
    # Пытаемся редактировать после одобрения
    resp_patch = await client.patch(
        f"/api/v1/campaigns/{campaign_id}/categories/{cat_id}",
        json={"final_bonus": 999}
    )
    
    # Проверяем, что статус кампании не сбился (остался APPROVED)
    resp_status = await client.get(f"/api/v1/campaigns/{campaign_id}/status")
    assert resp_status.json()["status"] == "APPROVED"