"""CSV-эмулятор внешней программы лояльности и «жизни кофейни».

Реального Loyalty API в проекте нет, поэтому данный модуль его
заменяет для MAX-бота Win-Back MVP. Источник истины по балансу —
``transactions.csv`` (журнал операций), визиты клиентов эмулируются
в ``visits.csv``, а ``balances.csv`` — производный снимок «для
наглядности» (логика его никогда не читает).

Модель детерминированная: никакого ``random``, только хэш sha256 от
``seed``, телефона, кампании, дня и роли. Благодаря этому повторный
``advance`` не создаёт дубликатов, а разные экземпляры адаптера с
одинаковым ``seed`` дают одинаковый результат.

Все публичные async-методы сериализуются экземплярным
:class:`asyncio.Lock`; внутренние синхронные ``_ensure_loaded`` и
``_append_*`` лок не берут (они не реентерабельны).
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.data_process.predprocessing import Preprocessor

logger = logging.getLogger(__name__)

TX_HEADER: list[str] = [
    "tx_id",
    "ts",
    "phone_e164",
    "op",
    "amount",
    "campaign_id",
    "target_id",
    "note",
]
VISIT_HEADER: list[str] = [
    "visit_id",
    "ts",
    "phone_e164",
    "amount",
    "bonus_spent",
    "campaign_id",
]
BALANCE_HEADER: list[str] = ["phone_e164", "balance", "updated_at"]

OP_ACCRUAL = "ACCRUAL"
OP_REDEMPTION = "REDEMPTION"
OP_REVOKE = "REVOKE"
OP_OPENING = "OPENING"
KNOWN_OPS = {OP_ACCRUAL, OP_REDEMPTION, OP_REVOKE, OP_OPENING}

#: Час суток, которым датируются эмулированные визиты.
VISIT_HOUR = 12

_DAY = timedelta(days=1)


def _mask_phone(phone: str | None) -> str:
    """Маскирует телефон для логов (персональные данные не пишем)."""

    if not phone:
        return "***"

    value = str(phone).strip()

    if len(value) <= 4:
        # Короткое значение нельзя маскировать «хвостом» — он раскроет всё.
        return "***"

    return f"***{value[-4:]}"


def _iso(value: datetime | None) -> str | None:
    """Сериализует дату в ISO-8601 (без микросекунд)."""

    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _coerce_dt(value) -> datetime | None:
    """Приводит значение к naive-datetime (UTC), если это возможно."""

    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time(0, 0))
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_int(value) -> int | None:
    """Мягко приводит значение из CSV к целому."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text.replace(",", ".")))
        except ValueError:
            return None


def _parse_float(value) -> float | None:
    """Мягко приводит значение из CSV к числу с плавающей точкой."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


@dataclass
class _Transaction:
    """Строка журнала операций."""

    tx_id: str
    ts: datetime | None
    phone_e164: str
    op: str
    amount: int
    campaign_id: int | None
    target_id: int | None
    note: str


@dataclass
class _Visit:
    """Строка эмулированного визита."""

    visit_id: str
    ts: datetime | None
    phone_e164: str
    amount: float
    bonus_spent: int
    campaign_id: int | None


@dataclass
class _Account:
    """«Кампанийный счёт»: одно начисление (campaign_id, target_id)."""

    phone_e164: str
    campaign_id: int
    target_id: int
    amount: int
    recency: int | None = None
    frequency: int | None = None
    avg_amount: float | None = None
    issued_at: datetime | None = None
    ends_at: datetime | None = None
    created_at: datetime | None = None


@dataclass
class _Window:
    """Окно симуляции визитов для пары (телефон, кампания)."""

    phone_e164: str
    campaign_id: int
    start: datetime
    end: datetime | None
    recency: int | None
    avg_amount: float | None
    target_id: int | None = None


class LoyaltyMockAdapter:
    """CSV-эмулятор программы лояльности с симуляцией визитов.

    Параметры:
        data_dir: каталог с CSV-файлами. По умолчанию
            ``<project_root>/data/loyalty_mock``. Конструктор НИЧЕГО не
            создаёт на диске — файлы появляются лениво при первой
            записи (адаптер создаётся на импорте ``bot.py``).
        now: инъекция часов ``Callable[[], datetime]`` для тестов,
            по умолчанию :func:`datetime.utcnow`.
        realise_rate: вероятность списания бонуса при визите.
        seed: зерно детерминированной симуляции.
    """

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        realise_rate: float = 0.4,
        seed: int = 0,
    ) -> None:
        if data_dir is None:
            data_dir = (
                Path(__file__).resolve().parents[2] / "data" / "loyalty_mock"
            )
        self._data_dir = Path(data_dir)
        self._now: Callable[[], datetime] = now or datetime.utcnow
        self._realise_rate = float(realise_rate)
        self._seed = int(seed)

        self._lock = asyncio.Lock()

        self._loaded = False
        self._signature: tuple | None = None

        self._tx: list[_Transaction] = []
        self._visits: list[_Visit] = []
        self._balances: dict[str, int] = {}
        self._accounts: "OrderedDict[tuple[int, int], _Account]" = OrderedDict()
        self._spent: dict[tuple[int, int], int] = {}
        self._revoked: dict[tuple[int, int], int] = {}
        self._windows: list[_Window] = []
        self._simulated_days: set[tuple[str, int, date]] = set()
        self._visit_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Пути и мелкие помощники
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        """Каталог с CSV-файлами эмулятора."""

        return self._data_dir

    @property
    def _tx_path(self) -> Path:
        return self._data_dir / "transactions.csv"

    @property
    def _visits_path(self) -> Path:
        return self._data_dir / "visits.csv"

    @property
    def _balances_path(self) -> Path:
        return self._data_dir / "balances.csv"

    def _now_dt(self) -> datetime:
        """Текущее время по инъектированным часам (naive UTC)."""

        return _coerce_dt(self._now()) or datetime.utcnow()

    def _normalize_phone(self, value) -> str:
        """Нормализует телефон в E.164, не приводя его к числу.

        Если номер невалиден, возвращает исходную строку (без пробелов),
        чтобы не терять данные и не падать.
        """

        if value is None:
            return ""
        raw = str(value).strip()
        if not raw:
            return ""
        # Excel иногда сохраняет телефон как число: 79990000001.0
        if raw.endswith(".0") and raw[:-2].lstrip("+").isdigit():
            raw = raw[:-2]
        try:
            normalized = Preprocessor.normalize_phone(raw, "RU")
        except Exception:  # pragma: no cover - защита от чужих ошибок
            normalized = None
        return normalized or raw

    def _visit_probability(self, recency: int | None) -> float:
        """Вероятность визита в день в зависимости от давности клиента."""

        if recency is None:
            return 0.12
        if recency <= 30:
            return 0.30
        if recency <= 60:
            return 0.18
        if recency <= 120:
            return 0.10
        return 0.05

    def _hash_unit(
        self,
        phone_e164: str,
        campaign_id: int,
        day: date,
        role: str,
    ) -> float:
        """Детерминированное равномерное число u ∈ [0, 1) из sha256."""

        token = (
            f"{self._seed}:{phone_e164}:{campaign_id}:"
            f"{day.isoformat()}:{role}"
        )
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest, "big") / float(1 << 256)

    def _visit_id(self, phone_e164: str, campaign_id: int, day: date) -> str:
        """Детерминированный visit_id из sha256."""

        token = (
            f"{self._seed}:{phone_e164}:{campaign_id}:"
            f"{day.isoformat()}:visit_id"
        )
        return "v" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Чтение CSV (ленивая загрузка)
    # ------------------------------------------------------------------

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        """mtime_ns/size файла либо ``None``, если файла нет."""

        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    @staticmethod
    def _detect_encoding(path: Path) -> str:
        """Определяет кодировку файла (utf-8(-sig), utf-16, cp1251)."""

        try:
            head = path.open("rb").read(4096)
        except OSError:
            return "utf-8"
        if head.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
            return "utf-16"
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            return "cp1251"
        return "utf-8"

    @classmethod
    def _append_encoding(cls, path: Path) -> str:
        """Кодировка для дозаписи (без повторного BOM)."""

        encoding = cls._detect_encoding(path)
        return "utf-8" if encoding in {"utf-8", "utf-8-sig"} else encoding

    def _read_rows(
        self,
        path: Path,
        width: int,
        header: list[str],
    ) -> list[list[str]]:
        """Читает CSV, пропуская шапку, пустые и битые строки."""

        if not path.exists():
            return []
        encoding = self._detect_encoding(path)
        try:
            text = path.read_text(encoding=encoding, errors="replace")
        except OSError as exc:
            logger.warning(
                "LoyaltyMock: не удалось прочитать %s: %r", path.name, exc
            )
            return []

        rows: list[list[str]] = []
        for index, row in enumerate(csv.reader(io.StringIO(text))):
            if index == 0 and row and row[0].lstrip("\ufeff").strip() == header[0]:
                continue
            if not row or all(not str(cell).strip() for cell in row):
                continue
            if len(row) != width:
                logger.warning(
                    "LoyaltyMock: %s:%s пропущена (полей %s вместо %s)",
                    path.name,
                    index + 1,
                    len(row),
                    width,
                )
                continue
            rows.append(row)
        return rows

    def _ensure_loaded(self) -> None:
        """Ленивая загрузка/перезагрузка CSV при изменении файлов."""

        signature = (
            self._file_signature(self._tx_path),
            self._file_signature(self._visits_path),
        )
        if self._loaded and signature == self._signature:
            return
        self._reload()
        self._signature = signature
        self._loaded = True

    def _reload(self) -> None:
        """Полностью перечитывает CSV и пересобирает состояние."""

        self._tx = []
        self._visits = []
        self._balances = {}
        self._accounts = OrderedDict()
        self._spent = {}
        self._revoked = {}
        self._windows = []
        self._simulated_days = set()
        self._visit_ids = set()

        for row in self._read_rows(self._tx_path, len(TX_HEADER), TX_HEADER):
            tx = self._parse_transaction(row)
            if tx is None:
                continue
            self._tx.append(tx)
            self._balances[tx.phone_e164] = (
                self._balances.get(tx.phone_e164, 0) + tx.amount
            )
            if tx.campaign_id is None or tx.target_id is None:
                continue
            key = (tx.campaign_id, tx.target_id)
            if tx.op == OP_ACCRUAL:
                if key not in self._accounts:
                    self._accounts[key] = self._account_from_tx(tx)
                else:
                    # Начисление могли отозвать и начислить повторно —
                    # суммы по одному ключу складываются.
                    self._accounts[key].amount += tx.amount
            elif tx.op == OP_REDEMPTION:
                self._spent[key] = self._spent.get(key, 0) + (-tx.amount)
            elif tx.op == OP_REVOKE:
                self._revoked[key] = self._revoked.get(key, 0) + (-tx.amount)

        self._rebuild_windows()

        for row in self._read_rows(
            self._visits_path, len(VISIT_HEADER), VISIT_HEADER
        ):
            visit = self._parse_visit(row)
            if visit is None:
                continue
            self._visits.append(visit)
            self._visit_ids.add(visit.visit_id)
            if visit.ts is not None and visit.campaign_id is not None:
                self._simulated_days.add(
                    (visit.phone_e164, visit.campaign_id, visit.ts.date())
                )

    def _parse_transaction(self, row: list[str]) -> _Transaction | None:
        """Разбирает строку журнала; при мусоре — warning и ``None``."""

        (
            tx_id,
            ts_raw,
            phone_raw,
            op,
            amount_raw,
            campaign_raw,
            target_raw,
            note,
        ) = row
        op = op.strip().upper()
        if op not in KNOWN_OPS:
            logger.warning(
                "LoyaltyMock: transactions.csv: неизвестная операция %r "
                "пропущена",
                op,
            )
            return None
        amount = _parse_int(amount_raw)
        if amount is None:
            logger.warning(
                "LoyaltyMock: transactions.csv: некорректная сумма %r "
                "пропущена",
                amount_raw,
            )
            return None
        # OPENING не относится к кампании — campaign_id/target_id игнорируются
        campaign_id = _parse_int(campaign_raw) if op != OP_OPENING else None
        target_id = _parse_int(target_raw) if op != OP_OPENING else None
        return _Transaction(
            tx_id=tx_id.strip(),
            ts=_coerce_dt(ts_raw.strip()),
            phone_e164=self._normalize_phone(phone_raw),
            op=op,
            amount=amount,
            campaign_id=campaign_id,
            target_id=target_id,
            note=note,
        )

    def _account_from_tx(self, tx: _Transaction) -> _Account:
        """Восстанавливает «кампанийный счёт» из строки ACCRUAL."""

        meta: dict = {}
        if tx.note and tx.note.strip():
            try:
                parsed = json.loads(tx.note)
                if isinstance(parsed, dict):
                    meta = parsed
            except (TypeError, ValueError):
                logger.warning(
                    "LoyaltyMock: transactions.csv: не разобран note для "
                    "campaign=%s target=%s",
                    tx.campaign_id,
                    tx.target_id,
                )
        return _Account(
            phone_e164=tx.phone_e164,
            campaign_id=int(tx.campaign_id),
            target_id=int(tx.target_id),
            amount=tx.amount,
            recency=_parse_int(meta.get("recency")),
            frequency=_parse_int(meta.get("frequency")),
            avg_amount=_parse_float(meta.get("avg_amount")),
            issued_at=_coerce_dt(meta.get("issued_at")),
            ends_at=_coerce_dt(meta.get("ends_at")),
            created_at=tx.ts,
        )

    def _parse_visit(self, row: list[str]) -> _Visit | None:
        """Разбирает строку визита; при мусоре — warning и ``None``."""

        visit_id, ts_raw, phone_raw, amount_raw, spent_raw, campaign_raw = row
        amount = _parse_float(amount_raw)
        bonus_spent = _parse_int(spent_raw)
        if amount is None or bonus_spent is None:
            logger.warning(
                "LoyaltyMock: visits.csv: некорректные числовые поля "
                "пропущены"
            )
            return None
        return _Visit(
            visit_id=visit_id.strip(),
            ts=_coerce_dt(ts_raw.strip()),
            phone_e164=self._normalize_phone(phone_raw),
            amount=amount,
            bonus_spent=bonus_spent,
            campaign_id=_parse_int(campaign_raw),
        )

    def _rebuild_windows(self) -> None:
        """Собирает окна симуляции из восстановленных счетов."""

        self._windows = []
        seen: set[tuple] = set()
        for account in self._accounts.values():
            start = account.issued_at or account.created_at
            if start is None:
                continue
            key = (
                account.phone_e164,
                account.campaign_id,
                start,
                account.ends_at,
            )
            if key in seen:
                continue
            seen.add(key)
            self._windows.append(
                _Window(
                    phone_e164=account.phone_e164,
                    campaign_id=account.campaign_id,
                    start=start,
                    end=account.ends_at,
                    recency=account.recency,
                    avg_amount=account.avg_amount,
                    target_id=account.target_id,
                )
            )

    # ------------------------------------------------------------------
    # Запись CSV
    # ------------------------------------------------------------------

    def _append_row(
        self,
        path: Path,
        header: list[str],
        row: list,
    ) -> None:
        """Дозаписывает строку в CSV c flush + fsync."""

        self._data_dir.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        encoding = self._append_encoding(path) if exists else "utf-8"
        needs_header = not exists or path.stat().st_size == 0
        # Рваная последняя строка без "\n" не должна склеиваться с новой.
        needs_newline = False
        if exists and path.stat().st_size > 0:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                needs_newline = probe.read(1) != b"\n"
        with open(path, "a", encoding=encoding, newline="") as handle:
            if needs_newline:
                handle.write("\n")
            writer = csv.writer(handle)
            if needs_header:
                writer.writerow(header)
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())

    def _write_balances(self) -> None:
        """Атомарно перезаписывает производный ``balances.csv``."""

        self._data_dir.mkdir(parents=True, exist_ok=True)
        updated_at = _iso(self._now_dt())
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=".balances-",
            suffix=".tmp",
            dir=str(self._data_dir),
        )
        try:
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(BALANCE_HEADER)
                for phone in sorted(self._balances):
                    writer.writerow(
                        [phone, self._balances[phone], updated_at]
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._balances_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _refresh_signature(self) -> None:
        """Запоминает подписи файлов после собственной записи."""

        self._signature = (
            self._file_signature(self._tx_path),
            self._file_signature(self._visits_path),
        )
        self._loaded = True

    # ------------------------------------------------------------------
    # Остатки по начислениям
    # ------------------------------------------------------------------

    def _account_remainder(self, account: _Account) -> int:
        """Неизрасходованный остаток конкретного начисления (>= 0)."""

        key = (account.campaign_id, account.target_id)
        spent = self._spent.get(key, 0)
        revoked = self._revoked.get(key, 0)
        return max(0, account.amount - spent - revoked)

    def _group_remainder(self, phone_e164: str, campaign_id: int) -> int:
        """Суммарный неизрасходованный бонус по (телефон, кампания)."""

        total = 0
        for account in self._accounts.values():
            if (
                account.phone_e164 == phone_e164
                and account.campaign_id == campaign_id
            ):
                total += self._account_remainder(account)
        return total

    def _first_target_with_remainder(
        self, phone_e164: str, campaign_id: int
    ) -> _Account | None:
        """Первое начисление (в порядке регистрации) с остатком."""

        for account in self._accounts.values():
            if (
                account.phone_e164 == phone_e164
                and account.campaign_id == campaign_id
                and self._account_remainder(account) > 0
            ):
                return account
        return None

    def _write_campaign_redemption(
        self,
        phone_e164: str,
        campaign_id: int,
        amount: int,
        visit_id: str,
        ts_dt: datetime,
        ts_text: str,
    ) -> int:
        """Списывает часть с кампанийных начислений и пишет строки журнала.

        Возвращает фактически атрибутированную кампании сумму. Каждое
        начисление получает отдельную строку REDEMPTION, поэтому после
        перезагрузки ``_spent`` восстанавливается один в один.
        """

        remaining = int(amount)
        written = 0

        for account in list(self._accounts.values()):
            if remaining <= 0:
                break

            if (
                account.phone_e164 != phone_e164
                or account.campaign_id != campaign_id
            ):
                continue

            part = min(remaining, self._account_remainder(account))

            if part <= 0:
                continue

            key = (account.campaign_id, account.target_id)
            tx_id = f"red-{visit_id}-{account.target_id}"

            self._append_row(
                self._tx_path,
                TX_HEADER,
                [
                    tx_id,
                    ts_text,
                    phone_e164,
                    OP_REDEMPTION,
                    -part,
                    account.campaign_id,
                    account.target_id,
                    f"visit={visit_id}",
                ],
            )
            self._tx.append(
                _Transaction(
                    tx_id=tx_id,
                    ts=ts_dt,
                    phone_e164=phone_e164,
                    op=OP_REDEMPTION,
                    amount=-part,
                    campaign_id=account.campaign_id,
                    target_id=account.target_id,
                    note=f"visit={visit_id}",
                )
            )

            self._spent[key] = self._spent.get(key, 0) + part
            remaining -= part
            written += part

        return written

    # ------------------------------------------------------------------
    # Симуляция
    # ------------------------------------------------------------------

    def _ensure_window(
        self,
        phone_e164: str,
        campaign_id: int,
        issued_at,
        ends_at,
        target_id: int | None,
        recency: int | None = None,
        avg_amount: float | None = None,
    ) -> _Window:
        """Регистрирует окно симуляции для пары (телефон, кампания).

        Визиты эмулируются на пару (телефон, кампания), поэтому при
        нескольких начислениях в одной кампании параметры симуляции
        берутся с первого из них.
        """

        start = _coerce_dt(issued_at)
        end = _coerce_dt(ends_at)
        existing = [
            window
            for window in self._windows
            if window.phone_e164 == phone_e164
            and window.campaign_id == campaign_id
        ]
        if start is None:
            start = existing[0].start if existing else (end or self._now_dt())
        for window in existing:
            if window.start == start and window.end == end:
                return window
        window = _Window(
            phone_e164=phone_e164,
            campaign_id=campaign_id,
            start=start,
            end=end,
            recency=_parse_int(recency),
            avg_amount=_parse_float(avg_amount),
            target_id=target_id,
        )
        self._windows.append(window)
        return window

    def _advance_locked(self, now) -> int:
        """Достраивает симуляцию; вызывается под локом."""

        now_dt = _coerce_dt(now) or self._now_dt()
        new_visits = 0
        for window in list(self._windows):
            if now_dt < window.start:
                continue
            limit = now_dt if window.end is None else min(now_dt, window.end)
            if limit < window.start:
                continue
            day = window.start.date()
            last_day = limit.date()
            while day <= last_day:
                key = (window.phone_e164, window.campaign_id, day)
                if key not in self._simulated_days:
                    if self._simulate_day(window, day):
                        new_visits += 1
                    self._simulated_days.add(key)
                day += _DAY
        if new_visits:
            self._write_balances()
            self._refresh_signature()
        return new_visits

    def _simulate_day(self, window: _Window, day: date) -> bool:
        """Симулирует один день; True — если визит состоялся."""

        phone = window.phone_e164
        campaign_id = window.campaign_id
        u_visit = self._hash_unit(phone, campaign_id, day, "visit")
        if u_visit >= self._visit_probability(window.recency):
            return False

        u_amount = self._hash_unit(phone, campaign_id, day, "amount")
        base = window.avg_amount if window.avg_amount is not None else 300.0
        check = round(base * (0.6 + 0.8 * u_amount), 2)

        visit_id = self._visit_id(phone, campaign_id, day)
        ts_dt = datetime.combine(day, time(VISIT_HOUR, 0))
        ts_text = _iso(ts_dt)

        bonus_spent = 0
        if self._realise_rate > 0:
            u_redeem = self._hash_unit(phone, campaign_id, day, "redeem")
            if u_redeem < self._realise_rate:
                # У клиента ОДИН общий баланс: списываем из него.
                total_balance = int(self._balances.get(phone, 0))
                spendable = int(min(total_balance, check))

                if spendable > 0:
                    # Кампанийная часть: не больше неизрасходованного
                    # остатка именно этой кампании.
                    campaign_part = int(
                        min(spendable, self._group_remainder(phone, campaign_id))
                    )

                    if campaign_part > 0:
                        bonus_spent = self._write_campaign_redemption(
                            phone,
                            campaign_id,
                            campaign_part,
                            visit_id,
                            ts_dt,
                            ts_text,
                        )

                    # Остаток — «старые» бонусы клиента: списываются из
                    # общего баланса, но к кампании не относятся.
                    old_part = spendable - bonus_spent

                    if old_part > 0:
                        self._append_row(
                            self._tx_path,
                            TX_HEADER,
                            [
                                f"red-old-{visit_id}",
                                ts_text,
                                phone,
                                OP_REDEMPTION,
                                -old_part,
                                "",
                                "",
                                f"visit={visit_id}",
                            ],
                        )
                        self._tx.append(
                            _Transaction(
                                tx_id=f"red-old-{visit_id}",
                                ts=ts_dt,
                                phone_e164=phone,
                                op=OP_REDEMPTION,
                                amount=-old_part,
                                campaign_id=None,
                                target_id=None,
                                note=f"visit={visit_id}",
                            )
                        )

                    self._balances[phone] = total_balance - spendable

        if visit_id not in self._visit_ids:
            self._append_row(
                self._visits_path,
                VISIT_HEADER,
                [
                    visit_id,
                    ts_text,
                    phone,
                    check,
                    bonus_spent,
                    campaign_id,
                ],
            )
            self._visits.append(
                _Visit(
                    visit_id=visit_id,
                    ts=ts_dt,
                    phone_e164=phone,
                    amount=check,
                    bonus_spent=bonus_spent,
                    campaign_id=campaign_id,
                )
            )
            self._visit_ids.add(visit_id)
        return True

    # ------------------------------------------------------------------
    # Публичный async-интерфейс
    # ------------------------------------------------------------------

    async def fetch_balance(self, phone_e164: str) -> int | None:
        """Баланс бонусов клиента (0, если телефон неизвестен)."""

        async with self._lock:
            self._ensure_loaded()
            phone = self._normalize_phone(phone_e164)
            if not phone:
                return 0
            return int(self._balances.get(phone, 0))

    async def accrue_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
        recency=None,
        frequency=None,
        avg_amount=None,
        issued_at=None,
        ends_at=None,
    ) -> None:
        """Начисляет бонус и регистрирует кампанийный счёт.

        Идемпотентно по ключу (campaign_id, target_id): повторный вызов
        не создаёт вторую строку ACCRUAL, при отличающейся сумме пишет
        warning и сохраняет первое значение.
        """

        async with self._lock:
            self._ensure_loaded()

            if amount is None:
                return
            requested = _parse_int(amount)
            if requested is None or requested <= 0:
                logger.warning(
                    "LoyaltyMock: начисление пропущено (amount=%r), "
                    "campaign=%s target=%s",
                    amount,
                    campaign_id,
                    target_id,
                )
                return

            key = (int(campaign_id), int(target_id))
            existing = self._accounts.get(key)

            if existing is not None:
                fully_revoked = (
                    self._revoked.get(key, 0) >= existing.amount
                )

                if not fully_revoked:
                    if existing.amount != requested:
                        logger.warning(
                            "LoyaltyMock: повторное начисление с другой суммой "
                            "(%s вместо %s), campaign=%s target=%s — сохраняю "
                            "первое значение",
                            requested,
                            existing.amount,
                            key[0],
                            key[1],
                        )
                    else:
                        logger.warning(
                            "LoyaltyMock: повторное начисление, campaign=%s "
                            "target=%s — пропуск",
                            key[0],
                            key[1],
                        )
                    return

                # Бонус был полностью отозван (например, бот откатил
                # начисление из-за недоставленного сообщения) — начисляем
                # повторно по тому же ключу.
                logger.info(
                    "LoyaltyMock: повторное начисление после полного отзыва, "
                    "campaign=%s target=%s",
                    key[0],
                    key[1],
                )

            phone = self._normalize_phone(phone_e164)
            now_dt = self._now_dt()
            account = _Account(
                phone_e164=phone,
                campaign_id=key[0],
                target_id=key[1],
                amount=requested,
                recency=_parse_int(recency),
                frequency=_parse_int(frequency),
                avg_amount=_parse_float(avg_amount),
                issued_at=_coerce_dt(issued_at),
                ends_at=_coerce_dt(ends_at),
                created_at=now_dt,
            )
            note = json.dumps(
                {
                    "recency": account.recency,
                    "frequency": account.frequency,
                    "avg_amount": account.avg_amount,
                    "issued_at": _iso(account.issued_at),
                    "ends_at": _iso(account.ends_at),
                },
                ensure_ascii=False,
            )
            accrual_count = sum(
                1
                for tx in self._tx
                if tx.op == OP_ACCRUAL
                and tx.campaign_id == key[0]
                and tx.target_id == key[1]
            )
            tx_id = f"acc-{key[0]}-{key[1]}"
            if accrual_count:
                tx_id = f"{tx_id}-r{accrual_count}"
            self._append_row(
                self._tx_path,
                TX_HEADER,
                [
                    tx_id,
                    _iso(now_dt),
                    phone,
                    OP_ACCRUAL,
                    requested,
                    key[0],
                    key[1],
                    note,
                ],
            )
            if existing is not None:
                existing.amount += requested
                existing.recency = account.recency
                existing.frequency = account.frequency
                existing.avg_amount = account.avg_amount
                existing.issued_at = account.issued_at or existing.issued_at
                existing.ends_at = account.ends_at or existing.ends_at
            else:
                self._accounts[key] = account
            self._tx.append(
                _Transaction(
                    tx_id=tx_id,
                    ts=now_dt,
                    phone_e164=phone,
                    op=OP_ACCRUAL,
                    amount=requested,
                    campaign_id=key[0],
                    target_id=key[1],
                    note=note,
                )
            )
            self._balances[phone] = (
                self._balances.get(phone, 0) + requested
            )
            start = account.issued_at or account.created_at
            if start is not None:
                self._ensure_window(
                    phone,
                    account.campaign_id,
                    start,
                    account.ends_at,
                    account.target_id,
                    account.recency,
                    account.avg_amount,
                )
            self._write_balances()
            self._refresh_signature()
            logger.info(
                "LoyaltyMock: начислено %s бонусов phone=%s campaign=%s "
                "target=%s",
                requested,
                _mask_phone(phone),
                key[0],
                key[1],
            )

    async def revoke_bonus(
        self,
        *,
        phone_e164: str,
        amount: int,
        campaign_id: int,
        target_id: int,
    ) -> int:
        """Отзывает нереализованный бонус.

        Идемпотентно по ключу (campaign_id, target_id). Пишет строку
        только если по этой паре есть ACCRUAL; отзываемая сумма —
        ``min(запрошенная, неизрасходованный остаток)``.

        Возвращает ФАКТИЧЕСКИ отозванную сумму (``0``, если отзывать
        было нечего: повтор, отсутствие начисления или нулевой
        остаток). Бот использует это значение в отчёте.
        """

        async with self._lock:
            self._ensure_loaded()

            key = (int(campaign_id), int(target_id))

            requested = _parse_int(amount) or 0
            if requested <= 0:
                logger.warning(
                    "LoyaltyMock: отзыв пропущен (amount=%r), campaign=%s "
                    "target=%s",
                    amount,
                    key[0],
                    key[1],
                )
                return 0

            account = self._accounts.get(key)
            if account is None:
                logger.warning(
                    "LoyaltyMock: отзыв без начисления, campaign=%s "
                    "target=%s — пропуск",
                    key[0],
                    key[1],
                )
                return 0

            remainder = self._group_remainder(account.phone_e164, key[0])
            total_balance = int(self._balances.get(account.phone_e164, 0))
            revocable = max(0, min(requested, remainder, total_balance))

            if revocable <= 0:
                already = self._revoked.get(key, 0)

                if already > 0:
                    # Повтор (в т.ч. после отката транзакции бота):
                    # сообщаем уже отозванную сумму, чтобы учёт в отчёте
                    # не занижался.
                    logger.warning(
                        "LoyaltyMock: повторный отзыв, campaign=%s target=%s — "
                        "уже отозвано %s",
                        key[0],
                        key[1],
                        already,
                    )
                    return already

                logger.warning(
                    "LoyaltyMock: отзывать нечего, campaign=%s target=%s — "
                    "пропуск",
                    key[0],
                    key[1],
                )
                return 0

            now_dt = self._now_dt()
            tx_id = f"rev-{key[0]}-{key[1]}"
            self._append_row(
                self._tx_path,
                TX_HEADER,
                [
                    tx_id,
                    _iso(now_dt),
                    account.phone_e164,
                    OP_REVOKE,
                    -revocable,
                    key[0],
                    key[1],
                    "revoke",
                ],
            )
            self._tx.append(
                _Transaction(
                    tx_id=tx_id,
                    ts=now_dt,
                    phone_e164=account.phone_e164,
                    op=OP_REVOKE,
                    amount=-revocable,
                    campaign_id=key[0],
                    target_id=key[1],
                    note="revoke",
                )
            )
            self._balances[account.phone_e164] = (
                self._balances.get(account.phone_e164, 0) - revocable
            )
            self._revoked[key] = self._revoked.get(key, 0) + revocable
            self._write_balances()
            self._refresh_signature()
            logger.info(
                "LoyaltyMock: отозвано %s из %s бонусов phone=%s "
                "campaign=%s target=%s",
                revocable,
                requested,
                _mask_phone(account.phone_e164),
                key[0],
                key[1],
            )

            return revocable

    async def fetch_realised(
        self,
        *,
        campaign_id: int,
        target_id: int,
        phone_e164: str,
        bonus_amount: int,
        issued_at,
        ends_at,
    ) -> int | None:
        """«Клиент вернулся» = сумма списаний бонуса за период кампании.

        Возвращает ``None``, если за окно нет ни одного визита клиента,
        ``0`` — если визиты есть, но списаний не было, и сумму списаний
        (не больше ``bonus_amount``) иначе.
        """

        async with self._lock:
            self._ensure_loaded()
            phone = self._normalize_phone(phone_e164)
            campaign = int(campaign_id)
            # Окно расширяем только для существующего начисления:
            # для неизвестного телефона визиты не выдумываем.
            accounts = [
                account
                for account in self._accounts.values()
                if account.phone_e164 == phone
                and account.campaign_id == campaign
            ]
            if accounts:
                self._ensure_window(
                    phone,
                    campaign,
                    issued_at,
                    ends_at,
                    int(target_id) if target_id is not None else None,
                    accounts[0].recency,
                    accounts[0].avg_amount,
                )
            self._advance_locked(self._now_dt())

            start = _coerce_dt(issued_at)
            end = _coerce_dt(ends_at)
            matched = [
                visit
                for visit in self._visits
                if visit.phone_e164 == phone
                and visit.campaign_id == campaign
                and (start is None or (visit.ts is not None and visit.ts >= start))
                and (end is None or (visit.ts is not None and visit.ts <= end))
            ]
            if not matched:
                return None
            total = sum(int(visit.bonus_spent) for visit in matched)
            cap = _parse_int(bonus_amount)
            if cap is not None and cap > 0:
                total = min(total, cap)
            return int(total)

    async def advance(self, now=None) -> int:
        """Достраивает симуляцию до ``min(now, ends_at)``.

        Возвращает число новых визитов. Повторный вызов с тем же
        ``now`` не создаёт дубликатов.
        """

        async with self._lock:
            self._ensure_loaded()
            return self._advance_locked(now)

    # ------------------------------------------------------------------
    # Диагностика (необязательный метод, bot.py его не вызывает)
    # ------------------------------------------------------------------

    def snapshot(self) -> list[dict]:
        """Текущее состояние кампанийных счетов (телефоны маскированы)."""

        self._ensure_loaded()
        result: list[dict] = []
        for account in self._accounts.values():
            key = (account.campaign_id, account.target_id)
            result.append(
                {
                    "phone_masked": _mask_phone(account.phone_e164),
                    "balance": self._balances.get(account.phone_e164, 0),
                    "campaign_id": account.campaign_id,
                    "target_id": account.target_id,
                    "accrued": account.amount,
                    "spent": self._spent.get(key, 0),
                    "revoked": self._revoked.get(key, 0),
                    "remaining": self._account_remainder(account),
                    "recency": account.recency,
                    "frequency": account.frequency,
                    "avg_amount": account.avg_amount,
                    "issued_at": _iso(account.issued_at),
                    "ends_at": _iso(account.ends_at),
                }
            )
        return result
