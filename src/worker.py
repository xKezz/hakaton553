# src/worker.py
import sys
import os

# Добавляем папку src в пути поиска модулей
# Это нужно, чтобы pipeline.py мог импортировать data_process.loader и т.д.
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# Теперь все остальные импорты
import asyncio
import uuid
import logging
import pandas as pd
from arq.connections import RedisSettings
# ==========================================
# 1. ИМПОРТЫ ИЗ БД
# ==========================================
from DB.database import async_session
from DB.models import Campaign, CampaignCategory, CampaignStatus

# ==========================================
# 2. ИМПОРТ ТВОЕГО DS-МОДУЛЯ
# ==========================================
# Убедись, что путь правильный! Если pipeline.py в корне, то так:
from src.pipeline import Pipeline

logger = logging.getLogger(__name__)

# ==========================================
# 3. ГЛАВНАЯ ЗАДАЧА: ОБРАБОТКА CSV ЧЕРЕЗ PANDAS
# ==========================================
async def process_csv_task(ctx: dict, campaign_id: str, file_path: str):
    """
    Эту функцию забирает arq из Redis.
    Она читает CSV, прогоняет через твой Pipeline и сохраняет категории в БД.
    """
    logger.info(f"🚀 Воркер взял задачу: {campaign_id}")
    
    async with async_session() as db:
        try:
            # ШАГ A: Читаем CSV в DataFrame
            logger.info(f"📖 Читаем CSV: {file_path}")
            df = await asyncio.to_thread(pd.read_csv, file_path)
            
            # ШАГ B: Запускаем ТВОЙ пайплайн
            logger.info("🧮 Запускаем Pipeline (предобработка + расчёт бонусов)...")
            pipeline = Pipeline(df=df, default_region="RU")
            
            # Вызываем .run() в отдельном потоке (pandas блокирует event loop!)
            report = await asyncio.to_thread(
                pipeline.run,
                count_cat=5,
                reference_date="2026-06-22",
                method="win-back",
                max_bonus_points=1000,
                step=50
            )
            
            # ШАГ C: Парсим результат
            categories_data = report.get("categories", [])
            meta = report.get("meta", {})
            logger.info(f"📊 Пайплайн отработал. Найдено {len(categories_data)} категорий.")
            
            # ШАГ D: Сохраняем категории в БД (Bulk Insert)
            db_categories = []
            for cat in categories_data:
                db_cat = CampaignCategory(
                    campaign_id=uuid.UUID(campaign_id),
                    segment_group=int(cat.get("segment_group", 0)),
                    label=cat.get("label", f"cat_{cat.get('segment_group', 0)}"),
                    clients_count=int(cat.get("clients_count", 0)),
                    avg_recency=float(cat.get("avg_recency", 0.0)),
                    avg_frequency=float(cat.get("avg_frequency", 0.0)),
                    avg_monetary_score=float(cat.get("avg_monetary_score", 0.0)),
                    avg_amount=float(cat.get("avg_amount", 0.0)),
                    proposed_bonus=int(cat.get("proposed_bonus", 0)),
                    final_bonus=int(cat.get("proposed_bonus", 0)),
                    reason=str(cat.get("reason", ""))
                )
                db_categories.append(db_cat)
            
            db.add_all(db_categories)
            
            # ШАГ E: Меняем статус кампании на DRAFT
            campaign = await db.get(Campaign, uuid.UUID(campaign_id))
            if campaign:
                campaign.status = CampaignStatus.DRAFT
                if meta.get("warnings"):
                    campaign.config["warnings"] = meta["warnings"]
            
            await db.commit()
            logger.info("✅ Успех! Данные сохранены в БД.")

        except Exception as e:
            logger.error(f"❌ Ошибка в воркере: {e}")
            campaign = await db.get(Campaign, uuid.UUID(campaign_id))
            if campaign:
                campaign.config["error"] = str(e)
            await db.commit()
            raise


# ==========================================
# 4. НАСТРОЙКИ ВОРКЕРА (arq)
# ==========================================
class WorkerSettings:
    redis_settings = RedisSettings(host='localhost', port=6379)
    functions = [process_csv_task]
    job_timeout = 300  # 5 минут на задачу
    
    async def startup(ctx):
        logger.info("🟢 Воркер запущен и слушает Redis!")
        
    async def shutdown(ctx):
        logger.info("🔴 Воркер остановлен.")