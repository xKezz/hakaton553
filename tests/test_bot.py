"""Тесты бизнес-логики MAX-бота (``src/Bot/bot.py``).

``maxapi`` в окружении не установлен, поэтому ``bot.py`` работает через
свою fallback-заглушку (``MAXAPI_AVAILABLE is False``). Здесь проверяется
логика бота, а не сеть:

* чистые хелперы (конфиг, парсинг, формат, CSV, вложения);
* работа с БД на sqlite+aiosqlite in-memory (PostgreSQL и сеть не нужны);
* рассылка предложений и завершение кампаний на фейковом Loyalty-адаптере.

Файл самодостаточен: все фикстуры локальные, ``tests/conftest.py`` и
``src/Bot/bot.py`` не изменяются.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import src.Bot.bot as bot_module
from src.DB.crud import (
    get_campaign,
    get_campaign_categories,
    get_campaign_targets,
)
from src.DB.database import Base
from src.DB.models import (
    Campaign,
    CampaignCategory,
    CampaignStatus,
    CampaignTarget,
    Client,
)

# =========================================================
# 0. Тестовые данные и фейки
# =========================================================


def sample_csv_bytes() -> bytes:
    """CSV на 10 клиентов (по мотивам conftest.make_sample_raw_df).

    Даты считаются от сегодняшнего дня, поэтому метрики (recency,
    frequency) не зависят от абсолютной календарной даты запуска тестов.
    """

    today = pd.Timestamp.today().normalize()
    rows: list[str] = []
    purchase_id = 1

    for client_number in range(1, 11):
        phone = f"+799900000{client_number:02d}"
        last_date = today - pd.Timedelta(days=client_number)

        for purchase_index in range(client_number):
            purchase_date = last_date - pd.Timedelta(
                days=2 * purchase_index
            )
            rows.append(
                f"{purchase_id},{phone},"
                f"{purchase_date.date().isoformat()},"
                f"{client_number * 100}.00"
            )
            purchase_id += 1

    header = "purchase_id,phone,purchase_date,amount"
    return (f"{header}\n" + "\n".join(rows) + "\n").encode("utf-8")


class FakeLoyalty:
    """Фейковый адаптер лояльности с управляемым поведением."""

    def __init__(self) -> None:
        self.balance: int | None = 120
        self.accruals: list[dict[str, Any]] = []
        self.revocations: list[dict[str, Any]] = []
        # (campaign_id, target_id) -> фактически отозванная сумма.
        self.revoked_amounts: dict[tuple[int, int], int] = {}
        self.fetch_realised_calls: list[int] = []
        self.advance_calls: list[Any] = []

        # target_id -> значение; если ключа нет, берётся default.
        self.fetch_realised_map: dict[int, int | None] = {}
        self.fetch_realised_default: int | None = None

        self.accrue_errors: set[int] = set()
        self.revoke_errors: set[int] = set()

    async def fetch_balance(self, phone_e164: str) -> int | None:
        return self.balance

    async def accrue_bonus(self, **kwargs: Any) -> None:
        if kwargs.get("target_id") in self.accrue_errors:
            raise RuntimeError("accrue_bonus упал (фейк)")
        self.accruals.append(dict(kwargs))

    async def revoke_bonus(self, **kwargs: Any) -> int:
        if kwargs.get("target_id") in self.revoke_errors:
            raise RuntimeError("revoke_bonus упал (фейк)")

        key = (
            int(kwargs.get("campaign_id") or 0),
            int(kwargs.get("target_id") or 0),
        )

        if key in self.revoked_amounts:
            # Идемпотентность как у LoyaltyMockAdapter: повторная попытка
            # (в т.ч. после отката транзакции бота) возвращает ту же сумму
            # и НЕ пишет вторую операцию отзыва.
            return self.revoked_amounts[key]

        amount = int(kwargs.get("amount") or 0)
        self.revocations.append(dict(kwargs))
        self.revoked_amounts[key] = amount
        return amount

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
        self.fetch_realised_calls.append(target_id)
        return self.fetch_realised_map.get(
            target_id, self.fetch_realised_default
        )

    async def advance(self, now: Any = None) -> int:
        self.advance_calls.append(now)
        return 0


class RecordingContext:
    """Мини-контекст FSM, записывающий вызовы."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.cleared = 0
        self.states: list[Any] = []
        self.updates: list[dict[str, Any]] = []
        self.data: dict[str, Any] = dict(data or {})

    async def clear(self) -> None:
        self.cleared += 1
        self.data = {}
        # Как в реальном FSM-контексте: clear() сбрасывает и состояние.
        self.states.append(None)

    async def set_state(self, state: Any = None) -> None:
        self.states.append(state)

    async def get_state(self) -> Any:
        return self.states[-1] if self.states else None

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.data.update(kwargs)
        self.updates.append(dict(kwargs))
        return dict(self.data)


class FakeCallbackEvent:
    """Событие callback с ack/edit/send, записывающее вызовы."""

    def __init__(
        self,
        payload: str,
        user_id: Any,
        chat_id: int = 101,
    ) -> None:
        self.callback = SimpleNamespace(
            payload=payload,
            user=SimpleNamespace(user_id=user_id),
        )
        self.message = SimpleNamespace(
            recipient=SimpleNamespace(chat_id=chat_id)
        )
        self.acked: list[Any] = []
        self.edits: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    async def ack(self, notification: str | None = None):
        self.acked.append(notification)
        return SimpleNamespace(success=True, message=None)

    async def edit(self, **kwargs: Any):
        self.edits.append(kwargs)
        return SimpleNamespace(success=True, message=None)

    async def send(self, **kwargs: Any):
        self.sent.append(kwargs)
        return SimpleNamespace(success=True, message=None)


class FakeTextMessage:
    def __init__(
        self,
        text: str,
        user_id: Any = 7,
        chat_id: int = 101,
    ) -> None:
        self.body = SimpleNamespace(text=text, attachments=None)
        self.sender = SimpleNamespace(user_id=user_id)
        self.recipient = SimpleNamespace(chat_id=chat_id)
        self.answers: list[str] = []

    async def answer(self, text: str, attachments: Any = None):
        self.answers.append(text)
        return SimpleNamespace(success=True)


class FakeTextEvent:
    """Событие message_created с текстом."""

    def __init__(self, text: str, user_id: Any = 7) -> None:
        self.message = FakeTextMessage(text, user_id)
        self.callback = None
        self.user = None


def _db_bomb(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("БД не должна использоваться в этом сценарии")


def _file_attachment(
    url: str | None = "http://example.com/f.csv",
    filename: str | None = "f.csv",
    atype: str = "FILE",
) -> SimpleNamespace:
    return SimpleNamespace(
        type=atype,
        filename=filename,
        payload=SimpleNamespace(url=url),
    )


def _event_with_attachments(*attachments: Any) -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(
            body=SimpleNamespace(attachments=list(attachments))
        )
    )


# =========================================================
# 1. Фикстуры (локальные, conftest не трогаем)
# =========================================================


@pytest_asyncio.fixture
async def db(monkeypatch):
    """sqlite+aiosqlite in-memory вместо PostgreSQL."""

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    monkeypatch.setattr(bot_module, "async_session", maker)

    yield maker

    await engine.dispose()


@pytest_asyncio.fixture
async def env(db, monkeypatch):
    """Изолированное окружение бота: БД, заглушки, фейковый Loyalty."""

    monkeypatch.setattr(bot_module, "SEND_DELAY_SECONDS", 0)
    monkeypatch.setattr(bot_module, "ADMIN_IDS", {"7"})
    monkeypatch.setattr(
        bot_module,
        "_save_source_file",
        lambda filename, data: Path("/tmp/dsh_test_source.csv"),
    )

    outgoing: list[dict[str, Any]] = []

    async def fake_outgoing(
        text: str,
        attachments: Any = None,
        chat_id: int | None = None,
        user_id: Any = None,
    ) -> bool:
        outgoing.append(
            {
                "text": text,
                "attachments": attachments,
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )
        return True

    monkeypatch.setattr(bot_module, "_outgoing", fake_outgoing)

    notified: list[str] = []

    async def fake_notify(text: str) -> None:
        notified.append(text)

    monkeypatch.setattr(bot_module, "_notify_admins", fake_notify)

    fake_loyalty = FakeLoyalty()
    monkeypatch.setattr(bot_module, "loyalty", fake_loyalty)

    # Защита от «залипшего» флага рассылки между тестами.
    bot_module._sending_campaigns.clear()

    yield SimpleNamespace(
        maker=db,
        loyalty=fake_loyalty,
        outgoing=outgoing,
        notified=notified,
    )

    bot_module._sending_campaigns.clear()


@pytest.fixture
def spawned(monkeypatch) -> list[tuple[str, Any]]:
    """Перехватывает bot_module._spawn, закрывая корутину."""

    items: list[tuple[str, Any]] = []

    def fake_spawn(coro: Any, *, label: str) -> None:
        items.append((label, coro))
        coro.close()
        return None

    monkeypatch.setattr(bot_module, "_spawn", fake_spawn)
    return items


def _keyboard_payloads(markup: Any) -> list[Any]:
    """Достаёт payload'ы кнопок из разметки клавиатуры (fallback и maxapi)."""

    if isinstance(markup, dict):
        buttons = markup.get("buttons")
    else:
        buttons = getattr(getattr(markup, "payload", None), "buttons", None)

    payloads: list[Any] = []

    for row in buttons or []:
        for button in row:
            payloads.append(getattr(button, "payload", None))

    return payloads


def _keyboard_texts(markup: Any) -> list[Any]:
    """Достаёт тексты кнопок из разметки клавиатуры."""

    if isinstance(markup, dict):
        buttons = markup.get("buttons")
    else:
        buttons = getattr(getattr(markup, "payload", None), "buttons", None)

    texts: list[Any] = []

    for row in buttons or []:
        for button in row:
            texts.append(getattr(button, "text", None))

    return texts


# =========================================================
# 2. Хелперы для работы с БД
# =========================================================


async def _ingest(env) -> int:
    ok, message, campaign_id = await bot_module.ingest_csv(
        sample_csv_bytes(), "sample.csv"
    )
    assert ok is True, message
    assert campaign_id is not None
    return int(campaign_id)


async def _campaign(env, campaign_id: int) -> Campaign:
    async with env.maker() as session:
        campaign = await get_campaign(session, campaign_id)
        assert campaign is not None
        return campaign


async def _categories(env, campaign_id: int) -> list[Any]:
    async with env.maker() as session:
        return await get_campaign_categories(session, campaign_id)


async def _targets(env, campaign_id: int) -> list[CampaignTarget]:
    async with env.maker() as session:
        return await get_campaign_targets(session, campaign_id)


async def _set_campaign_ends(env, campaign_id: int, value: datetime) -> None:
    async with env.maker() as session:
        campaign = await session.get(Campaign, campaign_id)
        campaign.campaign_ends_at = value
        await session.commit()


async def _set_all_targets_bonus(
    env,
    campaign_id: int,
    bonus: int,
    realised: int | None = None,
) -> list[int]:
    async with env.maker() as session:
        rows = (
            (
                await session.execute(
                    select(CampaignTarget).where(
                        CampaignTarget.campaign_id == campaign_id
                    )
                )
            )
            .scalars()
            .all()
        )
        ids = [row.id for row in rows]

        for row in rows:
            row.bonus_amount = bonus
            row.bonus_realised = realised

        await session.commit()

    return ids


async def _make_all_deliverable(env, campaign_id: int) -> int:
    """Проставляет max_user_id всем клиентам targets кампании."""

    async with env.maker() as session:
        rows = (
            (
                await session.execute(
                    select(CampaignTarget).where(
                        CampaignTarget.campaign_id == campaign_id
                    )
                )
            )
            .scalars()
            .all()
        )

        for row in rows:
            client = await session.get(Client, row.client_id)
            client.max_user_id = str(5000 + row.id)

        await session.commit()

        return len(rows)


async def _prepare_report_targets(
    env,
    amounts: list[int],
    realised: list[int | None],
    deliverable: list[bool],
) -> tuple[int, list[int]]:
    """Оставляет в кампании ровно len(amounts) подготовленных targets."""

    campaign_id = await _ingest(env)
    targets = (await _targets(env, campaign_id))[: len(amounts)]
    assert len(targets) == len(amounts)
    target_ids = [target.id for target in targets]

    async with env.maker() as session:
        await session.execute(
            sa_delete(CampaignTarget).where(
                CampaignTarget.campaign_id == campaign_id,
                CampaignTarget.id.notin_(target_ids),
            )
        )

        for target, amount, realised_value, has_chat in zip(
            targets, amounts, realised, deliverable
        ):
            row = await session.get(CampaignTarget, target.id)
            row.bonus_amount = amount
            row.bonus_realised = realised_value

            client = await session.get(Client, target.client_id)
            client.max_user_id = (
                f"77{target.client_id}" if has_chat else None
            )

        await session.commit()

    return campaign_id, target_ids


async def _prepare_expired_campaign(
    env,
    bonus: int = 100,
) -> tuple[int, list[int]]:
    campaign_id = await _ingest(env)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    await _make_all_deliverable(env, campaign_id)
    target_ids = await _set_all_targets_bonus(
        env, campaign_id, bonus, realised=None
    )
    await _set_campaign_ends(
        env, campaign_id, datetime.utcnow() - timedelta(hours=1)
    )

    return campaign_id, target_ids


def _report_value(report: str, prefix: str) -> str:
    for line in report.splitlines():
        if line.startswith(prefix):
            return line

    raise AssertionError(f"нет строки {prefix!r} в отчёте:\n{report}")


# =========================================================
# 3. Чистые функции
# =========================================================


def test_parse_admin_ids_variants() -> None:
    assert bot_module.parse_admin_ids("1,2") == {"1", "2"}
    assert bot_module.parse_admin_ids("1;2") == {"1", "2"}
    assert bot_module.parse_admin_ids("1 2") == {"1", "2"}
    assert bot_module.parse_admin_ids(" 1 , 2 ; 3 ") == {"1", "2", "3"}
    assert bot_module.parse_admin_ids("") == set()
    assert bot_module.parse_admin_ids(None) == set()
    assert bot_module.parse_admin_ids(" , ; ") == set()


def test_is_admin(monkeypatch) -> None:
    monkeypatch.setattr(bot_module, "ADMIN_IDS", {"7", "8"})

    assert bot_module.is_admin("7") is True
    assert bot_module.is_admin(7) is True
    assert bot_module.is_admin(" 8 ") is True
    assert bot_module.is_admin("9") is False
    assert bot_module.is_admin(None) is False


def test_mask_phone_hides_middle_digits() -> None:
    result = bot_module._mask_phone("+79991234567")

    assert result == "***4567"
    assert "+7999123" not in result
    assert "999123" not in result

    assert bot_module._mask_phone("1234") == "***"
    assert bot_module._mask_phone("") == "***"
    assert bot_module._mask_phone(None) == "***"
    assert bot_module._mask_phone("12345") == "***2345"


def test_to_int_and_group_int() -> None:
    assert bot_module._to_int(" 42 ") == 42
    assert bot_module._to_int("3") == 3
    assert bot_module._to_int("abc") is None
    assert bot_module._to_int(None) is None
    assert bot_module._to_int("3.5") is None

    assert bot_module._group_int("2") == 2
    assert bot_module._group_int(3) == 3
    assert bot_module._group_int(None) is None
    assert bot_module._group_int("мусор") is None


def test_fmt_and_fmt_dt() -> None:
    assert bot_module._fmt(None) == "—"
    assert bot_module._fmt("abc") == "abc"
    assert bot_module._fmt(123.456) == "123.5"
    assert bot_module._fmt(123.456, 2) == "123.46"

    assert bot_module._fmt_dt(None) == "—"
    assert bot_module._fmt_dt("мусор") == "мусор"
    assert (
        bot_module._fmt_dt(datetime(2026, 1, 2, 3, 4))
        == "2026-01-02 03:04 UTC"
    )


def test_split_text_short_is_single_part() -> None:
    assert bot_module._split_text("короткий текст") == ["короткий текст"]
    assert bot_module._split_text("") == [""]


def test_split_text_long_multiline_keeps_content() -> None:
    lines = [f"line-{index:03d}-" + "x" * 80 for index in range(60)]
    text = "\n".join(lines)

    assert len(text) > bot_module.MAX_MESSAGE_LENGTH

    parts = bot_module._split_text(text)

    assert len(parts) > 1
    assert all(len(part) <= bot_module.MAX_MESSAGE_LENGTH for part in parts)
    assert "line-000" in parts[0]
    assert "line-059" in parts[-1]
    assert (
        "".join(part.replace("\n", "") for part in parts)
        == text.replace("\n", "")
    )

    long_line = "z" * 8000
    parts = bot_module._split_text(long_line)

    assert len(parts) == 3
    assert all(len(part) <= bot_module.MAX_MESSAGE_LENGTH for part in parts)
    assert "".join(parts) == long_line


def test_env_int_valid_garbage_and_clamps(monkeypatch) -> None:
    name = "DSH_TEST_INT"

    monkeypatch.setenv(name, " 42 ")
    assert bot_module._env_int(name, 5) == 42

    monkeypatch.setenv(name, "abc")
    assert bot_module._env_int(name, 5) == 5

    monkeypatch.setenv(name, "")
    assert bot_module._env_int(name, 5) == 5

    monkeypatch.delenv(name)
    assert bot_module._env_int(name, 5) == 5

    monkeypatch.setenv(name, "0")
    assert bot_module._env_int(name, 5, minimum=1) == 1

    monkeypatch.setenv(name, "999")
    assert bot_module._env_int(name, 5, maximum=10) == 10


def test_env_float_valid_garbage_and_clamp(monkeypatch) -> None:
    name = "DSH_TEST_FLOAT"

    monkeypatch.setenv(name, "1.5")
    assert bot_module._env_float(name, 0.0) == pytest.approx(1.5)

    monkeypatch.setenv(name, "abc")
    assert bot_module._env_float(name, 0.25) == pytest.approx(0.25)

    monkeypatch.setenv(name, "")
    assert bot_module._env_float(name, 0.25) == pytest.approx(0.25)

    monkeypatch.setenv(name, "-3")
    assert bot_module._env_float(name, 1.0, minimum=0.0) == 0.0


def test_friendly_pipeline_error() -> None:
    too_many = bot_module._friendly_pipeline_error("too_many_rows:200000")
    assert "слишком много строк" in too_many

    missing = bot_module._friendly_pipeline_error(
        "Колонка с телефонами не найдена."
    )
    assert "колонк" in missing

    empty = bot_module._friendly_pipeline_error(
        "После предобработки не осталось ни одной валидной строки."
    )
    assert "не осталось" in empty

    days = bot_module._friendly_pipeline_error(
        "campaign_days должен быть больше нуля."
    )
    assert "срок" in days

    other = bot_module._friendly_pipeline_error("что-то другое")
    assert "формат" in other


def test_read_csv_comma_and_semicolon() -> None:
    comma = (
        b"purchase_id,phone,purchase_date,amount\n"
        b"1,+79990000001,2026-09-01,100\n"
        b"2,+79990000002,2026-09-02,200\n"
    )

    frame = bot_module._read_csv(comma)

    assert list(frame.columns) == [
        "purchase_id",
        "phone",
        "purchase_date",
        "amount",
    ]
    assert len(frame) == 2

    semicolon = (
        "purchase_id;phone;purchase_date;amount\n"
        "1;+79990000001;2026-09-01;100\n"
        "2;+79990000002;2026-09-02;200\n"
    ).encode("utf-8")

    frame = bot_module._read_csv(semicolon)

    assert list(frame.columns) == [
        "purchase_id",
        "phone",
        "purchase_date",
        "amount",
    ]
    assert len(frame) == 2


def test_read_csv_utf8_bom_and_cp1251() -> None:
    bom = (
        "\ufeffpurchase_id,phone,purchase_date,amount\n"
        "1,+79990000001,2026-09-01,100\n"
    ).encode("utf-8")

    frame = bot_module._read_csv(bom)

    assert "purchase_id" in frame.columns
    assert len(frame) == 1

    cp1251 = (
        "purchase_id;телефон;дата;сумма\n"
        "1;+79990000001;2026-09-01;100\n"
    ).encode("cp1251")

    frame = bot_module._read_csv(cp1251)

    assert "телефон" in frame.columns
    assert len(frame) == 1


def test_read_csv_row_limit(monkeypatch) -> None:
    monkeypatch.setattr(bot_module, "CSV_MAX_ROWS", 5)

    rows = "\n".join(
        f"{index},+7999000000{index},2026-09-01,100"
        for index in range(10)
    )
    data = (
        "purchase_id,phone,purchase_date,amount\n" + rows + "\n"
    ).encode("utf-8")

    with pytest.raises(ValueError, match="too_many_rows"):
        bot_module._read_csv(data)


def test_find_file_attachment() -> None:
    attachment = _file_attachment()

    found, url, filename = bot_module._find_file_attachment(
        _event_with_attachments(attachment)
    )

    assert found is attachment
    assert url == "http://example.com/f.csv"
    assert filename == "f.csv"

    # Картинка без filename игнорируется.
    image = SimpleNamespace(
        type="IMAGE",
        filename=None,
        payload=SimpleNamespace(url="http://example.com/i.jpg"),
    )
    assert bot_module._find_file_attachment(
        _event_with_attachments(image)
    ) == (None, None, None)

    # Файл без payload.url игнорируется.
    no_url = _file_attachment(url=None)
    assert bot_module._find_file_attachment(
        _event_with_attachments(no_url)
    ) == (None, None, None)

    # Файл вообще без payload игнорируется.
    no_payload = SimpleNamespace(type="FILE", filename="f.csv")
    assert bot_module._find_file_attachment(
        _event_with_attachments(no_payload)
    ) == (None, None, None)

    # Картинка перед файлом не мешает найти файл.
    found, url, _ = bot_module._find_file_attachment(
        _event_with_attachments(image, attachment)
    )
    assert found is attachment
    assert url == "http://example.com/f.csv"

    assert bot_module._find_file_attachment(
        SimpleNamespace()
    ) == (None, None, None)


async def test_load_clients_by_ids_empty(env) -> None:
    async with env.maker() as session:
        assert await bot_module._load_clients_by_ids(session, []) == {}


async def test_load_clients_by_ids_chunked(env, monkeypatch) -> None:
    monkeypatch.setattr(bot_module, "CLIENT_ID_CHUNK", 2)

    async with env.maker() as session:
        for index in range(5):
            session.add(
                Client(
                    phone_e164=f"+7999555000{index}",
                    max_user_id=f"max-{index}",
                )
            )
        await session.commit()

    async with env.maker() as session:
        ids = list(
            (
                await session.execute(select(Client.id))
            ).scalars().all()
        )

        assert len(ids) == 5

        loaded = await bot_module._load_clients_by_ids(session, ids)

    assert set(loaded) == set(ids)
    assert all(client.max_user_id for client in loaded.values())


# =========================================================
# 4. ingest_csv
# =========================================================


async def test_ingest_csv_happy_path(env) -> None:
    ok, message, campaign_id = await bot_module.ingest_csv(
        sample_csv_bytes(), "sample.csv"
    )

    assert ok is True
    assert campaign_id is not None
    assert "DRAFT" in message

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value

    categories = await _categories(env, campaign_id)
    assert len(categories) == 5

    targets = await _targets(env, campaign_id)
    assert targets

    group_by_category = {
        category.id: int(category.segment_group)
        for category in categories
    }

    for target in targets:
        assert group_by_category[target.category_id] < 4
        assert int(target.bonus_amount) > 0
        assert target.bonus_realised is None


async def test_ingest_csv_empty_bytes(env) -> None:
    ok, message, campaign_id = await bot_module.ingest_csv(b"", "empty.csv")

    assert ok is False
    assert "пустой" in message.lower()
    assert campaign_id is None


async def test_ingest_csv_missing_columns(env) -> None:
    ok, message, campaign_id = await bot_module.ingest_csv(
        b"foo,bar\n1,2\n", "bad.csv"
    )

    assert ok is False
    assert "колонк" in message
    assert campaign_id is None


async def test_ingest_csv_size_limit(env, monkeypatch) -> None:
    monkeypatch.setattr(bot_module, "CSV_MAX_BYTES", 10)

    ok, message, campaign_id = await bot_module.ingest_csv(
        sample_csv_bytes(), "big.csv"
    )

    assert ok is False
    assert "большой" in message
    assert campaign_id is None


# =========================================================
# 5. _apply_bonus_change
# =========================================================


async def _category_by_group(env, campaign_id: int, group: str):
    for category in await _categories(env, campaign_id):
        if str(category.segment_group) == group:
            return category

    raise AssertionError(f"нет категории группы {group}")


async def test_apply_bonus_change_syncs_targets(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "0")
    before = int(category.final_bonus)

    value, message, changed = await bot_module._apply_bonus_change(
        category.id, -50
    )

    assert changed is True
    assert value == before - 50
    assert str(value) in message

    targets = [
        target
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]

    assert targets
    assert all(int(target.bonus_amount) == value for target in targets)

    refreshed = await _category_by_group(env, campaign_id, "0")
    assert int(refreshed.final_bonus) == value


async def test_apply_bonus_change_clamps(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "0")

    value, _, changed = await bot_module._apply_bonus_change(
        category.id, -1000
    )

    assert value == bot_module.MIN_BONUS
    assert changed is True

    target_ids_after_zero = [
        target.id
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]
    assert target_ids_after_zero == []

    value, _, changed = await bot_module._apply_bonus_change(
        category.id, 1000
    )

    assert value == bot_module.MAX_BONUS
    assert changed is True

    restored = [
        target
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]
    assert restored
    assert all(
        int(target.bonus_amount) == bot_module.MAX_BONUS
        for target in restored
    )


async def test_apply_bonus_change_zero_delta(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "0")
    before = int(category.final_bonus)

    value, message, changed = await bot_module._apply_bonus_change(
        category.id, 0
    )

    assert changed is False
    assert value == before
    assert str(before) in message

    targets = [
        target
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]
    assert all(int(target.bonus_amount) == before for target in targets)


async def test_apply_bonus_change_unknown_category(env) -> None:
    value, message, changed = await bot_module._apply_bonus_change(
        999_999, -50
    )

    assert value is None
    assert changed is False
    assert "не найдена" in message


async def test_apply_bonus_change_group4_rejected(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "4")

    value, _, changed = await bot_module._apply_bonus_change(
        category.id, -50
    )

    assert value is None
    assert changed is False


async def test_apply_bonus_change_non_draft_rejected(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "0")

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    value, response, changed = await bot_module._apply_bonus_change(
        category.id, -50
    )

    assert value is None
    assert changed is False
    assert "DRAFT" in response


async def test_apply_bonus_change_full_cycle_50_to_0_to_50(env) -> None:
    campaign_id = await _ingest(env)
    category = await _category_by_group(env, campaign_id, "2")

    # Готовим ровно цикл 50 -> 0 -> 50: бонус категории и её targets = 50.
    async with env.maker() as session:
        category_row = await session.get(CampaignCategory, category.id)
        category_row.final_bonus = 50

        rows = (
            (
                await session.execute(
                    select(CampaignTarget).where(
                        CampaignTarget.category_id == category.id
                    )
                )
            )
            .scalars()
            .all()
        )
        target_ids = [row.id for row in rows]

        for row in rows:
            row.bonus_amount = 50

        await session.commit()

    assert target_ids

    value, _, changed = await bot_module._apply_bonus_change(
        category.id, -50
    )
    assert value == 0
    assert changed is True

    remaining = [
        target.id
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]
    assert remaining == []

    campaign = await _campaign(env, campaign_id)
    snapshot = (campaign.config or {}).get("deferred_targets", {})
    assert snapshot.get(str(category.id))
    assert len(snapshot[str(category.id)]) == len(target_ids)

    value, _, changed = await bot_module._apply_bonus_change(
        category.id, 50
    )
    assert value == 50
    assert changed is True

    restored = [
        target
        for target in await _targets(env, campaign_id)
        if target.category_id == category.id
    ]
    assert len(restored) == len(target_ids)
    assert all(int(target.bonus_amount) == 50 for target in restored)

    campaign = await _campaign(env, campaign_id)
    assert not (campaign.config or {}).get("deferred_targets")


# =========================================================
# 6. _approve_campaign / _stale_campaign_message
# =========================================================


async def test_approve_campaign_draft_to_approved(env) -> None:
    campaign_id = await _ingest(env)

    ok, message = await bot_module._approve_campaign(campaign_id)

    assert ok is True
    assert "APPROVED" in message

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.APPROVED.value


async def test_approve_campaign_twice_rejected(env) -> None:
    campaign_id = await _ingest(env)

    ok, _ = await bot_module._approve_campaign(campaign_id)
    assert ok is True

    ok, message = await bot_module._approve_campaign(campaign_id)

    assert ok is False
    assert "уже запущена" in message


async def test_approve_campaign_extends_expired(env) -> None:
    campaign_id = await _ingest(env)
    await _set_campaign_ends(
        env, campaign_id, datetime.utcnow() - timedelta(days=1)
    )

    ok, message = await bot_module._approve_campaign(campaign_id)

    assert ok is True
    assert "продлён" in message

    campaign = await _campaign(env, campaign_id)
    assert campaign.campaign_ends_at > datetime.utcnow()


async def test_approve_campaign_without_recipients(env) -> None:
    campaign_id = await _ingest(env)
    await _set_all_targets_bonus(env, campaign_id, 0)

    # Просроченная кампания: отклонённый запуск НЕ должен продлевать срок.
    expired_at = datetime.utcnow() - timedelta(days=1)
    await _set_campaign_ends(env, campaign_id, expired_at)

    ok, message = await bot_module._approve_campaign(campaign_id)

    assert ok is False
    assert "получател" in message

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value
    assert campaign.campaign_ends_at == expired_at


async def test_stale_campaign_message(env) -> None:
    assert "нет" in (await bot_module._stale_campaign_message(1))

    first_id = await _ingest(env)

    async with env.maker() as session:
        campaign = await session.get(Campaign, first_id)
        campaign.launched_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()

    second_id = await _ingest(env)
    assert second_id != first_id

    assert await bot_module._stale_campaign_message(second_id) is None

    message = await bot_module._stale_campaign_message(first_id)
    assert message is not None
    assert "устарела" in message


# =========================================================
# 7. build_campaign_report
# =========================================================


async def _report_for(env, campaign_id: int) -> str:
    async with env.maker() as session:
        campaign = await get_campaign(session, campaign_id)
        return await bot_module.build_campaign_report(session, campaign)


async def test_build_campaign_report_none_realised(env) -> None:
    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[None, None, None],
        deliverable=[True, True, True],
    )

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Целевых клиентов") == (
        "Целевых клиентов (targets): 3"
    )
    assert _report_value(report, "Доставлено") == (
        "Доставлено (есть MAX user_id): 3"
    )
    assert _report_value(report, "Реализовали бонус") == (
        "Реализовали бонус: 0"
    )
    assert _report_value(report, "Конверсия") == "Конверсия: 0.0%"
    assert _report_value(report, "Выдано бонусов") == "Выдано бонусов: 300"
    assert _report_value(report, "Реализовано бонусов") == (
        "Реализовано бонусов: 0"
    )
    assert _report_value(report, "Отозвано/к отзыву") == (
        "Отозвано/к отзыву: 300"
    )


async def test_build_campaign_report_undeliverable_target(env) -> None:
    """Клиент без MAX user_id не считается получателем оффера."""

    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[None, None, None],
        deliverable=[True, True, False],
    )

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Целевых клиентов") == (
        "Целевых клиентов (targets): 3"
    )
    assert _report_value(report, "Доставлено") == (
        "Доставлено (есть MAX user_id): 2"
    )
    assert _report_value(report, "Реализовали бонус") == (
        "Реализовали бонус: 0"
    )
    assert _report_value(report, "Конверсия") == "Конверсия: 0.0%"


async def test_build_campaign_report_zero_realised(env) -> None:
    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[0, 0, 0],
        deliverable=[True, True, True],
    )

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Реализовали бонус") == (
        "Реализовали бонус: 0"
    )
    assert _report_value(report, "Реализовано бонусов") == (
        "Реализовано бонусов: 0"
    )
    assert _report_value(report, "Отозвано/к отзыву") == (
        "Отозвано/к отзыву: 300"
    )


async def test_build_campaign_report_partial_realised(env) -> None:
    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[100, 50, None],
        deliverable=[True, True, True],
    )

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Реализовали бонус") == (
        "Реализовали бонус: 2"
    )
    assert _report_value(report, "Конверсия") == "Конверсия: 66.7%"
    assert _report_value(report, "Выдано бонусов") == "Выдано бонусов: 300"
    assert _report_value(report, "Реализовано бонусов") == (
        "Реализовано бонусов: 150"
    )
    assert _report_value(report, "Отозвано/к отзыву") == (
        "Отозвано/к отзыву: 150"
    )


async def test_build_campaign_report_full_realised(env) -> None:
    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[100, 100, 100],
        deliverable=[True, True, True],
    )

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Реализовали бонус") == (
        "Реализовали бонус: 3"
    )
    assert _report_value(report, "Конверсия") == "Конверсия: 100.0%"
    assert _report_value(report, "Реализовано бонусов") == (
        "Реализовано бонусов: 300"
    )
    assert _report_value(report, "Отозвано/к отзыву") == (
        "Отозвано/к отзыву: 0"
    )


async def test_build_campaign_report_completed_uses_stored_revoked_total(
    env,
) -> None:
    campaign_id, _ = await _prepare_report_targets(
        env,
        amounts=[100, 100, 100],
        realised=[None, None, None],
        deliverable=[True, True, True],
    )

    async with env.maker() as session:
        campaign = await session.get(Campaign, campaign_id)
        campaign.status = CampaignStatus.COMPLETED.value
        campaign.config = {"revoked_total": 777}
        await session.commit()

    report = await _report_for(env, campaign_id)

    assert _report_value(report, "Отозвано/к отзыву") == (
        "Отозвано/к отзыву: 777"
    )
    assert _report_value(report, "Статус") == "Статус: COMPLETED"


# =========================================================
# 8. send_campaign_offers
# =========================================================


async def test_send_campaign_offers_not_approved(env) -> None:
    campaign_id = await _ingest(env)

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result == {"error": 1}
    assert env.loyalty.accruals == []
    assert env.outgoing == []


async def test_send_campaign_offers_expired(env) -> None:
    campaign_id = await _ingest(env)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    await _set_campaign_ends(
        env, campaign_id, datetime.utcnow() - timedelta(hours=1)
    )

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result == {"error": 1}
    assert env.loyalty.accruals == []
    assert env.notified


async def test_send_campaign_offers_skips_clients_without_max_user_id(
    env,
) -> None:
    campaign_id = await _ingest(env)
    targets = await _targets(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result["sent"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == len(targets)
    assert env.loyalty.accruals == []
    assert env.outgoing == []


async def test_send_campaign_offers_happy_path(env) -> None:
    campaign_id = await _ingest(env)
    targets = await _targets(env, campaign_id)
    await _make_all_deliverable(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result["sent"] == len(targets)
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert len(env.loyalty.accruals) == len(targets)
    assert len(env.outgoing) == len(targets)
    assert all("бонус" in item["text"].lower() for item in env.outgoing)


async def test_send_campaign_offers_busy(env) -> None:
    campaign_id = await _ingest(env)
    await _make_all_deliverable(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    bot_module._sending_campaigns.add(campaign_id)

    try:
        result = await bot_module.send_campaign_offers(campaign_id)
    finally:
        bot_module._sending_campaigns.discard(campaign_id)

    assert result == {"busy": 1}
    assert env.loyalty.accruals == []


async def test_send_campaign_offers_accrue_error_skips_offer(env) -> None:
    campaign_id = await _ingest(env)
    targets = await _targets(env, campaign_id)
    await _make_all_deliverable(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    failing = targets[0]
    env.loyalty.accrue_errors.add(failing.id)

    async with env.maker() as session:
        client = await session.get(Client, failing.client_id)
        failing_uid = int(client.max_user_id)

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result["sent"] == len(targets) - 1
    assert result["failed"] == 1
    assert len(env.outgoing) == len(targets) - 1
    assert failing_uid not in [item["user_id"] for item in env.outgoing]


# =========================================================
# 9. finish_expired_campaigns
# =========================================================


async def test_finish_expired_campaigns_none_realised_full_revoke(
    env,
) -> None:
    campaign_id, target_ids = await _prepare_expired_campaign(env)

    finished = await bot_module.finish_expired_campaigns()

    assert [item[0] for item in finished] == [campaign_id]
    assert len(env.loyalty.revocations) == len(target_ids)
    assert {item["amount"] for item in env.loyalty.revocations} == {100}

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.COMPLETED.value
    assert campaign.config["revoked_total"] == 100 * len(target_ids)

    # Повторный вызов на завершённой кампании ничего не делает.
    assert await bot_module.finish_expired_campaigns() == []


async def test_finish_expired_campaigns_zero_realised(env) -> None:
    env.loyalty.fetch_realised_default = 0
    campaign_id, target_ids = await _prepare_expired_campaign(env)

    finished = await bot_module.finish_expired_campaigns()

    assert len(finished) == 1
    assert len(env.loyalty.revocations) == len(target_ids)
    assert {item["amount"] for item in env.loyalty.revocations} == {100}

    targets = await _targets(env, campaign_id)
    assert all(target.bonus_realised == 0 for target in targets)


async def test_finish_expired_campaigns_partial_revoke(env) -> None:
    campaign_id, target_ids = await _prepare_expired_campaign(env)
    env.loyalty.fetch_realised_map = {target_ids[0]: 40}

    finished = await bot_module.finish_expired_campaigns()

    assert len(finished) == 1

    partial = [
        item
        for item in env.loyalty.revocations
        if item["target_id"] == target_ids[0]
    ]
    assert len(partial) == 1
    assert partial[0]["amount"] == 60

    targets = {
        target.id: target for target in await _targets(env, campaign_id)
    }
    assert targets[target_ids[0]].bonus_realised == 40


async def test_finish_expired_campaigns_no_revoke_when_fully_realised(
    env,
) -> None:
    env.loyalty.fetch_realised_default = 100
    campaign_id, _ = await _prepare_expired_campaign(env)

    finished = await bot_module.finish_expired_campaigns()

    assert len(finished) == 1
    assert env.loyalty.revocations == []

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.COMPLETED.value
    assert campaign.config["revoked_total"] == 0


async def test_finish_expired_campaigns_rollback_then_retry(env) -> None:
    campaign_id, target_ids = await _prepare_expired_campaign(env)
    env.loyalty.revoke_errors.add(target_ids[0])

    with pytest.raises(RuntimeError):
        await bot_module.finish_expired_campaigns()

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.APPROVED.value

    targets = {
        target.id: target for target in await _targets(env, campaign_id)
    }
    assert targets[target_ids[0]].bonus_realised is None

    env.loyalty.revoke_errors.clear()

    finished = await bot_module.finish_expired_campaigns()

    assert [item[0] for item in finished] == [campaign_id]

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.COMPLETED.value


# =========================================================
# 10. Авторизация и FSM-гигиена
# =========================================================


async def test_handle_callback_non_admin_denied_without_db(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bot_module, "ADMIN_IDS", {"7"})
    monkeypatch.setattr(bot_module, "async_session", _db_bomb)

    event = FakeCallbackEvent("adm:menu", user_id=999)
    context = RecordingContext()

    await bot_module.handle_callback(event, context)

    assert event.acked == ["Доступно только администратору."]
    assert event.edits == []
    assert context.cleared == 0


async def test_handle_admin_callback_checks_rights(monkeypatch) -> None:
    monkeypatch.setattr(bot_module, "ADMIN_IDS", {"7"})
    monkeypatch.setattr(bot_module, "async_session", _db_bomb)

    event = FakeCallbackEvent("adm:menu", user_id=999)
    context = RecordingContext()

    await bot_module._handle_admin_callback(
        event, context, "999", "adm:menu"
    )

    assert event.acked == ["Доступно только администратору."]
    assert event.edits == []
    assert context.cleared == 0


async def test_handle_callback_admin_reaches_menu(env) -> None:
    event = FakeCallbackEvent("adm:menu", user_id=7)
    context = RecordingContext()

    await bot_module.handle_callback(event, context)

    assert event.acked == []
    assert len(event.edits) == 1
    assert "Админ-меню" in event.edits[0]["text"]


async def test_admin_callback_fsm_hygiene(env) -> None:
    menu_event = FakeCallbackEvent("adm:menu", user_id=7)
    menu_context = RecordingContext()

    await bot_module._handle_admin_callback(
        menu_event, menu_context, "7", "adm:menu"
    )

    assert menu_context.cleared == 1
    assert menu_event.edits

    upload_event = FakeCallbackEvent("adm:upload", user_id=7, chat_id=555)
    upload_context = RecordingContext()

    await bot_module._handle_admin_callback(
        upload_event, upload_context, "7", "adm:upload"
    )

    assert upload_context.cleared == 0
    assert len(upload_context.states) == 1
    assert str(upload_context.states[0]) == str(
        bot_module.AdminStates.waiting_csv
    )


# =========================================================
# 11. handle_launch_confirmation_text
# =========================================================


@pytest.mark.parametrize("answer_text", ["да", "yes"])
async def test_launch_confirmation_text_yes_starts(
    env, spawned, answer_text
) -> None:
    campaign_id = await _ingest(env)
    context = RecordingContext({"confirm_campaign_id": campaign_id})
    event = FakeTextEvent(answer_text)

    await bot_module.handle_launch_confirmation_text(event, context)

    assert len(spawned) == 1
    assert spawned[0][0] == f"send_campaign_{campaign_id}"
    assert context.cleared == 1

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.APPROVED.value
    assert any(
        "рассылк" in answer for answer in event.message.answers
    )


@pytest.mark.parametrize("answer_text", ["ок", "ok"])
async def test_launch_confirmation_text_ok_does_not_start(
    env, spawned, answer_text
) -> None:
    campaign_id = await _ingest(env)
    context = RecordingContext({"confirm_campaign_id": campaign_id})
    event = FakeTextEvent(answer_text)

    await bot_module.handle_launch_confirmation_text(event, context)

    assert spawned == []
    assert context.cleared == 0

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value
    assert any(
        "Подтвердите запуск" in answer for answer in event.message.answers
    )


async def test_launch_confirmation_text_no_cancels(env, spawned) -> None:
    campaign_id = await _ingest(env)
    context = RecordingContext({"confirm_campaign_id": campaign_id})
    event = FakeTextEvent("нет")

    await bot_module.handle_launch_confirmation_text(event, context)

    assert spawned == []
    assert context.cleared == 1

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value
    assert event.message.answers


# =========================================================
# 12. Повторная рассылка: защита через offers_sent
# =========================================================


async def test_send_campaign_offers_repeat_requires_force(env) -> None:
    """Повтор без force не дублирует офферы; force отправляет заново."""

    campaign_id = await _ingest(env)
    targets = await _targets(env, campaign_id)
    await _make_all_deliverable(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    first = await bot_module.send_campaign_offers(campaign_id)

    assert first["sent"] == len(targets)
    assert first["failed"] == 0
    assert len(env.outgoing) == len(targets)
    assert len(env.loyalty.accruals) == len(targets)

    campaign = await _campaign(env, campaign_id)
    offers_sent = (campaign.config or {}).get("offers_sent")

    assert isinstance(offers_sent, dict)
    assert offers_sent["sent"] == len(targets)

    outgoing_after_first = len(env.outgoing)

    repeat = await bot_module.send_campaign_offers(campaign_id)

    assert repeat == {"already": 1}
    assert len(env.outgoing) == outgoing_after_first
    assert len(env.loyalty.accruals) == len(targets)

    forced = await bot_module.send_campaign_offers(campaign_id, force=True)

    assert forced["sent"] > 0
    assert forced["sent"] == len(targets)
    assert len(env.outgoing) == outgoing_after_first + forced["sent"]


async def test_send_campaign_offers_repeat_not_marked_when_nothing_sent(
    env,
) -> None:
    """Если не отправлено ни одного оффера, повтор не блокируется."""

    campaign_id = await _ingest(env)
    targets = await _targets(env, campaign_id)

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    first = await bot_module.send_campaign_offers(campaign_id)

    assert first == {
        "sent": 0,
        "failed": 0,
        "skipped": len(targets),
        "aborted": 0,
    }

    campaign = await _campaign(env, campaign_id)
    assert not (campaign.config or {}).get("offers_sent")

    second = await bot_module.send_campaign_offers(campaign_id)

    assert second["sent"] == 0
    assert second["skipped"] == len(targets)
    assert "already" not in second
    assert env.outgoing == []
    assert env.loyalty.accruals == []


# =========================================================
# 13. Отзыв бонусов: сбой на тике + повтор
# =========================================================


async def test_finish_expired_campaigns_retry_counts_all_revokes(env) -> None:
    """После сбоя и повтора revoked_total учитывает оба отзыва."""

    amounts = [120, 45]

    campaign_id, target_ids = await _prepare_report_targets(
        env,
        amounts=amounts,
        realised=[None, None],
        deliverable=[True, True],
    )
    assert len(target_ids) == 2

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    await _set_campaign_ends(
        env, campaign_id, datetime.utcnow() - timedelta(hours=1)
    )

    # Первый тик: первый target успевает отозвать бонус, второй падает.
    env.loyalty.revoke_errors.add(target_ids[1])

    with pytest.raises(RuntimeError):
        await bot_module.finish_expired_campaigns()

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.APPROVED.value
    assert "revoked_total" not in (campaign.config or {})

    # Отзыв первого target состоялся на стороне адаптера и учтён в фейке.
    assert [item["target_id"] for item in env.loyalty.revocations] == [
        target_ids[0]
    ]
    assert env.loyalty.revoked_amounts[
        (campaign_id, target_ids[0])
    ] == amounts[0]

    env.loyalty.revoke_errors.clear()

    finished = await bot_module.finish_expired_campaigns()

    assert [item[0] for item in finished] == [campaign_id]

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.COMPLETED.value
    # Итог — сумма ОБОИХ отзывов, а не только успешного на повторе.
    assert campaign.config["revoked_total"] == sum(amounts)

    # Повтор не продублировал отзыв первого target.
    assert len(env.loyalty.revocations) == 2
    assert sorted(item["target_id"] for item in env.loyalty.revocations) == (
        sorted(target_ids)
    )


# =========================================================
# 14. Защита от старых карточек и DRAFT-рассылки
# =========================================================


async def test_admin_bonus_stale_category_rejected(env) -> None:
    """Бонус категории старой кампании не меняется — карточка устарела."""

    first_id = await _ingest(env)
    old_category = await _category_by_group(env, first_id, "0")

    # Гарантируем, что кампания #1 не «последняя».
    async with env.maker() as session:
        campaign = await session.get(Campaign, first_id)
        campaign.launched_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()

    second_id = await _ingest(env)
    assert second_id != first_id
    assert await bot_module._stale_campaign_message(first_id) is not None

    before_bonus = int(old_category.final_bonus)
    before_targets = {
        target.id: int(target.bonus_amount)
        for target in await _targets(env, first_id)
        if target.category_id == old_category.id
    }
    assert before_targets

    payload = f"adm:bonus:{old_category.id}:{bot_module.BONUS_STEP}"
    event = FakeCallbackEvent(payload, user_id=7)
    context = RecordingContext()

    await bot_module._handle_admin_callback(event, context, "7", payload)

    refreshed = await _category_by_group(env, first_id, "0")
    assert int(refreshed.final_bonus) == before_bonus

    after_targets = {
        target.id: int(target.bonus_amount)
        for target in await _targets(env, first_id)
        if target.category_id == old_category.id
    }
    assert after_targets == before_targets

    assert event.edits
    assert "устарела" in (event.edits[-1].get("notification") or "")
    # Пользователю отрисован свежий список категорий.
    assert f"Кампания #{second_id}" in event.edits[-1]["text"]

    # Кампания #1 не менялась, а её targets — тем более.
    first_campaign = await _campaign(env, first_id)
    assert first_campaign.status == CampaignStatus.DRAFT.value


async def test_admin_send_on_draft_notifies(env, spawned) -> None:
    """Кнопка «Отправить предложения» удалена, payload adm:send отключён."""

    campaign_id = await _ingest(env)

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value
    assert spawned == []

    # В меню нет кнопки повторной рассылки, но есть выгрузка истории.
    payloads = _keyboard_payloads(bot_module.admin_menu_keyboard(campaign_id))
    assert not any(str(item).startswith("adm:send") for item in payloads)
    assert "adm:export" in payloads
    assert not any("Отправить предложения" in str(t) for t in _keyboard_texts(
        bot_module.admin_menu_keyboard(campaign_id)
    ))

    event = FakeCallbackEvent(f"adm:send:{campaign_id}", user_id=7)
    context = RecordingContext()

    await bot_module._handle_admin_callback(
        event, context, "7", f"adm:send:{campaign_id}"
    )

    assert spawned == []
    assert env.outgoing == []
    assert env.loyalty.accruals == []

    campaign = await _campaign(env, campaign_id)
    assert campaign.status == CampaignStatus.DRAFT.value
    assert not (campaign.config or {}).get("offers_sent")

    notification = event.edits[-1].get("notification") or ""
    assert "недоступно" in notification.lower()
    assert "Рассылка запущена" not in notification
    assert "Рассылка запущена" not in event.acked


async def test_category_block_fields(env) -> None:
    """После загрузки CSV в категориях нет «Частота» и monetary score."""

    campaign_id = await _ingest(env)

    text, _attachments = await bot_module.build_categories_view()

    assert "Средняя давность последнего посещения" in text
    assert "Средний чек" in text
    assert "Частота" not in text
    assert "monetary score" not in text

    async with env.maker() as session:
        categories = (
            (
                await session.execute(
                    select(CampaignCategory).where(
                        CampaignCategory.campaign_id == campaign_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert categories

    card_text = bot_module._format_category_block(categories[0])
    assert "Средняя давность последнего посещения" in card_text
    assert "Частота" not in card_text
    assert "monetary score" not in card_text


async def test_export_campaigns_csv(env, spawned) -> None:
    """Кнопка выгрузки отдаёт CSV со всеми кампаниями, а не список в чат."""

    first_id = await _ingest(env)
    second_id = await _ingest(env)

    event = FakeCallbackEvent("adm:export", user_id=7)
    context = RecordingContext()

    await bot_module._handle_admin_callback(event, context, "7", "adm:export")

    assert spawned == []
    assert len(env.outgoing) == 1

    attachments = env.outgoing[0]["attachments"]
    assert attachments and len(attachments) == 1

    media = attachments[0]
    assert media.filename == "campaigns.csv"

    data = media.buffer
    assert data.startswith("\ufeff".encode("utf-8"))

    text = data.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]

    assert lines[0].startswith("campaign_id;status;")
    assert len(lines) == 3
    assert lines[1].startswith(f"{first_id};")
    assert lines[2].startswith(f"{second_id};")
    assert "DRAFT" in lines[1] and "DRAFT" in lines[2]


async def test_send_failure_revokes_accrual(env, monkeypatch) -> None:
    """Если MAX не принял сообщение — начисление откатывается."""

    campaign_id = await _ingest(env)

    async with env.maker() as session:
        for client in (
            (await session.execute(select(Client))).scalars().all()
        ):
            client.max_user_id = str(900000 + client.id)
        await session.commit()

    ok, message = await bot_module._approve_campaign(campaign_id)
    assert ok is True, message

    async def failing_outgoing(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(bot_module, "_outgoing", failing_outgoing)

    result = await bot_module.send_campaign_offers(campaign_id)

    assert result["sent"] == 0
    assert result["failed"] > 0

    accrued_keys = {
        (item["campaign_id"], item["target_id"])
        for item in env.loyalty.accruals
    }
    revoked_keys = {
        (item["campaign_id"], item["target_id"])
        for item in env.loyalty.revocations
    }

    assert accrued_keys
    assert revoked_keys == accrued_keys


# =========================================================
# 15. FSM клиента: callback снимает незавершённую регистрацию
# =========================================================


async def test_client_callback_clears_fsm(env) -> None:
    """Клиентский callback сбрасывает состояние ожидания телефона."""

    async with env.maker() as session:
        session.add(
            Client(phone_e164="+79995550001", max_user_id="555001")
        )
        await session.commit()

    event = FakeCallbackEvent("cli:menu", user_id="555001")
    context = RecordingContext()

    await context.set_state(bot_module.ClientStates.waiting_phone)
    assert await context.get_state() == bot_module.ClientStates.waiting_phone

    await bot_module.handle_callback(event, context)

    assert context.cleared == 1
    assert await context.get_state() is None
    assert event.edits
    assert "программе лояльности" in event.edits[-1]["text"]
