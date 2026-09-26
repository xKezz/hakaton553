# tests/conftest.py
import sys
import os

# Добавляем корень проекта в пути Python (КРИТИЧНО для импортов)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Импорты из нашего проекта
from src.API.api import app
from src.DB.database import Base, get_db, DATABASE_URL
from src.DB.models import Campaign, CampaignCategory, CampaignStatus

# Используем тестовую БД или основную с очисткой (для хакатона проще основную с rollback)
TEST_DATABASE_URL = DATABASE_URL.replace("winback_db", "winback_db_test") 
# Если тестовой БД нет, можно использовать основную, но лучше создать winback_db_test вручную

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@pytest.fixture(scope="session")
def event_loop():
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
async def db_session():
    """Создает чистую сессию БД для каждого теста и откатывает изменения."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture(scope="function")
async def client(db_session):
    """Асинхронный HTTP-клиент для тестов FastAPI."""
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest.fixture
def generate_csv():
    """Фабрика для создания CSV-файлов разного объема."""
    def _generate(rows: int, filename: str = "test_data.csv", corrupt: bool = False) -> str:
        file_path = f"tests/fixtures/{filename}"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        if corrupt and rows > 0:
            # Создаем битый файл (нет нужных колонок)
            df = pd.DataFrame({"wrong_col": [1, 2], "bad_date": ["a", "b"]})
        else:
            # Генерируем валидные данные для ColumnFinder
            end_date = datetime(2026, 6, 22)
            dates = [end_date - timedelta(days=np.random.randint(10, 200)) for _ in range(rows)]
            amounts = np.random.uniform(100.0, 5000.0, rows).round(2)
            phones = [f"+7999{np.random.randint(1000000, 9999999)}" for _ in range(rows)]
            
            df = pd.DataFrame({
                "client_id": phones,
                "purchase_date": [d.strftime("%Y-%-%d") for d in dates],
                "amount": amounts
            })
            
            # Добавим немного дубликатов для реалистичности
            if rows > 10:
                df = pd.concat([df, df.iloc[:max(1, rows//10)]], ignore_index=True)

        df.to_csv(file_path, index=False, encoding="utf-8")
        return file_path
    return _generate