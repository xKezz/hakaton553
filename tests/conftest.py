# tests/conftest.py
import sys
import os
from unittest.mock import AsyncMock, patch, MagicMock

# Добавляем корень проекта и src в пути Python (КРИТИЧНО для импортов)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'src'))

# Мокаем Redis до импорта app, чтобы startup_event не пытался подключиться
# Патчим глобальную переменную redis_pool в модуле api
mock_redis_pool = AsyncMock()
mock_redis_pool.enqueue_job = AsyncMock()
mock_redis_pool.close = AsyncMock()

# Запускаем патчи и сохраняем их для остановки позже
_patcher_redis_pool = patch('src.API.api.redis_pool', mock_redis_pool)
_patcher_create_pool = patch('arq.create_pool', return_value=mock_redis_pool)
_patcher_redis_pool.start()
_patcher_create_pool.start()

# Теперь импорты с активными патчами
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Используем SQLite в памяти для тестов (не требует внешней БД)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# Патчим async_session в DB.database для тестов
_patcher_async_session = patch('DB.database.async_session', TestingSessionLocal)
_patcher_async_session.start()

# Импорты из нашего проекта
from src.API.api import app
from src.DB.database import Base, get_db
from src.DB.models import Campaign, CampaignCategory, CampaignStatus

@pytest.fixture(scope="session")
def event_loop():
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
async def db_session():
    """Создает чистую сессию БД для каждого теста и откатывает изменения."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture(scope="function")
async def client(db_session):
    """Асинхронный HTTP-клиент для тестов FastAPI."""
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
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
                "phone": phones,
                "purchase_date": [d.strftime("%Y-%m-%d") for d in dates],
                "amount": amounts
            })
            
            # Ensure phone is written as string (prevent pandas from inferring int)
            df["phone"] = df["phone"].astype(str)
            
            # Добавим немного дубликатов для реалистичности
            if rows > 10:
                df = pd.concat([df, df.iloc[:max(1, rows//10)]], ignore_index=True)

        df.to_csv(file_path, index=False, encoding="utf-8")
        return file_path
    return _generate