# src/Bot/bot.py
"""
Принципы работы:

* аналитика не переписывается — используется :class:`src.pipeline.Pipeline`;
* работа с БД идёт только через ``src.DB.database.async_session``
  (второго engine нет);
* реального Loyalty API в репозитории нет, поэтому внешняя программа
  лояльности заменена CSV-эмулятором ``src.services.loyalty_mock``
  (``LoyaltyMockAdapter``): баланс, начисления, отзывы и «поведение
  клиентов» (визиты, списания бонусов). Бот не знает, что это мок —
  он работает через интерфейс :class:`LoyaltyAdapter`.

Запуск::

    MAX_BOT_TOKEN=... ADMIN_IDS=123456,789012 python -m src.Bot.bot
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update

from src.DB.crud import (
    create_campaign_targets,
    create_client,
    get_campaign,
    get_campaign_categories,
    get_campaign_targets,
    get_client_by_max_id,
    get_client_by_phone,
    get_latest_campaign,
    update_campaign_status,
    update_category_bonus,
    update_target_bonus_realised,
)
from src.DB.database import async_session
from src.DB.models import (
    Campaign,
    CampaignCategory,
    CampaignStatus,
    CampaignTarget,
    Client,
)
from src.data_process.predprocessing import Preprocessor
from src.pipeline import Pipeline

# =========================================================
# 0. .env и maxapi
# =========================================================

with contextlib.suppress(Exception):
    # python-dotenv необязателен: без него берём переменные окружения.
    from dotenv import load_dotenv

    load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("winback.bot")


MAXAPI_AVAILABLE = True
MAXAPI_IMPORT_ERROR: Exception | None = None

try:
    from maxapi import Bot, Dispatcher, F
    from maxapi.context import MemoryContext, State, StatesGroup
    from maxapi.filters.command import Command, CommandStart
    from maxapi.types import (
        BotStarted,
        CallbackButton,
        MessageCallback,
        InputMediaBuffer,
        MessageCreated,
        RequestContactButton,
    )
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
except ImportError as exc: 
    MAXAPI_AVAILABLE = False
    MAXAPI_IMPORT_ERROR = exc

    class State:  # type: ignore[no-redef]
        """Минимальная замена maxapi.context.State."""

        def __init__(self) -> None:
            self.name: str | None = None

        def __set_name__(self, owner: type, attr_name: str) -> None:
            self.name = f"{owner.__name__}:{attr_name}"

        def __str__(self) -> str:
            return self.name or ""

        def __eq__(self, value: object, /) -> bool:
            if isinstance(value, State):
                return self.name == value.name
            if isinstance(value, str):
                return self.name == value
            return NotImplemented

    class StatesGroup:  # type: ignore[no-redef]
        """Минимальная замена maxapi.context.StatesGroup."""

        @classmethod
        def states(cls) -> list[str]:
            found: list[str] = []
            for owner in cls.__mro__:
                for value in vars(owner).values():
                    if isinstance(value, State) and str(value) not in found:
                        found.append(str(value))
            return found

    class MemoryContext:  # type: ignore[no-redef]
        """Минимальная замена maxapi.context.MemoryContext."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._state: Any = None
            self._data: dict[str, Any] = {}

        async def set_state(self, state: Any = None) -> None:
            self._state = state

        async def get_state(self) -> Any:
            return self._state

        async def clear(self) -> None:
            self._state = None
            self._data = {}

        async def get_data(self) -> dict[str, Any]:
            return dict(self._data)

        async def update_data(self, **kwargs: Any) -> dict[str, Any]:
            self._data.update(kwargs)
            return dict(self._data)

    class _AnyFilter:  # type: ignore[no-redef]
        """Заглушка F-фильтра: принимает любое обращение к атрибутам."""

        def __getattr__(self, item: str) -> "_AnyFilter":
            return self

        def __call__(self, *args: Any, **kwargs: Any) -> "_AnyFilter":
            return self

        def __bool__(self) -> bool:
            return True

    F = _AnyFilter()  # type: ignore[assignment]

    class Command:  # type: ignore[no-redef]
        def __init__(self, name: str, *args: Any, **kwargs: Any) -> None:
            self.name = name

    class CommandStart(Command):  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__("start")

    class CallbackButton:  # type: ignore[no-redef]
        def __init__(
            self, *, text: str = "", payload: str | None = None, **kwargs: Any
        ) -> None:
            self.text = text
            self.payload = payload

    class RequestContactButton:  # type: ignore[no-redef]
        def __init__(self, *, text: str = "", **kwargs: Any) -> None:
            self.text = text

    class InputMediaBuffer:  # type: ignore[no-redef]
        def __init__(
            self, *, buffer: bytes = b"", filename: str = "", **kwargs: Any
        ) -> None:
            self.buffer = buffer
            self.filename = filename

    class InlineKeyboardBuilder:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.payload: list[list[Any]] = [[]]

        def row(self, *buttons: Any) -> "InlineKeyboardBuilder":
            if not self.payload[-1]:
                self.payload[-1].extend(buttons)
            else:
                self.payload.append(list(buttons))
            return self

        def add(self, *buttons: Any) -> "InlineKeyboardBuilder":
            self.payload[-1].extend(buttons)
            return self

        def as_markup(self) -> dict[str, Any]:
            return {"buttons": self.payload}

    class _NullEvent:  # type: ignore[no-redef]
        """Заглушка декоратора события."""

        def __call__(self, *args: Any, **kwargs: Any):
            def decorator(func):
                return func

            return decorator

        def register(self, func, *args: Any, **kwargs: Any):
            return func

    class Dispatcher:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.message_created = _NullEvent()
            self.message_callback = _NullEvent()
            self.bot_started = _NullEvent()
            self.errors = _NullEvent()
            self.error = self.errors

        async def start_polling(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(
                "maxapi не установлен: "
                f"{MAXAPI_IMPORT_ERROR!r}. Установите maxapi для запуска бота."
            )

    class Bot:  # type: ignore[no-redef]
        def __init__(self, token: str | None = None, **kwargs: Any) -> None:
            self.token = token

        async def send_message(self, **kwargs: Any) -> None:
            raise RuntimeError("maxapi не установлен: отправка недоступна.")

        async def download_bytes(self, url: str) -> bytes:
            raise RuntimeError("maxapi не установлен: скачивание недоступно.")

    class MessageCreated:  # type: ignore[no-redef]
        ...

    class MessageCallback:  # type: ignore[no-redef]
        ...

    class BotStarted:  # type: ignore[no-redef]
        ...


# =========================================================
# 1. Конфигурация
# =========================================================

def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Читает целое из окружения с валидацией (не падает на мусоре)."""

    raw = os.getenv(name)

    if raw is None or not str(raw).strip():
        return default

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r не число, беру %s", name, raw, default)
        return default

    if minimum is not None and value < minimum:
        logger.warning("%s=%s меньше %s, беру %s", name, value, minimum, minimum)
        return minimum

    if maximum is not None and value > maximum:
        logger.warning("%s=%s больше %s, беру %s", name, value, maximum, maximum)
        return maximum

    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)

    if raw is None or not str(raw).strip():
        return default

    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r не число, беру %s", name, raw, default)
        return default

    if minimum is not None and value < minimum:
        return minimum

    return value


BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "test_token_for_dev")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES_DIR = PROJECT_ROOT / "data" / "source_files"
LOYALTY_MOCK_DIR = Path(
    os.getenv("LOYALTY_MOCK_DIR") or (PROJECT_ROOT / "data" / "loyalty_mock")
)

# Бонусы: 0..300 с шагом 50; группа 4 бонус не получает.
MIN_BONUS = 0
MAX_BONUS = 300
BONUS_STEP = 50
STABLE_GROUP = 4

CAMPAIGN_DAYS = _env_int(
    "CAMPAIGN_DAYS", Pipeline.DEFAULT_CAMPAIGN_DAYS, minimum=1, maximum=365
)
CSV_MAX_BYTES = _env_int("MAX_CSV_SIZE_BYTES", 20 * 1024 * 1024, minimum=1024)
# Лимит строк держим ниже предела параметров asyncpg (32767): в
# src/DB/crud.py запросы `IN (...)` по покупкам не чанкованы, и больший
# CSV упадёт на InterfaceError. Менять crud.py нельзя.
CSV_MAX_ROWS = _env_int("MAX_CSV_ROWS", 30_000, minimum=1, maximum=32_767)
CAMPAIGN_FINISH_CHECK_INTERVAL = _env_int(
    "CAMPAIGN_FINISH_CHECK_INTERVAL", 60, minimum=5
)
SEND_DELAY_SECONDS = _env_float("CAMPAIGN_SEND_DELAY_SECONDS", 0.05, minimum=0.0)
SEND_STATUS_RECHECK_EVERY = 25
CLIENT_ID_CHUNK = 1000
MAX_MESSAGE_LENGTH = 3900

GROUP_TITLES = {
    0: "Группа 0 — высший приоритет",
    1: "Группа 1 — высокий приоритет",
    2: "Группа 2 — средний приоритет",
    3: "Группа 3 — низкий приоритет",
    4: "Группа 4 — стабильные",
}

LAUNCH_YES = {"да", "yes", "y", "запускай", "запустить", "подтверждаю"}
LAUNCH_NO = {"нет", "no", "n", "отмена", "cancel"}


def parse_admin_ids(raw: str | None) -> set[str]:
    """Разбирает ADMIN_IDS (запятая/точка с запятой/пробел/таб/перевод строки)."""

    if not raw:
        return set()

    normalized = (
        str(raw)
        .replace(";", ",")
        .replace("\t", ",")
        .replace("\r", ",")
        .replace("\n", ",")
        .replace(" ", ",")
    )

    return {item.strip() for item in normalized.split(",") if item.strip()}


ADMIN_IDS = parse_admin_ids(os.getenv("ADMIN_IDS") or os.getenv("ADMIN_MAX_IDS"))


def is_admin(user_id: Any) -> bool:
    """Является ли пользователь MAX администратором бизнеса."""

    return str(user_id).strip() in ADMIN_IDS


# =========================================================
# 2. Loyalty API -> CSV-эмулятор
# =========================================================

class LoyaltyAdapter:
    """Интерфейс к внешней программе лояльности.

    Реализация по умолчанию — CSV-эмулятор
    (:class:`src.services.loyalty_mock.LoyaltyMockAdapter`).
    Когда появится реальный Loyalty API, достаточно заменить фабрику
    :func:`build_loyalty_adapter`; логику бота править не нужно.
    """

    async def fetch_balance(self, phone_e164: str) -> int | None:
        """Баланс бонусов клиента."""

        raise NotImplementedError

    async def accrue_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
        recency: int | None = None,
        frequency: int | None = None,
        avg_amount: float | None = None,
        issued_at: Any = None,
        ends_at: Any = None,
    ) -> None:
        """Начислить бонус при запуске кампании."""

        raise NotImplementedError

    async def revoke_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
    ) -> int | None:
        """Отозвать нереализованный бонус после кампании.

        Возвращает фактически отозванную сумму (``None``, если адаптер
        её не сообщает).
        """

        raise NotImplementedError

    async def fetch_realised(
        self,
        *,
        campaign_id: int,
        target_id: int,
        phone_e164: str,
        bonus_amount: int,
        issued_at: Any,
        ends_at: Any,
    ) -> int | None:
        """Сколько бонуса клиент реально списал за период кампании.

        ``None`` — данных за период нет; ``0`` — визиты были, списаний
        не было; ``> 0`` — клиент вернулся и потратил бонус.
        """

        return None

    async def advance(self, now: Any = None) -> int:
        """Продвинуть эмуляцию «жизни бизнеса» до ``now``."""

        return 0


class StubLoyaltyAdapter(LoyaltyAdapter):
    """Аварийная заглушка, если CSV-эмулятор недоступен.

    Ничего не начисляет и не отзывает, только пишет технический лог.
    """

    async def fetch_balance(self, phone_e164: str) -> int | None:
        logger.info("[LOYALTY STUB] Баланс %s недоступен", _mask_phone(phone_e164))
        return None

    async def accrue_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
        **kwargs: Any,
    ) -> None:
        logger.info(
            "[LOYALTY STUB] Начисление %s: phone=%s, campaign=%s, target=%s",
            amount,
            _mask_phone(phone_e164),
            campaign_id,
            target_id,
        )

    async def revoke_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
    ) -> int | None:
        logger.info(
            "[LOYALTY STUB] Отзыв %s: phone=%s, campaign=%s, target=%s",
            amount,
            _mask_phone(phone_e164),
            campaign_id,
            target_id,
        )

        return 0


def build_loyalty_adapter() -> LoyaltyAdapter:
    """Фабрика адаптера программы лояльности."""

    try:
        from src.services.loyalty_mock import LoyaltyMockAdapter

        return LoyaltyMockAdapter(data_dir=LOYALTY_MOCK_DIR)
    except Exception as exc:  # pragma: no cover - зависит от окружения
        logger.error(
            "CSV-эмулятор Loyalty недоступен (%r); работаю на заглушке", exc
        )
        return StubLoyaltyAdapter()


loyalty: LoyaltyAdapter = build_loyalty_adapter()


# =========================================================
# 3. Бот, диспетчер, состояния
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class ClientStates(StatesGroup):
    """Состояния клиентского флоу."""

    waiting_phone = State()


class AdminStates(StatesGroup):
    """Состояния административного флоу."""

    waiting_csv = State()
    confirming_launch = State()


# =========================================================
# 4. БД
# =========================================================

@asynccontextmanager
async def session_scope():
    """Сессия PostgreSQL с commit/rollback (общий async_session)."""

    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _load_clients_by_ids(session, client_ids: list[int]) -> dict[int, Client]:
    """Загружает клиентов чанками (лимит параметров запроса)."""

    unique_ids = sorted({int(item) for item in client_ids if item is not None})

    if not unique_ids:
        return {}

    clients: dict[int, Client] = {}

    for start in range(0, len(unique_ids), CLIENT_ID_CHUNK):
        chunk = unique_ids[start : start + CLIENT_ID_CHUNK]
        result = await session.execute(select(Client).where(Client.id.in_(chunk)))

        for client in result.scalars().all():
            clients[client.id] = client

    return clients


async def _campaign_is_active(campaign_id: int) -> bool:
    """Лёгкая проверка: кампания ещё APPROVED и не истекла."""

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Campaign.status, Campaign.campaign_ends_at).where(
                    Campaign.id == campaign_id
                )
            )
        ).first()

    if row is None:
        return False

    status, ends_at = row

    return (
        status == CampaignStatus.APPROVED.value
        and ends_at is not None
        and ends_at > _utcnow()
    )


async def _stale_campaign_message(campaign_id: int) -> str | None:
    """Проверяет, что кнопка относится к актуальной кампании."""

    async with session_scope() as session:
        latest = await get_latest_campaign(session)

    if latest is None:
        return "Кампании пока нет. Загрузите CSV."

    if latest.id != campaign_id:
        return (
            f"Карточка устарела: актуальна кампания #{latest.id}. "
            "Откройте «📋 Кампания» заново."
        )

    return None


async def _campaign_id_of_category(category_id: int) -> int | None:
    """Кампания, которой принадлежит категория (для защиты от старых карточек)."""

    async with session_scope() as session:
        row = (
            await session.execute(
                select(CampaignCategory.campaign_id).where(
                    CampaignCategory.id == category_id
                )
            )
        ).first()

    return int(row[0]) if row is not None else None


async def _campaign_send_state(campaign_id: int) -> dict[str, Any]:
    """Статус кампании и признак «уже рассылали» (без тяжёлых связей)."""

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Campaign.status, Campaign.config).where(
                    Campaign.id == campaign_id
                )
            )
        ).first()

    if row is None:
        return {"status": None, "offers_sent": None}

    return {
        "status": row[0],
        "offers_sent": (row[1] or {}).get("offers_sent"),
    }


# =========================================================
# 5. Общие хелперы
# =========================================================

def _mask_phone(phone: Any) -> str:
    """Маскирует телефон для логов (PII не пишем)."""

    value = str(phone or "").strip()

    if len(value) <= 4:
        return "***"

    return f"***{value[-4:]}"


def _mask_id(value: Any) -> str:
    """Маскирует MAX user_id/chat_id для логов (персональные данные)."""

    text = str(value or "").strip()

    if not text:
        return "***"

    if len(text) <= 4:
        return "***"

    return f"***{text[-4:]}"


def _user_id(event: Any) -> str:
    """MAX user_id инициатора события.

    Для callback приоритет у ``callback.user``: в ``message.sender``
    лежит сам бот, а не нажавший.
    """

    callback = getattr(event, "callback", None)

    if callback is not None:
        user = getattr(callback, "user", None)

        if user is not None:
            return str(user.user_id)

    message = getattr(event, "message", None)

    if message is not None and getattr(message, "sender", None) is not None:
        return str(message.sender.user_id)

    user = getattr(event, "user", None)

    if user is not None:
        return str(user.user_id)

    return ""


def _chat_id(event: Any) -> int | None:
    """MAX chat_id из события."""

    message = getattr(event, "message", None)

    if message is not None:
        recipient = getattr(message, "recipient", None)

        if recipient is not None:
            return recipient.chat_id

    return getattr(event, "chat_id", None)


def _callback_target(event: Any) -> tuple[int | None, int | None]:
    """Куда отвечать, если исходное сообщение callback недоступно."""

    callback = getattr(event, "callback", None)
    user = getattr(callback, "user", None) if callback is not None else None

    return _chat_id(event), _to_int(getattr(user, "user_id", None))


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _group_int(value: Any) -> int | None:
    """segment_group хранится строкой."""

    return _to_int(value)


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"

    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"

    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except (AttributeError, ValueError):
        return str(value)


def _utcnow() -> datetime:
    """Текущее время в naive UTC (в БД время хранится без таймзоны).

    Заменяет deprecated ``_utcnow()`` без изменения семантики.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_datetime(value: Any) -> datetime | None:
    """Приводит ISO-строку/date к naive datetime (для config кампании)."""

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Режет длинный текст на части (лимит MAX — 4000 символов).

    Содержимое сохраняется без потерь: ``"".join(части) == text``.
    """

    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text

    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)

        if cut <= 0:
            cut = limit
        elif remaining[cut] == "\n":
            cut += 1

        parts.append(remaining[:cut])
        remaining = remaining[cut:]

    if remaining:
        parts.append(remaining)

    return parts or [text[:limit]]


async def _answer(
    event: Any,
    text: str,
    attachments: list[Any] | None = None,
) -> bool:
    """Отвечает на сообщение, безопасно режа длинный текст."""

    message = getattr(event, "message", None)

    if message is None:
        return False

    ok = True

    for index, part in enumerate(_split_text(text)):
        try:
            await message.answer(
                part,
                attachments=attachments if index == 0 else [],
            )
        except Exception as exc:
            logger.error("Ошибка ответа на сообщение: %r", exc)
            ok = False

    return ok


async def _outgoing(
    text: str,
    attachments: list[Any] | None = None,
    chat_id: int | None = None,
    user_id: int | str | None = None,
) -> bool:
    """Отправка сообщения в MAX. Ошибки логируются, не пробрасываются."""

    if chat_id is None and user_id is None:
        logger.warning("Некуда отправлять сообщение: нет chat_id/user_id")
        return False

    ok = True

    for index, part in enumerate(_split_text(text)):
        try:
            await bot.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text=part,
                attachments=attachments if index == 0 else [],
            )
        except Exception as exc:
            logger.error(
                "Ошибка отправки (chat_id=%s, user_id=%s): %r",
                _mask_id(chat_id),
                _mask_id(user_id),
                exc,
            )
            ok = False

    return ok


async def _notify_admins(text: str) -> None:
    """Техническая рассылка администраторам (отчёты, итоги)."""

    for admin_id in sorted(ADMIN_IDS):
        uid = _to_int(admin_id)

        if uid is None:
            logger.warning("Некорректный ADMIN_IDS: %r", admin_id)
            continue

        await _outgoing(text, user_id=uid)


async def _safe_ack(event: Any, notification: str | None = None) -> bool:
    """Подтверждает callback, не меняя исходное сообщение."""

    try:
        result = await event.ack(notification=notification)
    except Exception as exc:
        logger.warning("Не удалось подтвердить callback: %r", exc)
        return False

    if getattr(result, "success", True) is False:
        logger.warning(
            "Callback отклонён API: %s", getattr(result, "message", None)
        )
        return False

    return True


async def _safe_edit(
    event: Any,
    text: str,
    attachments: list[Any] | None = None,
    notification: str | None = None,
) -> bool:
    """Обновляет сообщение callback.

    Дублирующее сообщение отправляется только если исходное сообщение
    недоступно или API явно отклонил правку. При сетевой ошибке новое
    сообщение НЕ отправляется (иначе возможны дубли).
    """

    message = getattr(event, "message", None)

    if message is None:
        if notification:
            await _safe_ack(event, notification)

        chat_id, user_id = _callback_target(event)

        return await _outgoing(text, attachments, chat_id=chat_id, user_id=user_id)

    try:
        result = await event.edit(
            text=text,
            attachments=attachments,
            notification=notification,
        )
    except Exception as exc:
        if isinstance(exc, ValueError):
            logger.warning("Edit невозможен (сообщение недоступно): %r", exc)

            if notification:
                await _safe_ack(event, notification)

            chat_id, user_id = _callback_target(event)

            return await _outgoing(
                text, attachments, chat_id=chat_id, user_id=user_id
            )

        logger.error("Ошибка обновления сообщения callback: %r", exc)
        await _safe_ack(event, "Не удалось обновить сообщение. Откройте меню заново.")
        return False

    if getattr(result, "success", True) is False:
        logger.warning("Edit отклонён API: %s", getattr(result, "message", None))

        if notification:
            await _safe_ack(event, notification)

        chat_id, user_id = _callback_target(event)

        return await _outgoing(text, attachments, chat_id=chat_id, user_id=user_id)

    return True


async def _send_from_callback(
    event: Any,
    user_id: str,
    text: str,
    attachments: list[Any] | None = None,
) -> bool:
    """Отправляет НОВОЕ сообщение в ответ на callback."""

    if getattr(event, "message", None) is not None:
        try:
            result = await event.send(text=text, attachments=attachments or [])

            if getattr(result, "success", True) is not False:
                return True

            logger.warning(
                "Send из callback отклонён API: %s",
                getattr(result, "message", None),
            )
        except Exception as exc:
            logger.warning("event.send недоступен: %r", exc)

    return await _outgoing(text, attachments, user_id=_to_int(user_id))


_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro: Any, *, label: str) -> asyncio.Task:
    """Создаёт фоновую задачу, удерживая ссылку и логируя ошибки."""

    task = asyncio.create_task(coro)
    _bg_tasks.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        _bg_tasks.discard(finished)

        if finished.cancelled():
            return

        error = finished.exception()

        if error is not None:
            logger.error(
                "Фоновая задача %s завершилась ошибкой: %r",
                label,
                error,
                exc_info=error,
            )

    task.add_done_callback(_on_done)

    return task


# =========================================================
# 6. Клавиатуры
# =========================================================

def phone_keyboard():
    """Клавиатура запроса номера телефона."""

    builder = InlineKeyboardBuilder()
    builder.row(RequestContactButton(text="📱 Отправить мой номер"))

    return builder.as_markup()


def admin_menu_keyboard(campaign_id: int | None = None):
    """Главное меню администратора (опасные кнопки — с id кампании)."""

    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="📥 Загрузить CSV", payload="adm:upload"))
    builder.row(
        CallbackButton(text="📋 Кампания", payload="adm:campaign"),
        CallbackButton(text="🧮 Категории", payload="adm:categories"),
    )

    if campaign_id is not None:
        builder.row(
            CallbackButton(text="🚀 Запустить кампанию", payload=f"adm:launch:{campaign_id}")
        )

    builder.row(CallbackButton(text="📊 Отчёт", payload="adm:report"))
    builder.row(
        CallbackButton(
            text="📤 Выгрузить историю кампаний (CSV)", payload="adm:export"
        )
    )

    return builder.as_markup()


def client_menu_keyboard():
    """Меню клиента."""

    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🎁 Персональные предложения", payload="cli:offer"))
    builder.row(
        CallbackButton(text="💳 Бонусы", payload="cli:balance"),
        CallbackButton(text="ℹ️ Программа", payload="cli:program"),
    )

    return builder.as_markup()


def back_keyboard() -> list[Any]:
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🏠 Меню", payload="cli:menu"))

    return [builder.as_markup()]


# =========================================================
# 7. Админ: представления
# =========================================================

NO_CAMPAIGN_TEXT = "Кампании пока нет. Загрузите CSV с покупками."

CSV_PROMPT = "📥 Пришлите CSV-файл с покупками как документ (расширение .csv)."


def _format_category_block(category: CampaignCategory) -> str:
    group = _group_int(category.segment_group)
    title = GROUP_TITLES.get(group, f"Группа {category.segment_group}")

    return (
        f"{title}\n"
        f"Клиентов: {int(category.clients_count)}\n"
        f"Средняя давность последнего посещения: {_fmt(category.avg_recency)} дн.\n"
        f"Средний чек: {_fmt(category.avg_amount)} ₽\n"
        f"Предлагаемый бонус: {int(category.proposed_bonus)}\n"
        f"Итоговый бонус: {int(category.final_bonus)}"
    )


async def build_admin_menu() -> tuple[str, list[Any]]:
    """Меню администратора со сводкой по текущей кампании."""

    async with session_scope() as session:
        campaign = await get_latest_campaign(session)

        if campaign is None:
            return f"🛠 Админ-меню\n\n{NO_CAMPAIGN_TEXT}", [
                admin_menu_keyboard(None)
            ]

        targets = await get_campaign_targets(session, campaign.id)
        recipients = sum(
            1 for target in targets if int(target.bonus_amount or 0) > 0
        )

        text = (
            "🛠 Админ-меню\n\n"
            f"Кампания #{campaign.id}: {campaign.status}\n"
            f"Получателей: {recipients} из {campaign.total_clients} клиентов\n"
            f"Окончание: {_fmt_dt(campaign.campaign_ends_at)}\n"
        )
        campaign_id = campaign.id

    return text, [admin_menu_keyboard(campaign_id)]


async def build_campaign_card() -> tuple[str, list[Any]]:
    """Карточка текущей кампании."""

    async with session_scope() as session:
        campaign = await get_latest_campaign(session)

        if campaign is None:
            return NO_CAMPAIGN_TEXT, [admin_menu_keyboard(None)]

        targets = await get_campaign_targets(session, campaign.id)
        recipients = sum(
            1 for target in targets if int(target.bonus_amount or 0) > 0
        )

        text = (
            f"📋 Кампания #{campaign.id}\n"
            f"Статус: {campaign.status}\n"
            f"Файл: {campaign.source_file_path or '—'}\n"
            f"Клиентов в выборке: {campaign.total_clients}\n"
            f"Получателей (targets с бонусом > 0): {recipients}\n"
            f"Создана: {_fmt_dt(campaign.launched_at)}\n"
            f"Окончание: {_fmt_dt(campaign.campaign_ends_at)}"
        )
        campaign_id = campaign.id

    return text, [admin_menu_keyboard(campaign_id)]


async def build_campaigns_history_csv() -> tuple[int, bytes]:
    """CSV со сводкой по ВСЕМ кампаниям (чтобы не забивать чат).

    Возвращает ``(количество кампаний, байты файла)``. Файл в utf-8-sig,
    разделитель ``;`` — так Excel открывает его без настроек.
    """

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")

    writer.writerow(
        [
            "campaign_id",
            "status",
            "created_at",
            "ends_at",
            "source_file",
            "total_clients",
            "targets",
            "delivered",
            "bonus_issued",
            "bonus_realised",
            "bonus_revoked",
            "conversion_percent",
        ]
    )

    async with session_scope() as session:
        campaigns = list(
            (await session.execute(select(Campaign).order_by(Campaign.id)))
            .scalars()
            .all()
        )

        for campaign in campaigns:
            targets = await get_campaign_targets(session, campaign.id)
            clients = await _load_clients_by_ids(
                session, [target.client_id for target in targets]
            )

            offers = 0
            delivered = 0
            issued = 0
            realised = 0
            to_revoke = 0
            realised_count = 0

            for target in targets:
                amount = int(target.bonus_amount or 0)

                if amount <= 0:
                    continue

                offers += 1
                client = clients.get(target.client_id)

                if client is None or not client.max_user_id:
                    continue

                delivered += 1
                issued += amount

                target_realised = int(target.bonus_realised or 0)
                realised += target_realised

                if target_realised > 0:
                    realised_count += 1

                if target_realised < amount:
                    to_revoke += amount - target_realised

            revoked = to_revoke

            if campaign.status == CampaignStatus.COMPLETED.value:
                stored = (campaign.config or {}).get("revoked_total")

                if stored is not None:
                    revoked = int(stored)

            conversion = (realised_count / offers * 100.0) if offers else 0.0

            writer.writerow(
                [
                    campaign.id,
                    campaign.status,
                    _fmt_dt(campaign.launched_at),
                    _fmt_dt(campaign.campaign_ends_at),
                    campaign.source_file_path or "",
                    int(campaign.total_clients or 0),
                    offers,
                    delivered,
                    issued,
                    realised,
                    revoked,
                    f"{conversion:.1f}",
                ]
            )

    return len(campaigns), buffer.getvalue().encode("utf-8-sig")


async def build_categories_view() -> tuple[str, list[Any]]:
    """5 маркетинговых групп без списка клиентов."""

    async with session_scope() as session:
        campaign = await get_latest_campaign(session)

        if campaign is None:
            return NO_CAMPAIGN_TEXT, [admin_menu_keyboard(None)]

        categories = await get_campaign_categories(session, campaign.id)

        if not categories:
            return (
                f"Кампания #{campaign.id}: категории ещё не рассчитаны.",
                [admin_menu_keyboard(campaign.id)],
            )

        lines = [f"🧮 Кампания #{campaign.id} ({campaign.status})", ""]
        builder = InlineKeyboardBuilder()

        for category in categories:
            lines.append(_format_category_block(category))
            lines.append("")

            group = _group_int(category.segment_group)

            if group is not None and group < STABLE_GROUP:
                builder.row(
                    CallbackButton(
                        text=f"Группа {group}: {int(category.final_bonus)}",
                        payload=f"adm:category:{category.id}",
                    )
                )

        builder.row(CallbackButton(text="🏠 Меню", payload="adm:menu"))

        text = "\n".join(lines).strip()

    return text, [builder.as_markup()]


async def build_category_card(
    category_id: int,
) -> tuple[str | None, list[Any] | None]:
    """Карточка группы с кнопками изменения бонуса."""

    async with session_scope() as session:
        result = await session.execute(
            select(CampaignCategory).where(CampaignCategory.id == category_id)
        )
        category = result.scalar_one_or_none()

        if category is None:
            return None, None

        group = _group_int(category.segment_group)
        text = (
            f"🧮 {_format_category_block(category)}\n\n"
            f"Шаг изменения: ±{BONUS_STEP} (от {MIN_BONUS} до {MAX_BONUS})"
        )

        builder = InlineKeyboardBuilder()

        if group is not None and group < STABLE_GROUP:
            builder.row(
                CallbackButton(
                    text=f"−{BONUS_STEP}", payload=f"adm:bonus:{category.id}:-{BONUS_STEP}"
                ),
                CallbackButton(
                    text=str(int(category.final_bonus)), payload=f"adm:bonus:{category.id}:0"
                ),
                CallbackButton(
                    text=f"+{BONUS_STEP}", payload=f"adm:bonus:{category.id}:{BONUS_STEP}"
                ),
            )
        else:
            builder.row(
                CallbackButton(
                    text="Группа 4 не получает бонус", payload=f"adm:bonus:{category.id}:0"
                )
            )

        builder.row(
            CallbackButton(text="⬅️ Категории", payload="adm:categories"),
            CallbackButton(text="🏠 Меню", payload="adm:menu"),
        )

    return text, [builder.as_markup()]


async def build_launch_confirmation(
    campaign_id: int | None = None,
) -> tuple[int | None, str, list[Any], bool]:
    """Подтверждение запуска: (id, текст, вложения, можно ли запускать)."""

    async with session_scope() as session:
        campaign = (
            await get_campaign(session, campaign_id)
            if campaign_id is not None
            else await get_latest_campaign(session)
        )

        if campaign is None:
            return None, NO_CAMPAIGN_TEXT, [admin_menu_keyboard(None)], False

        if campaign.status == CampaignStatus.APPROVED.value:
            return (
                campaign.id,
                "Кампания уже запущена (APPROVED).",
                [admin_menu_keyboard(campaign.id)],
                False,
            )

        if campaign.status != CampaignStatus.DRAFT.value:
            return (
                campaign.id,
                f"Кампанию нельзя запустить: статус {campaign.status}.",
                [admin_menu_keyboard(campaign.id)],
                False,
            )

        recipients = sum(
            1 for target in campaign.targets if int(target.bonus_amount or 0) > 0
        )

        if recipients == 0:
            return (
                campaign.id,
                "В кампании нет получателей с ненулевым бонусом. "
                "Проверьте бонусы групп.",
                [admin_menu_keyboard(campaign.id)],
                False,
            )

        text = (
            f"🚀 Запуск кампании #{campaign.id}\n\n"
            f"Получателей: {recipients}\n"
            f"Окончание: {_fmt_dt(campaign.campaign_ends_at)}\n\n"
            "После подтверждения статус станет APPROVED, и клиенты "
            "получат персональные предложения в MAX."
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            CallbackButton(text="✅ Подтвердить", payload=f"adm:launch:{campaign.id}:yes"),
            CallbackButton(text="❌ Отмена", payload=f"adm:launch:{campaign.id}:no"),
        )

        return campaign.id, text, [builder.as_markup()], True


async def build_campaign_report(
    session,
    campaign: Campaign,
    revoked_total: int | None = None,
) -> str:
    """Текстовый отчёт по кампании."""

    targets = await get_campaign_targets(session, campaign.id)
    clients = await _load_clients_by_ids(
        session, [target.client_id for target in targets]
    )

    offers = 0
    deliverable = 0
    undelivered = 0
    realised_count = 0
    bonus_issued = 0
    bonus_realised = 0
    bonus_to_revoke = 0

    for target in targets:
        amount = int(target.bonus_amount or 0)

        if amount <= 0:
            continue

        offers += 1

        client = clients.get(target.client_id)
        reachable = client is not None and bool(client.max_user_id)

        if not reachable:
            # Предложение не доставлялось, бонус не начислялся.
            undelivered += 1
            continue

        deliverable += 1
        bonus_issued += amount

        realised = target.bonus_realised

        if realised is None:
            bonus_to_revoke += amount
            continue

        realised_int = int(realised)
        bonus_realised += realised_int

        if realised_int > 0:
            realised_count += 1

        if realised_int < amount:
            bonus_to_revoke += amount - realised_int

    if revoked_total is None:
        stored = (campaign.config or {}).get("revoked_total")

        if campaign.status == CampaignStatus.COMPLETED.value and stored is not None:
            revoked_total = int(stored)
        else:
            revoked_total = bonus_to_revoke

    conversion = (realised_count / offers * 100.0) if offers else 0.0

    return (
        f"📊 Отчёт по кампании #{campaign.id}\n"
        f"Статус: {campaign.status}\n"
        f"Период: {_fmt_dt(campaign.launched_at)} → "
        f"{_fmt_dt(campaign.campaign_ends_at)}\n\n"
        f"Всего клиентов в выборке: {campaign.total_clients}\n"
        f"Целевых клиентов (targets): {offers}\n"
        f"Доставлено (есть MAX user_id): {deliverable}\n"
        f"Не доставлено (нет MAX user_id): {undelivered}\n"
        f"Реализовали бонус: {realised_count}\n"
        f"Конверсия: {conversion:.1f}%\n\n"
        f"Выдано бонусов: {bonus_issued}\n"
        f"Реализовано бонусов: {bonus_realised}\n"
        f"Отозвано/к отзыву: {int(revoked_total)}"
    )


# =========================================================
# 8. CSV -> Pipeline
# =========================================================

def _read_csv(data: bytes) -> pd.DataFrame:
    """Читает CSV: автоопределение разделителя, кодировки и лимит строк."""

    last_error: Exception | None = None

    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            frame = pd.read_csv(
                io.BytesIO(data),
                encoding=encoding,
                sep=None,
                engine="python",
                nrows=CSV_MAX_ROWS + 1,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

        if len(frame) > CSV_MAX_ROWS:
            raise ValueError(f"too_many_rows:{CSV_MAX_ROWS}")

        return frame

    if last_error is not None:
        raise last_error

    raise ValueError("read_failed")


def _save_source_file(filename: str | None, data: bytes) -> Path:
    """Сохраняет исходный CSV в data/source_files (имя без path traversal)."""

    SOURCE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename or "upload.csv").name or "upload.csv"
    path = SOURCE_FILES_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    path.write_bytes(data)

    return path


def _remove_file(path: Path | None) -> None:
    if path is None:
        return

    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _find_file_attachment(event: Any) -> tuple[Any | None, str | None, str | None]:
    """Ищет именно файловое вложение с URL (фото/видео не подходят)."""

    message = getattr(event, "message", None)
    body = getattr(message, "body", None) if message is not None else None
    attachments = getattr(body, "attachments", None) if body is not None else None

    for attachment in attachments or []:
        attachment_type = str(getattr(attachment, "type", "")).upper()

        if not attachment_type.endswith("FILE"):
            continue

        payload = getattr(attachment, "payload", None)
        url = getattr(payload, "url", None)

        if not isinstance(url, str) or not url:
            continue

        return attachment, url, getattr(attachment, "filename", None)

    return None, None, None


async def _download_attachment(
    url: str, attachment: Any
) -> tuple[bytes | None, str | None]:
    """Скачивает вложение с контролем размера. Возвращает (data, ошибка)."""

    size = getattr(attachment, "size", None)
    limit_error = (
        f"Файл слишком большой. Лимит: {CSV_MAX_BYTES // (1024 * 1024)} МБ."
    )

    if isinstance(size, int) and size > CSV_MAX_BYTES:
        return None, limit_error

    download_file = getattr(bot, "download_file", None)

    if callable(download_file):
        # Скачиваем на диск: не держим возможный гигантский файл в RAM.
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                path = Path(await download_file(url, Path(tmp_dir)))

                if path.stat().st_size > CSV_MAX_BYTES:
                    return None, limit_error

                return path.read_bytes(), None
        except Exception as exc:
            logger.exception("Ошибка скачивания CSV: %r", exc)
            return None, "Не удалось скачать файл из MAX."

    try:
        data = await bot.download_bytes(url)
    except Exception as exc:
        logger.exception("Ошибка скачивания CSV: %r", exc)
        return None, "Не удалось скачать файл из MAX."

    if len(data) > CSV_MAX_BYTES:
        return None, limit_error

    return data, None


def _friendly_pipeline_error(detail: str) -> str:
    lowered = detail.lower()

    if "too_many_rows" in lowered:
        return f"слишком много строк (лимит {CSV_MAX_ROWS})."

    if "не найдена" in lowered:
        return "не найдены обязательные колонки (ID покупки, телефон, дата, сумма)."

    if "не осталось" in lowered:
        return "после обработки не осталось валидных строк."

    if "campaign_days" in lowered:
        return "некорректный срок кампании."

    return "файл не соответствует ожидаемому формату (подробности в логах)."


async def ingest_csv(
    data: bytes,
    filename: str | None,
) -> tuple[bool, str, int | None]:
    """Прогоняет CSV через существующий Pipeline и создаёт DRAFT-кампанию."""

    logger.info("Загрузка CSV: %s (%s байт)", filename, len(data))

    if not data:
        return False, "Файл пустой.", None

    if len(data) > CSV_MAX_BYTES:
        return (
            False,
            f"Файл слишком большой. Лимит: {CSV_MAX_BYTES // (1024 * 1024)} МБ.",
            None,
        )

    try:
        frame = await asyncio.to_thread(_read_csv, data)
    except pd.errors.EmptyDataError:
        return False, "CSV не содержит данных.", None
    except pd.errors.ParserError as exc:
        logger.error("Некорректный CSV: %r", exc)
        return False, "Не удалось разобрать CSV: некорректный формат.", None
    except UnicodeDecodeError as exc:
        logger.error("Не удалось определить кодировку CSV: %r", exc)
        return False, "Не удалось определить кодировку файла.", None
    except ValueError as exc:
        logger.error("CSV отклонён: %r", exc)
        return (
            False,
            f"Не удалось обработать CSV: {_friendly_pipeline_error(str(exc))}",
            None,
        )
    except Exception as exc:
        logger.exception("Ошибка чтения CSV: %r", exc)
        return False, "Не удалось прочитать CSV.", None

    if frame is None or frame.empty:
        return False, "CSV не содержит строк.", None

    path: Path | None = None

    try:
        path = _save_source_file(filename, data)

        pipeline = Pipeline(frame)

        async with session_scope() as session:
            report = await pipeline.run(
                session,
                source_file_path=str(path),
                campaign_days=CAMPAIGN_DAYS,
            )

            campaign_id = int(report["campaign_id"])
            categories = await get_campaign_categories(session, campaign_id)
            clients_count = len(report.get("clients", []))
    except ValueError as exc:
        logger.error("Pipeline отклонил CSV: %s", exc)
        _remove_file(path)
        return (
            False,
            f"Не удалось обработать CSV: {_friendly_pipeline_error(str(exc))}",
            None,
        )
    except Exception as exc:
        logger.exception("Pipeline: непредвиденная ошибка: %r", exc)
        _remove_file(path)
        return False, "Внутренняя ошибка обработки файла. См. логи.", None

    logger.info(
        "CSV обработан: кампания #%s, категорий=%s, клиентов=%s",
        campaign_id,
        len(categories),
        clients_count,
    )

    return True, f"Кампания #{campaign_id} создана (DRAFT).", campaign_id


# =========================================================
# 9. Клиентский флоу
# =========================================================

REGISTRATION_TEXT = (
    "Здравствуйте! Это бот программы лояльности.\n\n"
    "Пришлите, пожалуйста, номер телефона, который указан в программе "
    "лояльности.\n\n"
    "Можно нажать кнопку ниже — MAX передаст ваш номер. Если номер "
    "в программе лояльности другой, просто отправьте его текстом в "
    "формате +79991234567."
)

REGISTRATION_REMINDER = (
    "Жду номер телефона в формате +79991234567 "
    "или нажмите кнопку «Отправить мой номер»."
)

PROGRAM_TEXT = (
    "ℹ️ О программе лояльности\n\n"
    "Мы начисляем бонусы клиентам, которые давно не совершали покупки. "
    "Персональное предложение приходит в этот чат и действует "
    "ограниченное время.\n\n"
    "Бонусы начисляются на номер телефона, привязанный к программе "
    "лояльности."
)

OFFER_TEXT = (
    "🎁 Для вас доступен персональный бонус: {bonus} баллов.\n\n"
    "Он действует ограниченное время.\n\n"
    "Успейте воспользоваться предложением."
)


def client_menu_text(client: Client) -> str:
    return (
        "👋 Вы в программе лояльности.\n\n"
        f"Номер в программе: {client.phone_e164}\n\n"
        "Выберите действие:"
    )


def _phone_from_contact(event: Any) -> str | None:
    """Достаёт телефон из вложения-контакта MAX."""

    message = getattr(event, "message", None)
    body = getattr(message, "body", None) if message is not None else None
    attachments = getattr(body, "attachments", None) if body is not None else None

    for attachment in attachments or []:
        payload = getattr(attachment, "payload", None)
        vcf = getattr(payload, "vcf", None)
        phone = getattr(vcf, "phone", None)

        if phone:
            return str(phone)

    return None


async def _ask_phone(
    context: MemoryContext,
    chat_id: int | None,
    user_id: str,
) -> None:
    """Просит телефон; повторно не дублирует полную инструкцию."""

    await context.set_state(ClientStates.waiting_phone)

    data = await context.get_data()
    already_asked = bool(data.get("phone_asked"))

    if not already_asked:
        await context.update_data(phone_asked=True)

    await _outgoing(
        REGISTRATION_REMINDER if already_asked else REGISTRATION_TEXT,
        attachments=[phone_keyboard()],
        chat_id=chat_id,
        user_id=_to_int(user_id),
    )


async def _link_client(
    session,
    max_user_id: str,
    phone_e164: str,
) -> tuple[Client | None, str | None]:
    """Привязывает MAX user_id к клиенту программы лояльности."""

    existing_by_max = await get_client_by_max_id(session, max_user_id)

    if existing_by_max is not None:
        if existing_by_max.phone_e164 == phone_e164:
            return existing_by_max, None

        return (
            None,
            "Этот аккаунт MAX уже привязан к другому номеру "
            "в программе лояльности.",
        )

    existing_by_phone = await get_client_by_phone(session, phone_e164)

    if existing_by_phone is not None:
        if (
            existing_by_phone.max_user_id
            and existing_by_phone.max_user_id != max_user_id
        ):
            return None, "Этот номер уже привязан к другому аккаунту MAX."

        existing_by_phone.max_user_id = max_user_id
        await session.flush()

        return existing_by_phone, None

    client = await create_client(session, phone_e164, max_user_id)

    if client is None:
        return None, "Не удалось создать клиента."

    return client, None


async def _client_offer_text(session, client: Client) -> str:
    """Актуальное персональное предложение клиента."""

    result = await session.execute(
        select(Campaign)
        .join(CampaignTarget, CampaignTarget.campaign_id == Campaign.id)
        .where(
            CampaignTarget.client_id == client.id,
            CampaignTarget.bonus_amount > 0,
            Campaign.status == CampaignStatus.APPROVED.value,
            Campaign.campaign_ends_at > _utcnow(),
        )
        .order_by(CampaignTarget.id.desc())
        .limit(1)
    )

    campaign = result.scalars().first()

    if campaign is None:
        return "🎁 Сейчас для вас нет активного предложения."

    target_result = await session.execute(
        select(CampaignTarget)
        .where(
            CampaignTarget.client_id == client.id,
            CampaignTarget.campaign_id == campaign.id,
            CampaignTarget.bonus_amount > 0,
        )
        .order_by(CampaignTarget.id.desc())
        .limit(1)
    )

    target = target_result.scalars().first()

    if target is None:
        return "🎁 Сейчас для вас нет активного предложения."

    return (
        "🎁 Ваше персональное предложение\n\n"
        f"Бонус: {int(target.bonus_amount or 0)} баллов\n"
        f"Действует до: {_fmt_dt(campaign.campaign_ends_at)}"
    )


async def _send_client_menu(
    client: Client,
    chat_id: int | None,
    user_id: str,
) -> None:
    await _outgoing(
        client_menu_text(client),
        attachments=[client_menu_keyboard()],
        chat_id=chat_id,
        user_id=_to_int(user_id),
    )


async def _route_start(
    user_id: str,
    chat_id: int | None,
    context: MemoryContext,
) -> None:
    """Общая логика /start и bot_started."""

    await context.clear()

    if not user_id:
        logger.warning("Не удалось определить MAX user_id в /start")
        return

    if is_admin(user_id):
        text, attachments = await build_admin_menu()
        await _outgoing(
            text, attachments, chat_id=chat_id, user_id=_to_int(user_id)
        )
        return

    async with session_scope() as session:
        client = await get_client_by_max_id(session, user_id)

    if client is not None:
        await _send_client_menu(client, chat_id, user_id)
        return

    await _ask_phone(context, chat_id, user_id)


# =========================================================
# 10. Обработчики: команды
# =========================================================

@dp.message_created(CommandStart())
async def handle_start(event: MessageCreated, context: MemoryContext) -> None:
    user_id = _user_id(event)
    logger.info("[/start] user=%s", _mask_id(user_id))

    await _route_start(user_id, _chat_id(event), context)


@dp.message_created(Command("admin"))
async def handle_admin_command(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    user_id = _user_id(event)

    if not is_admin(user_id):
        logger.warning("[/admin] доступ запрещён user=%s", _mask_id(user_id))
        await _answer(event, "Команда недоступна.")
        return

    await context.clear()

    text, attachments = await build_admin_menu()

    await _answer(event, text, attachments)


@dp.bot_started()
async def handle_bot_started(
    event: BotStarted,
    context: MemoryContext,
) -> None:
    user_id = str(event.user.user_id)
    logger.info("[bot_started] user=%s", _mask_id(user_id))

    await _route_start(user_id, event.chat_id, context)


# =========================================================
# 11. Обработчики: регистрация клиента
# =========================================================

@dp.message_created(ClientStates.waiting_phone)
async def handle_client_phone(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    user_id = _user_id(event)
    chat_id = _chat_id(event)

    if is_admin(user_id):
        await context.clear()
        text, attachments = await build_admin_menu()
        await _answer(event, text, attachments)
        return

    raw_phone = _phone_from_contact(event)

    if raw_phone is None:
        body = getattr(event.message, "body", None)
        raw_phone = getattr(body, "text", None) if body is not None else None

    normalized = (
        Preprocessor.normalize_phone(str(raw_phone), "RU") if raw_phone else None
    )

    if not normalized:
        await _answer(
            event,
            "Не похоже на номер телефона. Пришлите номер в формате "
            "+79991234567.",
        )
        return

    try:
        async with session_scope() as session:
            client, error = await _link_client(session, user_id, normalized)
    except Exception as exc:
        logger.exception("Ошибка регистрации клиента: %r", exc)
        await _answer(event, "Не удалось сохранить номер. Попробуйте позже.")
        return

    if error or client is None:
        logger.info("Регистрация отклонена: user=%s, %s", _mask_id(user_id), error)
        await _answer(event, error or "Не удалось зарегистрировать номер.")
        return

    await context.clear()

    logger.info("Клиент зарегистрирован: user=%s", _mask_id(user_id))

    await _send_client_menu(client, chat_id, user_id)


# =========================================================
# 12. Обработчики: CSV от админа
# =========================================================

async def _process_admin_csv(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    """Скачивает CSV из MAX и прогоняет его через Pipeline."""

    user_id = _user_id(event)

    attachment, url, filename = _find_file_attachment(event)

    if attachment is None or not url:
        if is_admin(user_id):
            await _answer(event, CSV_PROMPT)
        return

    if not is_admin(user_id):
        await _answer(event, "Файлы принимаются только от администратора.")
        return

    if filename and "." in filename and not filename.lower().endswith(".csv"):
        await _answer(event, "Нужен файл с расширением .csv.")
        return

    data, error = await _download_attachment(url, attachment)

    if error or data is None:
        await _answer(event, f"⚠️ {error or 'Не удалось скачать файл.'}")
        return

    await _answer(event, "⏳ Обрабатываю CSV...")

    ok, message, campaign_id = await ingest_csv(data, filename)

    if not ok:
        await _answer(event, f"⚠️ {message}")
        return

    await context.clear()

    text, attachments = await build_categories_view()

    await _answer(event, f"{message}\n\n{text}", attachments)

    logger.info("Админ загрузил CSV, кампания #%s", campaign_id)


@dp.message_created(AdminStates.waiting_csv)
async def handle_admin_csv_state(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    await _process_admin_csv(event, context)


@dp.message_created(F.message.body.attachments)
async def handle_admin_attachment(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    """CSV можно прислать и без кнопки «Загрузить CSV»."""

    await _process_admin_csv(event, context)


# =========================================================
# 13. Callback: админ
# =========================================================

async def _apply_bonus_change(
    category_id: int,
    delta: int,
) -> tuple[int | None, str, bool]:
    """Меняет бонус группы и синхронизирует targets.

    Возвращает ``(значение, сообщение, изменилось)``. Список получателей
    при обнулении бонуса не теряется: он сохраняется в
    ``campaign.config["deferred_targets"]`` и восстанавливается при
    повышении бонуса (новых таблиц и полей не создаём).
    """

    async with session_scope() as session:
        result = await session.execute(
            select(CampaignCategory).where(CampaignCategory.id == category_id)
        )
        category = result.scalar_one_or_none()

        if category is None:
            return None, "Категория не найдена.", False

        group = _group_int(category.segment_group)

        if group is None or group >= STABLE_GROUP:
            return None, "Эта группа бонус не получает.", False

        campaign = await get_campaign(session, category.campaign_id)

        if campaign is None:
            return None, "Кампания не найдена.", False

        if campaign.status != CampaignStatus.DRAFT.value:
            return (
                None,
                "Бонусы можно менять только в кампании со статусом DRAFT "
                f"(сейчас {campaign.status}).",
                False,
            )

        current = int(category.final_bonus or 0)

        if delta == 0:
            return current, f"Текущий бонус: {current}", False

        new_bonus = max(MIN_BONUS, min(MAX_BONUS, current + int(delta)))

        if new_bonus == current:
            return current, f"Граница: бонус от {MIN_BONUS} до {MAX_BONUS}.", False

        updated = await update_category_bonus(session, category_id, new_bonus)

        if updated is None:
            return None, "Категория не найдена.", False

        config = dict(campaign.config or {})
        deferred = dict(config.get("deferred_targets") or {})

        if new_bonus == 0:
            snapshot = [
                {
                    "client_id": target.client_id,
                    "recency": target.recency,
                    "frequency": target.frequency,
                    "monetary_score": (
                        float(target.monetary_score)
                        if target.monetary_score is not None
                        else None
                    ),
                    "avg_amount": (
                        float(target.avg_amount)
                        if target.avg_amount is not None
                        else None
                    ),
                }
                for target in campaign.targets
                if target.category_id == category_id
            ]

            deferred[str(category_id)] = snapshot
            config["deferred_targets"] = deferred
            campaign.config = config

            await session.execute(
                sa_delete(CampaignTarget).where(
                    CampaignTarget.category_id == category_id
                )
            )
        else:
            snapshot = deferred.pop(str(category_id), None)

            if snapshot:
                config["deferred_targets"] = deferred
                campaign.config = config

                await create_campaign_targets(
                    session,
                    [
                        {
                            "campaign_id": campaign.id,
                            "category_id": category_id,
                            "client_id": item["client_id"],
                            "recency": item.get("recency"),
                            "frequency": item.get("frequency"),
                            "monetary_score": item.get("monetary_score"),
                            "avg_amount": item.get("avg_amount"),
                            "bonus_amount": new_bonus,
                            "bonus_realised": None,
                        }
                        for item in snapshot
                    ],
                )
            else:
                await session.execute(
                    sa_update(CampaignTarget)
                    .where(CampaignTarget.category_id == category_id)
                    .values(bonus_amount=new_bonus)
                )

        await session.flush()

    return new_bonus, f"Бонус группы {group} изменён на {new_bonus}", True


async def _approve_campaign(campaign_id: int | None) -> tuple[bool, str]:
    """DRAFT -> APPROVED.

    Просроченную кампанию продлеваем, чтобы она не запустилась «в
    пустоту» и не была отозвана на следующем тике воркера.
    """

    async with session_scope() as session:
        campaign = (
            await get_campaign(session, campaign_id)
            if campaign_id is not None
            else await get_latest_campaign(session)
        )

        if campaign is None:
            return False, "Кампания не найдена."

        if campaign.status == CampaignStatus.APPROVED.value:
            return False, "Кампания уже запущена (APPROVED)."

        if campaign.status == CampaignStatus.COMPLETED.value:
            return False, "Кампания уже завершена (COMPLETED)."

        if campaign.status != CampaignStatus.DRAFT.value:
            return False, f"Кампания в статусе {campaign.status}."

        # Получателей проверяем ДО продления срока: иначе отклонённый
        # запуск всё равно закоммитил бы новый campaign_ends_at.
        recipients = sum(
            1 for target in campaign.targets if int(target.bonus_amount or 0) > 0
        )

        if recipients == 0:
            return (
                False,
                "В кампании нет получателей с ненулевым бонусом. "
                "Проверьте бонусы групп.",
            )

        extra = ""
        now = _utcnow()

        if campaign.campaign_ends_at is None or campaign.campaign_ends_at <= now:
            campaign.campaign_ends_at = now + timedelta(days=CAMPAIGN_DAYS)
            extra = (
                f"\nСрок кампании истёк — продлён до "
                f"{_fmt_dt(campaign.campaign_ends_at)}."
            )

        updated = await update_campaign_status(
            session, campaign.id, CampaignStatus.APPROVED.value
        )

        if updated is None:
            return False, "Кампания не найдена."

        # Фиксируем момент запуска: от него эмулятор считает окно
        # начисления, чтобы не достраивать визиты с даты создания DRAFT.
        config = dict(campaign.config or {})
        config["approved_at"] = now.isoformat(timespec="seconds")
        campaign.config = config

        await session.flush()

        logger.info(
            "Кампания #%s переведена в APPROVED (получателей: %s)",
            campaign.id,
            recipients,
        )

        return True, f"Кампания #{campaign.id} запущена (APPROVED).{extra}"


async def _handle_admin_callback(
    event: MessageCallback,
    context: MemoryContext,
    user_id: str,
    payload: str,
) -> None:
    if not is_admin(user_id):
        await _safe_ack(event, "Доступно только администратору.")
        return

    parts = payload.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    # Любое действие, кроме загрузки CSV, снимает ожидание файла и
    # подтверждения, чтобы состояния не залипали.
    if action != "upload":
        await context.clear()

    if action == "menu":
        text, attachments = await build_admin_menu()
        await _safe_edit(event, text, attachments)
        return

    if action == "upload":
        await context.set_state(AdminStates.waiting_csv)

        chat_id, target_user = _callback_target(event)
        await _outgoing(CSV_PROMPT, chat_id=chat_id, user_id=target_user)
        await _safe_ack(event)
        return

    if action == "campaign":
        text, attachments = await build_campaign_card()
        await _safe_edit(event, text, attachments)
        return

    if action == "categories":
        text, attachments = await build_categories_view()
        await _safe_edit(event, text, attachments)
        return

    if action == "category":
        category_id = _to_int(parts[2]) if len(parts) > 2 else None

        if category_id is None:
            await _safe_ack(event, "Категория не найдена.")
            return

        owner_id = await _campaign_id_of_category(category_id)
        stale = (
            await _stale_campaign_message(owner_id)
            if owner_id is not None
            else "Категория не найдена."
        )

        if stale:
            text, attachments = await build_categories_view()
            await _safe_edit(event, text, attachments, notification=stale)
            return

        text, attachments = await build_category_card(category_id)

        if text is None:
            await _safe_ack(event, "Категория не найдена.")
            return

        await _safe_edit(event, text, attachments)
        return

    if action == "bonus":
        category_id = _to_int(parts[2]) if len(parts) > 2 else None
        delta = _to_int(parts[3]) if len(parts) > 3 else None

        if category_id is None or delta is None:
            await _safe_ack(event, "Некорректное изменение бонуса.")
            return

        # Бонусы старой кампании менять нельзя: карточка могла устареть.
        owner_id = await _campaign_id_of_category(category_id)
        stale = (
            await _stale_campaign_message(owner_id)
            if owner_id is not None
            else "Категория не найдена."
        )

        if stale:
            text, attachments = await build_categories_view()
            await _safe_edit(event, text, attachments, notification=stale)
            return

        _, message, changed = await _apply_bonus_change(category_id, delta)

        text, attachments = await build_category_card(category_id)

        if text is None:
            await _safe_ack(event, message)
            return

        if changed:
            await _safe_edit(event, text, attachments, notification=message)
        else:
            await _safe_ack(event, message)
        return

    if action == "launch":
        campaign_id = _to_int(parts[2]) if len(parts) > 2 else None
        decision = parts[3] if len(parts) > 3 else None

        if campaign_id is None:
            await _safe_ack(
                event, "Карточка устарела. Откройте «🚀 Запустить кампанию» заново."
            )
            return

        stale = await _stale_campaign_message(campaign_id)

        if stale:
            text, attachments = await build_admin_menu()
            await _safe_edit(event, text, attachments, notification=stale)
            return

        if decision == "no":
            text, attachments = await build_admin_menu()
            await _safe_edit(
                event, text, attachments, notification="Запуск отменён."
            )
            return

        if decision == "yes":
            ok, message = await _approve_campaign(campaign_id)

            if not ok:
                text, attachments = await build_campaign_card()
                await _safe_edit(event, text, attachments, notification=message)
                return

            await _safe_edit(
                event,
                f"🚀 {message}\n\nНачинаю рассылку предложений...",
                [admin_menu_keyboard(campaign_id)],
                notification=message,
            )
            _spawn(
                send_campaign_offers(campaign_id),
                label=f"send_campaign_{campaign_id}",
            )
            return

        _, text, attachments, launchable = await build_launch_confirmation(
            campaign_id
        )

        if launchable:
            await context.set_state(AdminStates.confirming_launch)
            await context.update_data(confirm_campaign_id=campaign_id)

        await _safe_edit(event, text, attachments)
        return

    if action == "send":
        # Кнопка удалена: бизнес не должен иметь возможности отправить
        # предложения повторно. Рассылка идёт один раз при запуске кампании.
        text, attachments = await build_admin_menu()
        await _safe_edit(
            event,
            text,
            attachments,
            notification=(
                "Действие недоступно: рассылка выполняется один раз "
                "при запуске кампании."
            ),
        )
        return

    if action == "export":
        try:
            count, data = await build_campaigns_history_csv()
        except Exception as exc:
            logger.exception("Не удалось собрать историю кампаний: %r", exc)
            await _safe_ack(event, "Не удалось собрать историю кампаний.")
            return

        media = InputMediaBuffer(buffer=data, filename="campaigns.csv")
        chat_id, target_user = _callback_target(event)

        await _outgoing(
            f"📊 История кампаний: {count} шт. Файл во вложении.",
            attachments=[media],
            chat_id=chat_id,
            user_id=target_user,
        )
        await _safe_ack(event)
        return

    if action == "report":
        async with session_scope() as session:
            campaign = await get_latest_campaign(session)

            if campaign is None:
                await _safe_ack(event, "Кампании пока нет.")
                return

            text = await build_campaign_report(session, campaign)

        await _safe_edit(event, text, [admin_menu_keyboard(campaign.id)])
        return

    await _safe_ack(event, "Неизвестное действие.")


# =========================================================
# 14. Callback: клиент
# =========================================================

async def _handle_client_callback(
    event: MessageCallback,
    user_id: str,
    payload: str,
) -> None:
    async with session_scope() as session:
        client = await get_client_by_max_id(session, user_id)

        if client is None:
            await _safe_ack(event, "Сначала зарегистрируйтесь: /start")
            return

        action = payload.split(":")[1] if ":" in payload else "menu"

        if action == "offer":
            text = await _client_offer_text(session, client)
            attachments = back_keyboard()
        elif action == "balance":
            balance = await loyalty.fetch_balance(client.phone_e164)

            if balance is None:
                text = (
                    "💳 Баланс бонусов\n\n"
                    "Программа лояльности недоступна, баланс не удалось "
                    "получить. Попробуйте позже."
                )
            else:
                text = f"💳 Ваш баланс: {int(balance)} баллов"

            attachments = back_keyboard()
        elif action == "program":
            text = PROGRAM_TEXT
            attachments = back_keyboard()
        else:
            text = client_menu_text(client)
            attachments = [client_menu_keyboard()]

    await _safe_edit(event, text, attachments)


# =========================================================
# 15. Единый callback-обработчик
# =========================================================

@dp.message_callback()
async def handle_callback(
    event: MessageCallback,
    context: MemoryContext,
) -> None:
    try:
        callback = event.callback
        payload = str(getattr(callback, "payload", "") or "").strip()
        user_id = str(getattr(getattr(callback, "user", None), "user_id", ""))
    except Exception:
        logger.exception("Некорректный callback-апдейт")
        return

    if not payload or not user_id:
        await _safe_ack(event, "Кнопка устарела.")
        return

    try:
        if payload.startswith("adm:"):
            if not is_admin(user_id):
                logger.warning(
                    "Попытка доступа к админ-callback: user=%s",
                    _mask_id(user_id),
                )
                await _safe_ack(event, "Доступно только администратору.")
                return

            await _handle_admin_callback(event, context, user_id, payload)
            return

        if payload.startswith("cli:"):
            # Клиентское действие снимает незавершённое состояние
            # регистрации, иначе следующий текст уйдёт в парсер телефона.
            await context.clear()
            await _handle_client_callback(event, user_id, payload)
            return

        await _safe_ack(event, "Кнопка устарела.")
    except Exception as exc:
        logger.exception("Ошибка обработки callback %s: %r", payload, exc)
        await _safe_ack(event, "Ошибка. Подробности в логах.")


# =========================================================
# 16. Подтверждение запуска текстом
# =========================================================

@dp.message_created(AdminStates.confirming_launch)
async def handle_launch_confirmation_text(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    user_id = _user_id(event)

    if not is_admin(user_id):
        await context.clear()
        await _answer(event, "Доступно только администратору.")
        return

    data = await context.get_data()
    campaign_id = _to_int(data.get("confirm_campaign_id"))

    body = getattr(event.message, "body", None)
    text = (getattr(body, "text", None) or "").strip().lower()

    if text in LAUNCH_YES:
        await context.clear()

        if campaign_id is None:
            await _answer(
                event,
                "Карточка устарела. Откройте «🚀 Запустить кампанию» заново.",
            )
            return

        ok, message = await _approve_campaign(campaign_id)

        if not ok:
            await _answer(event, message)
            return

        await _answer(event, f"🚀 {message}\n\nНачинаю рассылку предложений...")
        _spawn(
            send_campaign_offers(campaign_id),
            label=f"send_campaign_{campaign_id}",
        )
        return

    if text in LAUNCH_NO:
        await context.clear()
        text_out, attachments = await build_admin_menu()
        await _answer(event, text_out, attachments)
        return

    await _answer(event, "Подтвердите запуск: напишите «да» или «нет».")


# =========================================================
# 17. Рассылка кампании
# =========================================================

_send_guard = asyncio.Lock()
_sending_campaigns: set[int] = set()


async def send_campaign_offers(
    campaign_id: int | None,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Отправляет персональные предложения по targets кампании.

    Повторная рассылка защищена тремя уровнями:

    * ``_sending_campaigns`` — параллельный запуск той же кампании;
    * ``campaign.config["offers_sent"]`` — случайный повтор требует
      явного ``force=True`` (кнопка подтверждения в админ-меню);
    * периодическая перепроверка статуса и срока во время цикла.

    Признак занятости берётся ДО загрузки получателей, чтобы воркер
    time-machine не завершил кампанию в промежутке.
    """

    async with session_scope() as session:
        query = select(
            Campaign.id,
            Campaign.status,
            Campaign.campaign_ends_at,
            Campaign.launched_at,
            Campaign.config,
        )

        if campaign_id is not None:
            row = (await session.execute(query.where(Campaign.id == campaign_id))).first()
        else:
            row = (
                await session.execute(
                    query.order_by(Campaign.launched_at.desc()).limit(1)
                )
            ).first()

    if row is None:
        logger.warning("Рассылка: кампания не найдена")
        return {"error": 1}

    campaign_id = int(row[0])
    status = row[1]
    ends_at = row[2]
    launched_at = row[3]
    config = dict(row[4] or {})

    if status != CampaignStatus.APPROVED.value:
        logger.warning(
            "Рассылка кампании #%s невозможна: статус %s", campaign_id, status
        )
        return {"error": 1}

    if ends_at is not None and ends_at <= _utcnow():
        logger.warning("Рассылка кампании #%s невозможна: срок истёк", campaign_id)
        await _notify_admins(
            f"⚠️ Кампания #{campaign_id} просрочена, рассылка отменена."
        )
        return {"error": 1}

    if config.get("offers_sent") and not force:
        logger.warning(
            "Кампания #%s уже рассылалась, нужен явный force", campaign_id
        )
        return {"already": 1}

    async with _send_guard:
        if campaign_id in _sending_campaigns:
            logger.warning("Рассылка кампании #%s уже идёт", campaign_id)
            return {"busy": 1}

        _sending_campaigns.add(campaign_id)

    sent = 0
    failed = 0
    skipped = 0
    aborted = 0

    try:
        # Окно начисления начинается с момента запуска кампании (а не с
        # даты создания DRAFT), иначе эмулятор достроил бы визиты «назад».
        issued_at = _coerce_datetime(config.get("approved_at")) or launched_at

        async with session_scope() as session:
            targets = await get_campaign_targets(session, campaign_id)
            clients = await _load_clients_by_ids(
                session, [target.client_id for target in targets]
            )

            pending = []

            for target in targets:
                amount = int(target.bonus_amount or 0)

                if amount <= 0:
                    continue

                client = clients.get(target.client_id)

                pending.append(
                    {
                        "target_id": target.id,
                        "bonus": amount,
                        "recency": target.recency,
                        "frequency": target.frequency,
                        "avg_amount": (
                            float(target.avg_amount)
                            if target.avg_amount is not None
                            else None
                        ),
                        "max_user_id": client.max_user_id if client else None,
                        "phone": client.phone_e164 if client else None,
                    }
                )

        logger.info(
            "Рассылка кампании #%s: получателей=%s", campaign_id, len(pending)
        )

        for index, item in enumerate(pending):
            if index and index % SEND_STATUS_RECHECK_EVERY == 0:
                if not await _campaign_is_active(campaign_id):
                    aborted = len(pending) - index
                    logger.warning(
                        "Рассылка кампании #%s прервана: кампания неактивна",
                        campaign_id,
                    )
                    break

            max_user_id = item["max_user_id"]
            phone = item["phone"]
            bonus = item["bonus"]

            if not max_user_id:
                skipped += 1
                continue

            uid = _to_int(max_user_id)

            if uid is None:
                skipped += 1
                logger.warning(
                    "Target %s пропущен: некорректный max_user_id",
                    item["target_id"],
                )
                continue

            if phone:
                try:
                    await loyalty.accrue_bonus(
                        phone_e164=phone,
                        amount=bonus,
                        campaign_id=campaign_id,
                        target_id=item["target_id"],
                        recency=item["recency"],
                        frequency=item["frequency"],
                        avg_amount=item["avg_amount"],
                        issued_at=issued_at,
                        ends_at=ends_at,
                    )
                except Exception as exc:
                    failed += 1
                    logger.exception(
                        "Начисление не удалось, оффер не отправлен (target=%s): %r",
                        item["target_id"],
                        exc,
                    )
                    continue

            ok = await _outgoing(OFFER_TEXT.format(bonus=bonus), user_id=uid)

            if ok:
                sent += 1
            else:
                failed += 1
                logger.error(
                    "Не удалось отправить предложение target=%s",
                    item["target_id"],
                )

                # Оффер не доставлен — откатываем начисление, иначе бонус
                # «зависнет» у клиента, а статистика кампании соврёт.
                if phone:
                    try:
                        await loyalty.revoke_bonus(
                            phone_e164=phone,
                            amount=bonus,
                            campaign_id=campaign_id,
                            target_id=item["target_id"],
                        )
                    except Exception as exc:
                        logger.exception(
                            "Не удалось откатить начисление target=%s: %r",
                            item["target_id"],
                            exc,
                        )

            if SEND_DELAY_SECONDS > 0:
                await asyncio.sleep(SEND_DELAY_SECONDS)
    finally:
        async with _send_guard:
            _sending_campaigns.discard(campaign_id)

    summary = {
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "aborted": aborted,
    }

    # Фиксируем факт рассылки: случайный повтор не продублирует офферы.
    # Маркер ставим и при частичном прерывании (aborted), чтобы повтор
    # требовал явного подтверждения force.
    if sent > 0 or aborted > 0:
        try:
            async with session_scope() as session:
                campaign = await get_campaign(session, campaign_id)

                if campaign is not None:
                    fresh = dict(campaign.config or {})
                    fresh["offers_sent"] = {
                        "at": _utcnow().isoformat(timespec="seconds"),
                        "sent": sent,
                        "failed": failed,
                        "skipped": skipped,
                        "aborted": aborted,
                    }
                    campaign.config = fresh
                    await session.flush()
        except Exception as exc:
            logger.error(
                "Не удалось сохранить факт рассылки #%s: %r", campaign_id, exc
            )

    logger.info("Рассылка кампании #%s завершена: %s", campaign_id, summary)

    await _notify_admins(
        f"📤 Рассылка кампании #{campaign_id} завершена\n"
        f"Отправлено: {sent}\n"
        f"Пропущено (нет MAX user_id): {skipped}\n"
        f"Ошибок: {failed}\n"
        f"Прервано (кампания неактивна): {aborted}"
    )

    return summary


# =========================================================
# 18. Time-machine: завершение кампаний
# =========================================================

async def finish_expired_campaigns() -> list[tuple[int, str]]:
    """Завершает APPROVED-кампании с истёкшим сроком.

    Перед расчётом отзыва берём у эмулятора фактическую реализацию
    (``fetch_realised``) и зеркалим её в ``campaign_target.bonus_realised``.
    Если хотя бы одна операция отзыва не прошла, транзакция
    откатывается, и кампания будет обработана на следующем тике
    (отзывы идемпотентны по target_id).
    """

    now = _utcnow()

    with contextlib.suppress(Exception):
        await loyalty.advance(now)

    finished: list[tuple[int, str]] = []

    async with session_scope() as session:
        result = await session.execute(
            select(Campaign).where(
                Campaign.status == CampaignStatus.APPROVED.value,
                Campaign.campaign_ends_at <= now,
            )
        )
        campaigns = list(result.scalars().all())

        if campaigns:
            logger.info(
                "Завершение кампаний: к обработке %s шт. (сейчас %s UTC)",
                len(campaigns),
                now.isoformat(timespec="seconds"),
            )

        for campaign in campaigns:
            if campaign.id in _sending_campaigns:
                logger.info(
                    "Кампания #%s ещё рассылается, завершение отложено",
                    campaign.id,
                )
                continue

            targets = await get_campaign_targets(session, campaign.id)
            clients = await _load_clients_by_ids(
                session, [target.client_id for target in targets]
            )

            revoked_total = 0
            issues = 0

            for target in targets:
                amount = int(target.bonus_amount or 0)

                if amount <= 0:
                    continue

                client = clients.get(target.client_id)

                if client is None:
                    issues += 1
                    logger.error(
                        "Target %s: клиент %s не найден",
                        target.id,
                        target.client_id,
                    )
                    continue

                if not client.max_user_id:
                    # Оффер и начисление этому клиенту не отправлялись —
                    # отзывать нечего.
                    continue

                realised = target.bonus_realised

                try:
                    simulated = await loyalty.fetch_realised(
                        campaign_id=campaign.id,
                        target_id=target.id,
                        phone_e164=client.phone_e164,
                        bonus_amount=amount,
                        issued_at=campaign.launched_at,
                        ends_at=campaign.campaign_ends_at,
                    )
                except Exception as exc:
                    logger.exception(
                        "Не удалось получить реализацию target=%s: %r",
                        target.id,
                        exc,
                    )
                    simulated = None
                    issues += 1

                if simulated is not None:
                    simulated = max(0, min(amount, int(simulated)))
                    await update_target_bonus_realised(session, target.id, simulated)
                    realised = simulated

                if realised is None:
                    revoke = amount
                else:
                    realised_int = int(realised)
                    revoke = 0 if realised_int >= amount else amount - realised_int

                if revoke <= 0:
                    continue

                try:
                    revoked = await loyalty.revoke_bonus(
                        phone_e164=client.phone_e164,
                        amount=revoke,
                        campaign_id=campaign.id,
                        target_id=target.id,
                    )
                except Exception as exc:
                    logger.exception(
                        "Ошибка отзыва бонуса target=%s: %r", target.id, exc
                    )
                    issues += 1
                    continue

                revoked_total += (
                    int(revoked) if isinstance(revoked, int) else revoke
                )

            if issues:
                # Кампанию не завершаем: повторим на следующем тике.
                raise RuntimeError(
                    f"Кампания #{campaign.id}: не удалось обработать "
                    f"{issues} операций, повтор позже"
                )

            campaign.status = CampaignStatus.COMPLETED.value

            config = dict(campaign.config or {})
            config["revoked_total"] = revoked_total
            config["completed_at"] = now.isoformat(timespec="seconds")
            campaign.config = config

            await session.flush()

            report = await build_campaign_report(
                session, campaign, revoked_total=revoked_total
            )

            finished.append((campaign.id, report))

            logger.info(
                "Кампания #%s завершена, отозвано бонусов: %s",
                campaign.id,
                revoked_total,
            )

    return finished


async def campaign_finish_worker(
    interval_seconds: int | None = None,
) -> None:
    """Фоновая задача: продвигает эмуляцию и завершает истёкшие кампании."""

    interval = max(5, interval_seconds or CAMPAIGN_FINISH_CHECK_INTERVAL)

    logger.info("Time-machine завершения кампаний запущен (интервал %s c)", interval)

    while True:
        try:
            due = await finish_expired_campaigns()

            if not due:
                logger.debug("Проверка завершения кампаний: готовых нет")

            for _campaign_id, report in due:
                await _notify_admins(report)
        except asyncio.CancelledError:
            logger.info("Time-machine завершения кампаний остановлен")
            raise
        except Exception as exc:
            logger.exception("Ошибка завершения кампаний: %r", exc)

        await asyncio.sleep(interval)


# =========================================================
# 19. Fallback и ошибки
# =========================================================

@dp.message_created(F.message.body.text)
async def handle_fallback(
    event: MessageCreated,
    context: MemoryContext,
) -> None:
    user_id = _user_id(event)

    if is_admin(user_id):
        await _answer(
            event, "Не понял команду. /admin — админ-меню, /start — начало."
        )
        return

    async with session_scope() as session:
        client = await get_client_by_max_id(session, user_id)

    if client is None:
        await _ask_phone(context, _chat_id(event), user_id)
        return

    await _answer(
        event, "Не понял команду. /start — меню, /admin — для администратора."
    )


@dp.errors()
async def handle_error(event: Any) -> None:
    """Логирует ошибку обработчика и подтверждает callback."""

    logger.error("Ошибка в обработчике: %r", getattr(event, "exception", event))

    update = getattr(event, "update", None)

    if update is not None and hasattr(update, "ack"):
        with contextlib.suppress(Exception):
            await update.ack(notification="Ошибка. Попробуйте позже.")


# =========================================================
# 20. Запуск
# =========================================================

async def main() -> None:
    logger.info("Запуск MAX-бота Win-Back MVP")

    if not MAXAPI_AVAILABLE:
        raise RuntimeError(
            "Библиотека maxapi не установлена: "
            f"{MAXAPI_IMPORT_ERROR!r}. Установите maxapi и повторите запуск."
        )

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS не задан: административное меню недоступно.")

    if BOT_TOKEN in {"", "test_token_for_dev", "change_me"}:
        logger.warning(
            "MAX_BOT_TOKEN не задан (используется %r): бот не выйдет в сеть.",
            BOT_TOKEN,
        )

    SOURCE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    worker = asyncio.create_task(campaign_finish_worker())

    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await worker

        for task in list(_bg_tasks):
            task.cancel()

        if _bg_tasks:
            await asyncio.gather(*_bg_tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
