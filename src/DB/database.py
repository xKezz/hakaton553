from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import os

DATABASE_URL = "postgresql+asyncpg://admin:admin123@localhost:5432/winback_db"

# Создаем "движок" (менеджер соединений)
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

# Создаем фабрику сессий (каждый запрос к БД будет идти через свою сессию)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Базовый класс, от которого будут наследоваться все таблицы
class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()