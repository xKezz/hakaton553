"""Тесты CSV-эмулятора программы лояльности (loyalty_mock)."""

from __future__ import annotations

import asyncio
import csv
import logging
from datetime import datetime
from pathlib import Path

import pytest

from src.services.loyalty_mock import LoyaltyMockAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER_NAME = "src.services.loyalty_mock"

PHONE = "+79990000001"
OTHER_PHONE = "+79990000002"

ISSUED = datetime(2026, 10, 1, 0, 0)
ENDS = datetime(2026, 10, 30, 23, 59)
SIM_NOW = datetime(2026, 11, 1, 12, 0)
FIXED_NOW = datetime(2026, 10, 1, 12, 0)


def read_rows(path: Path) -> list[dict]:
    """Читает CSV как список словарей (игнорирует ``None``-ключи)."""

    with open(path, encoding="utf-8", newline="") as handle:
        return [
            {key: value for key, value in row.items() if key is not None}
            for row in csv.DictReader(handle)
        ]


def rows_with_op(path: Path, op: str) -> list[dict]:
    """Строки журнала с заданной операцией."""

    return [row for row in read_rows(path) if row.get("op") == op]


# ----------------------------------------------------------------------
# 1. Конструктор ничего не создаёт
# ----------------------------------------------------------------------

def test_constructor_creates_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "loyalty_mock"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    assert not data_dir.exists()
    assert list(tmp_path.iterdir()) == []
    assert adapter.data_dir == data_dir

    default = LoyaltyMockAdapter(now=lambda: FIXED_NOW)
    assert default.data_dir == PROJECT_ROOT / "data" / "loyalty_mock"


# ----------------------------------------------------------------------
# 2. Начисление, баланс, идемпотентность
# ----------------------------------------------------------------------

async def test_accrue_balance_and_idempotency(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
    )
    assert await adapter.fetch_balance(PHONE) == 100
    # Неизвестный телефон — 0, а не None.
    assert await adapter.fetch_balance(OTHER_PHONE) == 0

    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
    )
    rows = rows_with_op(data_dir / "transactions.csv", "ACCRUAL")
    assert len(rows) == 1
    assert int(rows[0]["amount"]) == 100
    assert await adapter.fetch_balance(PHONE) == 100

    # Повтор с другой суммой: warning, первое значение сохраняется.
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await adapter.accrue_bonus(
            phone_e164=PHONE, amount=999, campaign_id=1, target_id=1
        )
    assert len(rows_with_op(data_dir / "transactions.csv", "ACCRUAL")) == 1
    assert await adapter.fetch_balance(PHONE) == 100
    assert "другой суммой" in caplog.text

    # Производный снимок записан и не оставляет temp-файлов.
    balances = read_rows(data_dir / "balances.csv")
    assert [row["phone_e164"] for row in balances] == [PHONE]
    assert int(balances[0]["balance"]) == 100
    assert not list(data_dir.glob(".balances-*"))

    # amount <= 0 — no-op.
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=0, campaign_id=2, target_id=2
    )
    assert len(rows_with_op(data_dir / "transactions.csv", "ACCRUAL")) == 1


# ----------------------------------------------------------------------
# 3. «Рестарт» адаптера
# ----------------------------------------------------------------------

async def test_restart_sees_balance_and_keeps_idempotency(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "loyalty"

    first = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)
    await first.accrue_bonus(
        phone_e164=PHONE,
        amount=250,
        campaign_id=7,
        target_id=42,
        recency=15,
        frequency=4,
        avg_amount=300,
        issued_at=ISSUED,
        ends_at=ENDS,
    )

    second = LoyaltyMockAdapter(
        data_dir=data_dir, now=lambda: datetime(2026, 10, 2, 12, 0)
    )
    assert await second.fetch_balance(PHONE) == 250

    await second.accrue_bonus(
        phone_e164=PHONE, amount=250, campaign_id=7, target_id=42
    )
    assert len(rows_with_op(data_dir / "transactions.csv", "ACCRUAL")) == 1
    assert await second.fetch_balance(PHONE) == 250

    # Метаданные кампанийного счёта пережили рестарт (через note).
    snapshot = second.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["recency"] == 15
    assert snapshot[0]["avg_amount"] == 300.0
    assert snapshot[0]["issued_at"] == ISSUED.isoformat()


# ----------------------------------------------------------------------
# 4. Отзыв бонусов
# ----------------------------------------------------------------------

async def test_revoke_full_partial_and_without_accrual(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    # Отзыв без предварительного начисления — no-op + warning.
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await adapter.revoke_bonus(
            phone_e164=PHONE, amount=50, campaign_id=9, target_id=9
        )
    assert "без начисления" in caplog.text
    assert not (data_dir / "transactions.csv").exists()

    # Частичный отзыв.
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
    )
    await adapter.revoke_bonus(
        phone_e164=PHONE, amount=30, campaign_id=1, target_id=1
    )
    assert await adapter.fetch_balance(PHONE) == 70
    revokes = rows_with_op(data_dir / "transactions.csv", "REVOKE")
    assert len(revokes) == 1
    assert int(revokes[0]["amount"]) == -30

    # Второй отзыв добирает неизрасходованный остаток.
    second = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=70, campaign_id=1, target_id=1
    )
    assert second == 70
    assert len(rows_with_op(data_dir / "transactions.csv", "REVOKE")) == 2
    assert await adapter.fetch_balance(PHONE) == 0

    # Когда отзывать уже нечего — повтор возвращает отозванную сумму.
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        repeat = await adapter.revoke_bonus(
            phone_e164=PHONE, amount=70, campaign_id=1, target_id=1
        )
    assert repeat == 100
    assert "повторный отзыв" in caplog.text
    assert len(rows_with_op(data_dir / "transactions.csv", "REVOKE")) == 2
    assert await adapter.fetch_balance(PHONE) == 0

    # Полный отзыв: min(запрошенная, остаток).
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=40, campaign_id=2, target_id=2
    )
    await adapter.revoke_bonus(
        phone_e164=PHONE, amount=1000, campaign_id=2, target_id=2
    )
    revokes = [
        row
        for row in rows_with_op(data_dir / "transactions.csv", "REVOKE")
        if row.get("target_id") == "2"
    ]
    assert len(revokes) == 1
    assert int(revokes[0]["amount"]) == -40
    assert await adapter.fetch_balance(PHONE) == 0

    # Нулевой запрос — no-op без строки.
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=15, campaign_id=3, target_id=3
    )
    await adapter.revoke_bonus(
        phone_e164=PHONE, amount=0, campaign_id=3, target_id=3
    )
    assert not [
        row
        for row in rows_with_op(data_dir / "transactions.csv", "REVOKE")
        if row.get("target_id") == "3"
    ]
    assert await adapter.fetch_balance(PHONE) == 15


# ----------------------------------------------------------------------
# 5. fetch_realised
# ----------------------------------------------------------------------

async def test_fetch_realised_none_zero_and_determinism(
    tmp_path: Path,
) -> None:
    # None: сейчас раньше issued_at, окно ещё не началось → визитов нет.
    early_dir = tmp_path / "early"
    early = LoyaltyMockAdapter(
        data_dir=early_dir, now=lambda: datetime(2026, 10, 1, 12, 0)
    )
    await early.accrue_bonus(
        phone_e164=PHONE,
        amount=100,
        campaign_id=5,
        target_id=5,
        recency=10,
        issued_at=datetime(2026, 10, 5),
        ends_at=datetime(2026, 10, 10),
    )
    assert (
        await early.fetch_realised(
            campaign_id=5,
            target_id=5,
            phone_e164=PHONE,
            bonus_amount=100,
            issued_at=datetime(2026, 10, 5),
            ends_at=datetime(2026, 10, 10),
        )
        is None
    )
    assert not (early_dir / "visits.csv").exists()

    # 0: визиты есть, но realise_rate = 0 → списаний нет.
    zero = LoyaltyMockAdapter(
        data_dir=tmp_path / "zero", now=lambda: SIM_NOW, realise_rate=0.0
    )
    await zero.accrue_bonus(
        phone_e164=PHONE,
        amount=500,
        campaign_id=1,
        target_id=1,
        recency=10,
        avg_amount=350,
        issued_at=ISSUED,
        ends_at=ENDS,
    )
    assert await zero.advance(SIM_NOW) > 0
    assert (
        await zero.fetch_realised(
            campaign_id=1,
            target_id=1,
            phone_e164=PHONE,
            bonus_amount=500,
            issued_at=ISSUED,
            ends_at=ENDS,
        )
        == 0
    )
    zero_visits = read_rows(tmp_path / "zero" / "visits.csv")
    assert zero_visits
    assert all(int(row["bonus_spent"]) == 0 for row in zero_visits)

    # > 0 и <= bonus_amount + детерминизм двух экземпляров с одним seed.
    first = LoyaltyMockAdapter(
        data_dir=tmp_path / "a1", now=lambda: SIM_NOW, seed=7
    )
    second = LoyaltyMockAdapter(
        data_dir=tmp_path / "a2", now=lambda: SIM_NOW, seed=7
    )
    for adapter in (first, second):
        await adapter.accrue_bonus(
            phone_e164=PHONE,
            amount=500,
            campaign_id=1,
            target_id=1,
            recency=10,
            avg_amount=350,
            issued_at=ISSUED,
            ends_at=ENDS,
        )

    kwargs = {
        "campaign_id": 1,
        "target_id": 1,
        "phone_e164": PHONE,
        "issued_at": ISSUED,
        "ends_at": ENDS,
    }
    realised_first = await first.fetch_realised(bonus_amount=1000, **kwargs)
    realised_second = await second.fetch_realised(bonus_amount=1000, **kwargs)

    assert realised_first is not None and realised_first > 0
    assert realised_first == realised_second
    assert realised_first <= 1000

    # Сумма списаний не превышает bonus_amount.
    capped = await first.fetch_realised(bonus_amount=100, **kwargs)
    assert capped == 100

    # Другой телефон в том же окне — визитов нет.
    assert (
        await first.fetch_realised(
            campaign_id=1,
            target_id=1,
            phone_e164=OTHER_PHONE,
            bonus_amount=1000,
            issued_at=ISSUED,
            ends_at=ENDS,
        )
        is None
    )

    # Все визиты лежат внутри окна кампании.
    visits = read_rows(tmp_path / "a1" / "visits.csv")
    assert visits
    for row in visits:
        moment = datetime.fromisoformat(row["ts"])
        assert ISSUED <= moment <= ENDS
        assert row["phone_e164"] == PHONE


# ----------------------------------------------------------------------
# 6. Конкурентные начисления
# ----------------------------------------------------------------------

async def test_concurrent_accruals_write_single_row(tmp_path: Path) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    await asyncio.gather(
        *[
            adapter.accrue_bonus(
                phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
            )
            for _ in range(10)
        ]
    )

    assert len(rows_with_op(data_dir / "transactions.csv", "ACCRUAL")) == 1
    assert await adapter.fetch_balance(PHONE) == 100


# ----------------------------------------------------------------------
# 7. Рваная последняя строка и мусор
# ----------------------------------------------------------------------

async def test_torn_last_line_and_garbage_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "loyalty"
    data_dir.mkdir(parents=True)
    (data_dir / "transactions.csv").write_text(
        "tx_id,ts,phone_e164,op,amount,campaign_id,target_id,note\n"
        f"acc-1-1,2026-10-01T12:00:00,{PHONE},ACCRUAL,50,1,1,\n"
        f"bad-op,2026-10-01T12:00:00,{PHONE},TRANSFER,10,2,2,\n"
        "short,2026-10-01T12:00:00\n"
        "torn,2026-10-01T12:00:00,+7999",
        encoding="utf-8",
    )

    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)
    assert await adapter.fetch_balance(PHONE) == 50

    # Дозапись после рваной строки не склеивает строки.
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=25, campaign_id=3, target_id=3
    )
    assert await adapter.fetch_balance(PHONE) == 75
    rows = read_rows(data_dir / "transactions.csv")
    assert len(rows_with_op(data_dir / "transactions.csv", "ACCRUAL")) == 2
    # 2 валидных ACCRUAL + bad-op + short + torn = 5 строк.
    assert len(rows) == 5


# ----------------------------------------------------------------------
# 8. Телефон как строка: «+», cp1251, utf-8-sig
# ----------------------------------------------------------------------

async def test_phone_stays_string_and_external_encodings(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)
    await adapter.accrue_bonus(
        phone_e164="+7 (999) 000-00-01", amount=10, campaign_id=1, target_id=1
    )

    raw = (data_dir / "transactions.csv").read_text(encoding="utf-8")
    assert "+79990000001" in raw
    assert "7.999" not in raw
    # Нормализация на входе: другой формат того же номера — тот же баланс.
    assert await adapter.fetch_balance("8 999 000 00 01") == 10

    # Внешний файл в cp1251.
    cp_dir = tmp_path / "cp1251"
    cp_dir.mkdir()
    cp_text = (
        "tx_id,ts,phone_e164,op,amount,campaign_id,target_id,note\n"
        f"acc-9-9,2026-10-01T12:00:00,{OTHER_PHONE},ACCRUAL,77,9,9,{{}}\n"
    )
    (cp_dir / "transactions.csv").write_bytes(cp_text.encode("cp1251"))
    cp_adapter = LoyaltyMockAdapter(data_dir=cp_dir, now=lambda: FIXED_NOW)
    assert await cp_adapter.fetch_balance(OTHER_PHONE) == 77
    await cp_adapter.accrue_bonus(
        phone_e164=OTHER_PHONE, amount=3, campaign_id=10, target_id=10
    )
    assert await cp_adapter.fetch_balance(OTHER_PHONE) == 80

    # Внешний файл в utf-8-sig.
    sig_dir = tmp_path / "utf8sig"
    sig_dir.mkdir()
    third_phone = "+79990000003"
    sig_text = (
        "tx_id,ts,phone_e164,op,amount,campaign_id,target_id,note\n"
        f"acc-11-11,2026-10-01T12:00:00,{third_phone},ACCRUAL,77,11,11,{{}}\n"
    )
    (sig_dir / "transactions.csv").write_bytes(
        b"\xef\xbb\xbf" + sig_text.encode("utf-8")
    )
    sig_adapter = LoyaltyMockAdapter(data_dir=sig_dir, now=lambda: FIXED_NOW)
    assert await sig_adapter.fetch_balance(third_phone) == 77
    await sig_adapter.accrue_bonus(
        phone_e164=third_phone, amount=1, campaign_id=12, target_id=12
    )
    assert await sig_adapter.fetch_balance(third_phone) == 78


# ----------------------------------------------------------------------
# 9. advance идемпотентен и не выходит за ends_at
# ----------------------------------------------------------------------

async def test_advance_is_idempotent_and_bounded(tmp_path: Path) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: SIM_NOW)
    await adapter.accrue_bonus(
        phone_e164=PHONE,
        amount=500,
        campaign_id=1,
        target_id=1,
        recency=10,
        avg_amount=350,
        issued_at=ISSUED,
        ends_at=ENDS,
    )

    first = await adapter.advance(SIM_NOW)
    assert first > 0
    assert await adapter.advance(SIM_NOW) == 0
    assert await adapter.advance() == 0  # инъектированные часы

    visits = read_rows(data_dir / "visits.csv")
    assert len(visits) == first
    for row in visits:
        moment = datetime.fromisoformat(row["ts"])
        assert ISSUED <= moment <= ENDS


# ----------------------------------------------------------------------
# 10. revoke_bonus возвращает фактическую сумму и идемпотентен
# ----------------------------------------------------------------------

async def test_revoke_bonus_returns_actual_amount(tmp_path: Path) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    tx_path = data_dir / "transactions.csv"

    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
    )

    revoked = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=30, campaign_id=1, target_id=1
    )

    assert revoked == 30
    assert await adapter.fetch_balance(PHONE) == 70

    revokes = rows_with_op(tx_path, "REVOKE")
    assert len(revokes) == 1
    assert int(revokes[0]["amount"]) == -30
    total_before = sum(int(row["amount"]) for row in revokes)

    # Повтор добирает остаток: сумма не превышает начисленного.
    repeat = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=70, campaign_id=1, target_id=1
    )

    assert repeat == 70
    assert await adapter.fetch_balance(PHONE) == 0

    revokes = rows_with_op(tx_path, "REVOKE")
    assert len(revokes) == 2
    assert sum(int(row["amount"]) for row in revokes) == -100

    # Когда отзывать нечего, повтор возвращает уже отозванную сумму.
    nothing_left = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=70, campaign_id=1, target_id=1
    )

    assert nothing_left == 100
    assert len(rows_with_op(tx_path, "REVOKE")) == 2

    # Фактическая сумма = min(запрошенная, неизрасходованный остаток).
    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=40, campaign_id=2, target_id=2
    )

    capped = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=1000, campaign_id=2, target_id=2
    )

    assert capped == 40
    assert await adapter.fetch_balance(PHONE) == 0
    assert total_before == -30


# ----------------------------------------------------------------------
# 11. Телефон маскируется в snapshot() и в логах
# ----------------------------------------------------------------------

async def test_phone_masked_in_snapshot_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "loyalty"
    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        await adapter.accrue_bonus(
            phone_e164=PHONE, amount=100, campaign_id=1, target_id=1
        )

    snapshot = adapter.snapshot()
    assert len(snapshot) == 1

    account = snapshot[0]
    assert account["phone_masked"] == "***0001"
    assert "phone_e164" not in account
    assert PHONE not in repr(snapshot)

    messages = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME
    )
    assert messages
    assert PHONE not in messages
    assert "***0001" in messages


# ----------------------------------------------------------------------
# 12. Один общий баланс клиента + атрибуция бонусов кампании
# ----------------------------------------------------------------------

OPENING_AMOUNT = 50
CAMPAIGN_BONUS = 100
TOTAL_BALANCE = OPENING_AMOUNT + CAMPAIGN_BONUS


def seed_opening_balance(data_dir: Path, phone: str, amount: int) -> None:
    """Пишет «старый» баланс клиента (op=OPENING) до создания адаптера."""

    data_dir.mkdir(parents=True, exist_ok=True)

    with open(
        data_dir / "transactions.csv", "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "tx_id",
                "ts",
                "phone_e164",
                "op",
                "amount",
                "campaign_id",
                "target_id",
                "note",
            ]
        )
        writer.writerow(
            [
                "open-1",
                "2026-09-01T10:00:00",
                phone,
                "OPENING",
                amount,
                "",
                "",
                "opening balance",
            ]
        )


def journal_total(data_dir: Path, phone: str) -> int:
    """Баланс как сумма amount по журналу (проверка инварианта)."""

    return sum(
        int(row["amount"])
        for row in read_rows(data_dir / "transactions.csv")
        if row.get("phone_e164") == phone
    )


@pytest.mark.parametrize(
    ("spend", "expected_balance", "expected_realised"),
    [
        (0, 150, 0),
        (30, 120, 30),
        (70, 80, 70),
        (100, 50, 100),
        (150, 0, 100),
    ],
)
async def test_shared_balance_and_campaign_attribution(
    tmp_path: Path,
    spend: int,
    expected_balance: int,
    expected_realised: int,
) -> None:
    """Старые и кампанийные бонусы — один баланс; realised <= bonus_amount."""

    data_dir = tmp_path / f"shared_{spend}"
    seed_opening_balance(data_dir, PHONE, OPENING_AMOUNT)

    adapter = LoyaltyMockAdapter(
        data_dir=data_dir,
        now=lambda: FIXED_NOW,
        realise_rate=1.0,
        seed=1,
    )

    # Один день окна: чек = avg_amount * 0.6 при u_amount = 0.
    await adapter.accrue_bonus(
        phone_e164=PHONE,
        amount=CAMPAIGN_BONUS,
        campaign_id=1,
        target_id=1,
        recency=10,
        frequency=3,
        avg_amount=(spend / 0.6 if spend else 300.0),
        issued_at=ISSUED,
        ends_at=ENDS,
    )

    assert await adapter.fetch_balance(PHONE) == TOTAL_BALANCE

    # Детерминированная симуляция: визит есть всегда, списание — по сценарию.
    adapter._hash_unit = lambda *args, **kwargs: 0.0

    if spend == 0:
        adapter._realise_rate = 0.0

    realised = await adapter.fetch_realised(
        campaign_id=1,
        target_id=1,
        phone_e164=PHONE,
        bonus_amount=CAMPAIGN_BONUS,
        issued_at=ISSUED,
        ends_at=ENDS,
    )
    balance = await adapter.fetch_balance(PHONE)

    assert realised is not None
    assert realised == expected_realised
    assert realised <= CAMPAIGN_BONUS
    assert balance == expected_balance
    # Баланс — это ровно сумма операций в журнале.
    assert journal_total(data_dir, PHONE) == expected_balance

    # «Рестарт»: состояние восстанавливается из CSV один в один.
    restarted = LoyaltyMockAdapter(
        data_dir=data_dir,
        now=lambda: FIXED_NOW,
        realise_rate=1.0,
        seed=1,
    )

    assert await restarted.fetch_balance(PHONE) == expected_balance
    assert (
        await restarted.fetch_realised(
            campaign_id=1,
            target_id=1,
            phone_e164=PHONE,
            bonus_amount=CAMPAIGN_BONUS,
            issued_at=ISSUED,
            ends_at=ENDS,
        )
        == expected_realised
    )


async def test_revoke_keeps_old_bonuses(tmp_path: Path) -> None:
    """Отзыв кампании не трогает «старые» бонусы и не уводит баланс в минус."""

    data_dir = tmp_path / "revoke_shared"
    seed_opening_balance(data_dir, PHONE, OPENING_AMOUNT)

    adapter = LoyaltyMockAdapter(data_dir=data_dir, now=lambda: FIXED_NOW)

    await adapter.accrue_bonus(
        phone_e164=PHONE, amount=CAMPAIGN_BONUS, campaign_id=1, target_id=1
    )

    revoked = await adapter.revoke_bonus(
        phone_e164=PHONE, amount=1000, campaign_id=1, target_id=1
    )

    assert revoked == CAMPAIGN_BONUS
    assert await adapter.fetch_balance(PHONE) == OPENING_AMOUNT
    assert journal_total(data_dir, PHONE) == OPENING_AMOUNT
