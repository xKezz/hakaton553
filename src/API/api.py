# src/API/api.py
import os
import uuid
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

# --- ИМПОРТЫ ИЗ БД (ТО, ЧТО МЫ ТОЛЬКО ЧТО СОЗДАЛИ) ---
from src.DB.database import get_db
from src.DB.models import Campaign, CampaignCategory, CampaignStatus
from src.DB.schemas import (
    CampaignUploadResponse, CampaignStatusResponse, CategoriesListResponse, 
    CategoryOut, CategoryUpdate, CategoryUpdateResponse, ApproveResponse
)

# ==========================================
# 1. НАСТРОЙКА ПРИЛОЖЕНИЯ FASTAPI
# ==========================================
app = FastAPI(title="Win-Back MAX MVP API", version="1.0.0")

# Разрешаем запросы из мини-аппа (CORS) - критично для MAX!
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# Глобальная переменная для подключения к Redis (очереди задач)
redis_pool: ArqRedis = None

# При запуске API подключаемся к Redis
@app.on_event("startup")
async def startup_event():
    global redis_pool
    # Подключаемся к Redis, который ты запустил (порт 6379)
    redis_pool = await create_pool(RedisSettings(host='localhost', port=6379))

# При остановке API закрываем соединение
@app.on_event("shutdown")
async def shutdown_event():
    if redis_pool:
        await redis_pool.close()

# ==========================================
# 2. ЗАГЛУШКА АВТОРИЗАЦИИ (Для MVP)
# ==========================================
async def get_current_admin():
    # В реальности здесь будет проверка initData от MAX.
    # Пока просто возвращаем "тестового" админа, чтобы эндпоинты работали.
    return {"id": "test-admin", "role": "owner"}

# ==========================================
# 3. ЭНДПОИНТЫ: КАМПАНИИ (ЭТАП 1 И 2)
# ==========================================

@app.post("/api/v1/campaigns/upload", response_model=CampaignUploadResponse, status_code=202)
async def upload_campaign(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    # 1. Сохраняем CSV на диск (чтобы не грузить оперативную память)
    file_path = f"data/source_files/{uuid.uuid4()}_{file.filename}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(await file.read())

    # 2. Создаем запись в БД со статусом "В процессе"
    new_campaign = Campaign(
        status=CampaignStatus.PROCESSING,
        csv_file_path=file_path,
        config={"reference_date": "2026-06-22", "count_cat": 5} # Временный конфиг
    )
    db.add(new_campaign)
    await db.commit()
    await db.refresh(new_campaign)

    # 3. Кидаем задачу в Redis! Воркер подхватит и запустит твой pandas.
    await redis_pool.enqueue_job(
        'process_csv_task',      # Имя функции в worker.py
        str(new_campaign.id),    # Аргумент 1: ID кампании
        file_path                # Аргумент 2: Путь к файлу
    )

    # 4. Мгновенно отвечаем фронту, что всё ок
    return CampaignUploadResponse(
        campaign_id=new_campaign.id,
        status=new_campaign.status,
        message="Файл принят, идёт обработка"
    )


@app.get("/api/v1/campaigns/{campaign_id}/status", response_model=CampaignStatusResponse)
async def get_campaign_status(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    
    extra_data = {}
    # Если расчет закончен, считаем кол-во категорий и клиентов для фронта
    if campaign.status == CampaignStatus.DRAFT:
        cats = await db.execute(
            select(CampaignCategory).where(CampaignCategory.campaign_id == campaign_id)
        )
        categories = cats.scalars().all()
        extra_data["categories_count"] = len(categories)
        extra_data["total_clients"] = sum(c.clients_count for c in categories)

    return CampaignStatusResponse(
        campaign_id=campaign.id, 
        status=campaign.status, 
        **extra_data
    )


@app.get("/api/v1/campaigns/{campaign_id}/categories", response_model=CategoriesListResponse)
async def get_categories(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    if campaign.status != CampaignStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Кампания еще не готова к просмотру")

    result = await db.execute(
        select(CampaignCategory)
        .where(CampaignCategory.campaign_id == campaign_id)
        .order_by(CampaignCategory.segment_group)
    )
    categories = result.scalars().all()

    return CategoriesListResponse(
        campaign_id=campaign.id,
        ttl_days=campaign.ttl_days,
        categories=[CategoryOut.model_validate(c) for c in categories]
    )


# ==========================================
# 4. ЭНДПОИНТЫ: РЕДАКТИРОВАНИЕ И ЗАПУСК (ЭТАП 2 И 3)
# ==========================================

@app.patch("/api/v1/campaigns/{campaign_id}/categories/{cat_id}", response_model=CategoryUpdateResponse)
async def update_category_bonus(
    campaign_id: uuid.UUID,
    cat_id: uuid.UUID,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db)
):
    category = await db.get(CampaignCategory, cat_id)
    if not category or category.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    # Бизнес меняет бонус
    category.final_bonus = data.final_bonus
    await db.commit()

    return CategoryUpdateResponse(
        id=category.id,
        final_bonus=category.final_bonus,
        total_budget_impact=category.final_bonus * category.clients_count
    )


@app.post("/api/v1/campaigns/{campaign_id}/approve", response_model=ApproveResponse, status_code=202)
async def approve_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign or campaign.status != CampaignStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Нельзя запустить кампанию в этом статусе")

    # Меняем статус на APPROVED
    campaign.status = CampaignStatus.APPROVED
    await db.commit()

    # TODO: Завтра сюда добавим задачу в Redis на начисление бонусов (accrue_bonuses_task)

    return ApproveResponse(
        campaign_id=campaign.id,
        status=campaign.status,
        total_clients=0, # Посчитается в воркере
        total_bonus_budget=0,
        message="Кампания запущена. Начисление бонусов в процессе."
    )

# ==========================================
# 5. ВЕБХУКИ (ЭТАП 4)
# ==========================================

@app.post("/api/v1/webhooks/loyalty-spend")
async def loyalty_spend_webhook(payload: dict, db: AsyncSession = Depends(get_db)):
    # TODO: Завтра напишем логику: найти транзакцию по external_txn_id и поменять статус на USED
    return {"status": "ok", "message": "Webhook received"}