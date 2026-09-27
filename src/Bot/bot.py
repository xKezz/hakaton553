# src/Bot/bot.py
import asyncio
import logging
import os
import re

from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import MessageCreated, BotStarted, OpenAppButton
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from src.DB.crud import (
    get_business_by_max_id,
    check_client_in_loyalty,
    check_api_key,
    register_business,
    save_client_phone,
)

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "test_token_for_dev")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://example.com")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================== СОСТОЯНИЯ ====================
class BusinessReg(StatesGroup):
    waiting_api_key = State()


class ClientReg(StatesGroup):
    waiting_phone = State()


# ==================== ВСПОМОГАТЕЛЬНОЕ ====================
def menu_keyboard():
    """Кнопка открытия мини-аппа."""
    builder = InlineKeyboardBuilder()
    builder.row(
        OpenAppButton(
            text="Открыть меню",
            web_app=MINIAPP_URL,
            payload="business_menu",
        )
    )
    return builder.as_markup()


async def route_start(
    user_id: str,
    chat_id: int,
    payload: str | None,
    context: MemoryContext,
):
    """Общая логика для /start и bot_started."""
    if payload == "client":
        in_loyalty = await check_client_in_loyalty(user_id)
        if in_loyalty:
            await bot.send_message(
                chat_id=chat_id,
                text="Вы уже зарегистрированы ✅",
            )
        else:
            await context.set_state(ClientReg.waiting_phone)
            await bot.send_message(
                chat_id=chat_id,
                text="Пришлите ваш номер телефона (например, +79991234567).",
            )
        return

    if payload == "business":
        business = await get_business_by_max_id(user_id)
        if business:
            await bot.send_message(
                chat_id=chat_id,
                text="С возвращением! Откройте меню.",
                attachments=[menu_keyboard()],
            )
        else:
            await context.set_state(BusinessReg.waiting_api_key)
            await bot.send_message(
                chat_id=chat_id,
                text="Привет! Пришлите API-ключ вашей программы лояльности.",
            )
        return

    # Без payload
    await bot.send_message(
        chat_id=chat_id,
        text="Используйте /start client или /start business.",
    )


# ==================== /start (вручную) ====================
@dp.message_created(CommandStart())
async def handle_start(event: MessageCreated, context: MemoryContext):
    await context.clear()

    text = event.message.body.text or ""
    match = re.match(
        r"^/start(?:@\w+)?(?:\s+(\w+))?", text.strip(), re.IGNORECASE
    )
    payload = match.group(1) if match and match.group(1) else None

    user_id = str(event.message.sender.user_id)
    chat_id = event.chat.chat_id  # ← исправлено

    logger.info(f"[/start] user_id={user_id}, payload={payload}")

    await route_start(user_id, chat_id, payload, context)


# ==================== bot_started (переход по диплинку) ====================
@dp.bot_started()
async def on_bot_started(event: BotStarted, context: MemoryContext):
    await context.clear()

    payload = getattr(event, "payload", None)
    user_id = str(event.user.user_id)
    chat_id = event.chat_id

    logger.info(f"[bot_started] user_id={user_id}, payload={payload}")

    await route_start(user_id, chat_id, payload, context)


# ==================== БИЗНЕС: ЖДЁМ API-КЛЮЧ ====================
@dp.message_created(BusinessReg.waiting_api_key)
async def business_waiting_api_key(event: MessageCreated, context: MemoryContext):
    api_key = (event.message.body.text or "").strip()

    if len(api_key) < 10:
        await event.message.answer("Ключ слишком короткий. Пришлите ещё раз.")
        return

    await event.message.answer("Проверяю ключ...")

    valid, endpoint = await check_api_key(api_key)
    if not valid:
        await event.message.answer(
            "Ключ не подошёл. Проверьте и пришлите ещё раз."
        )
        return

    await register_business(
        str(event.message.sender.user_id),
        api_key,
        endpoint or "",
    )
    await context.clear()

    await event.message.answer(
        "Готово! Бизнес зарегистрирован ✅",
        attachments=[menu_keyboard()],
    )


# ==================== КЛИЕНТ: ЖДЁМ НОМЕР ====================
@dp.message_created(ClientReg.waiting_phone)
async def client_waiting_phone(event: MessageCreated, context: MemoryContext):
    phone = (
        (event.message.body.text or "")
        .strip()
        .replace(" ", "")
        .replace("-", "")
    )

    if not phone.startswith("+") or len(phone) < 12:
        await event.message.answer(
            "Похоже, это не номер. Пришлите в формате +79991234567."
        )
        return

    success = await save_client_phone(
        str(event.message.sender.user_id), phone
    )
    if not success:
        await event.message.answer(
            "Этот номер уже привязан к другому аккаунту."
        )
        return

    await context.clear()
    await event.message.answer("Готово! Вы в программе ✅")


# ==================== FALLBACK ====================
@dp.message_created(F.message.body.text)
async def fallback(event: MessageCreated):
    await event.message.answer("Не понял. Используйте /start.")


async def main():
    logger.info("Бот запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())