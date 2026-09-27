# src/DB/crud.py
"""Заглушки. Заменим на реальные запросы, когда появятся модели."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# MOCK-режим: пока нет реального API лояльности — считаем ключ рабочим
MOCK_LOYALTY = True

# Список реальных URL API лояльности — впишешь позже
LOYALTY_ENDPOINTS = [
    # "https://api.iiko.ru/v1/loyalty/ping",
]


async def check_api_key(api_key: str) -> tuple[bool, Optional[str]]:
    """Возвращает (успех, найденный_endpoint)."""
    if MOCK_LOYALTY:
        logger.info("[MOCK] check_api_key -> True")
        return True, "https://mock.loyalty.local"

    # Реальный перебор (включится, когда MOCK_LOYALTY=False и есть URL в списке)
    import httpx
    async with httpx.AsyncClient(timeout=3) as client:
        for endpoint in LOYALTY_ENDPOINTS:
            try:
                resp = await client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    logger.info(f"[check_api_key] подошёл: {endpoint}")
                    return True, endpoint
            except Exception as e:
                logger.debug(f"[check_api_key] {endpoint}: {e}")
                continue

    logger.info("[check_api_key] ни один endpoint не подошёл")
    return False, None


async def get_business_by_max_id(max_user_id: str) -> Optional[dict]:
    logger.info(f"[STUB] get_business_by_max_id({max_user_id})")
    return None


async def check_client_in_loyalty(max_user_id: str) -> bool:
    logger.info(f"[STUB] check_client_in_loyalty({max_user_id})")
    return False


async def register_business(
    max_user_id: str, api_key: str, api_endpoint: str
) -> bool:
    logger.info(f"[STUB] register_business({max_user_id}, endpoint={api_endpoint})")
    return True


async def save_client_phone(max_user_id: str, phone: str) -> bool:
    logger.info(f"[STUB] save_client_phone({max_user_id})")
    return True