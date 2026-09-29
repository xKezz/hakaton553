#!/bin/bash
# ==========================================================
# Win-Back MAX MVP — полное развёртывание проекта.
#
# Что делает скрипт:
#   1. определяет среду (systemd / WSL / контейнер) и дистрибутив;
#   2. проверяет и при необходимости устанавливает PostgreSQL;
#   3. запускает PostgreSQL;
#   4. создаёт роль postgres/postgres и базу winback_db;
#   5. применяет схему create_db.sql (5 таблиц);
#   6. создаёт venv и ставит зависимости из requirements.txt;
#   7. готовит .env (из .env.example) и каталоги данных;
#   8. проверяет импорт бота и подключение к БД.
#
# Redis, FastAPI, Mini App, Alembic и отдельный worker в текущей
# архитектуре MVP не используются.
#
# Запуск:  ./setup.sh
# Для установки PostgreSQL и работы с ролью postgres нужен sudo.
# ==========================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error()   { echo -e "${RED}❌ $1${NC}"; exit 1; }

# ==========================================================
# 0. КОНФИГУРАЦИЯ
# ==========================================================
# Реквизиты ДОЛЖНЫ совпадать со строкой подключения в
# src/DB/database.py: postgres:postgres@localhost:5432/winback_db
DB_NAME="winback_db"
DB_USER="postgres"
DB_PASSWORD="postgres"
DB_HOST="localhost"
DB_PORT="5432"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

info "Каталог проекта: $PROJECT_ROOT"

# ==========================================================
# 1. ОПРЕДЕЛЕНИЕ СРЕДЫ
# ==========================================================
info "Определяю среду выполнения..."
HAS_SYSTEMD=false
if [ -d /run/systemd/system ]; then
    HAS_SYSTEMD=true
    info "Обнаружен systemd (полноценный Linux)"
else
    warning "systemd не обнаружен (WSL или контейнер)"
fi

if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
    info "Дистрибутив: $DISTRO"
else
    error "Не удалось определить дистрибутив Linux"
fi

# ==========================================================
# 2. УСТАНОВКА POSTGRESQL
# ==========================================================
if command -v psql &> /dev/null; then
    success "PostgreSQL уже установлен ($(psql --version | head -n1))"
else
    info "Устанавливаю PostgreSQL..."
    case $DISTRO in
    ubuntu|debian)
        sudo apt-get update -qq
        sudo apt-get install -y -qq postgresql postgresql-contrib > /dev/null
        ;;
    centos|rhel|fedora)
        sudo yum install -y -q postgresql-server postgresql-contrib
        if [ "$HAS_SYSTEMD" = true ]; then
            sudo postgresql-setup initdb
        else
            sudo -u postgres /usr/bin/initdb -D /var/lib/pgsql/data
        fi
        ;;
    *)
        error "Дистрибутив $DISTRO не поддерживается. Установи PostgreSQL вручную."
        ;;
    esac
    success "PostgreSQL установлен"
fi

# ==========================================================
# 3. ЗАПУСК POSTGRESQL
# ==========================================================
info "Запускаю PostgreSQL..."
if pg_isready -q 2> /dev/null; then
    success "PostgreSQL уже запущен и принимает подключения"
else
    if [ "$HAS_SYSTEMD" = true ]; then
        sudo systemctl start postgresql
        sudo systemctl enable postgresql
    else
        case $DISTRO in
        ubuntu|debian)
            PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | head -n1)
            if [ -z "$PG_VERSION" ]; then
                error "Не удалось определить версию PostgreSQL"
            fi
            info "Версия PostgreSQL: $PG_VERSION"
            if command -v service &> /dev/null; then
                sudo service postgresql start
            else
                sudo pg_ctlcluster "$PG_VERSION" main start
            fi
            ;;
        centos|rhel|fedora)
            sudo -u postgres /usr/bin/pg_ctl -D /var/lib/pgsql/data start
            ;;
        esac
    fi
    sleep 2
    if pg_isready -q 2> /dev/null; then
        success "PostgreSQL запущен"
    else
        error "Не удалось запустить PostgreSQL"
    fi
fi

if ! sudo -n true 2> /dev/null; then
    warning "sudo без пароля недоступен — шаги с ролью и базой могут запросить пароль."
fi

# ==========================================================
# 4. РОЛЬ И БАЗА ДАННЫХ
# ==========================================================
info "Настраиваю роль $DB_USER и базу $DB_NAME..."

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" 2>/dev/null | grep -q 1; then
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" > /dev/null
    success "Роль $DB_USER уже существует, пароль обновлён"
else
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" > /dev/null
    success "Роль $DB_USER создана"
fi

if sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    warning "База $DB_NAME уже существует"
else
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" > /dev/null
    success "База $DB_NAME создана"
fi

# ==========================================================
# 5. СХЕМА (create_db.sql)
# ==========================================================
info "Применяю схему из create_db.sql..."
if [ ! -f "create_db.sql" ]; then
    error "Не найден create_db.sql в $PROJECT_ROOT"
fi

TABLES_COUNT=$(sudo -u postgres psql -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" \
    2>/dev/null | tr -d '[:space:]')

if ! [[ "$TABLES_COUNT" =~ ^[0-9]+$ ]]; then
    error "Не удалось проверить схему БД (ответ: '$TABLES_COUNT')"
fi

if [ "$TABLES_COUNT" -gt 0 ]; then
    warning "В базе уже есть таблицы ($TABLES_COUNT шт.) — схема не применяется повторно"
else
    sudo -u postgres psql -d "$DB_NAME" -f create_db.sql > /dev/null
    success "Схема применена (client, purchase, campaign, campaign_category, campaign_target)"
fi

# ==========================================================
# 6. PYTHON, VENV И ЗАВИСИМОСТИ
# ==========================================================
info "Настраиваю Python..."
if ! command -v python3 &> /dev/null; then
    error "Python 3 не установлен"
fi
info "Версия Python: $(python3 --version 2>&1 | awk '{print $2}')"

if [ ! -d "venv" ]; then
    info "Создаю виртуальное окружение..."
    python3 -m venv venv
    success "Виртуальное окружение создано"
else
    warning "Виртуальное окружение уже существует"
fi

if [ ! -f "requirements.txt" ]; then
    error "Не найден requirements.txt в $PROJECT_ROOT"
fi

info "Устанавливаю зависимости из requirements.txt..."
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet --requirement requirements.txt
success "Зависимости установлены"

# ==========================================================
# 7. .env И КАТАЛОГИ ДАННЫХ
# ==========================================================
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        success "Создан .env из .env.example"
        warning "Заполните в .env переменные MAX_BOT_TOKEN и ADMIN_IDS"
    else
        warning ".env.example не найден — создайте .env вручную"
    fi
else
    warning ".env уже существует — не перезаписываю"
fi

info "Готовлю каталоги данных..."
mkdir -p data/source_files data/loyalty_mock
success "Каталоги data/source_files и data/loyalty_mock готовы"

# ==========================================================
# 8. ПРОВЕРКИ
# ==========================================================
info "Проверяю импорт бота..."
if PYTHONPATH="$PROJECT_ROOT" venv/bin/python -c "import src.Bot.bot" > /dev/null 2>&1; then
    success "src.Bot.bot импортируется без ошибок"
else
    error "Модуль src.Bot.bot не импортируется — смотри вывод: PYTHONPATH=. venv/bin/python -c 'import src.Bot.bot'"
fi

info "Проверяю подключение к БД и состав таблиц..."
DB_NAME="$DB_NAME" DB_USER="$DB_USER" DB_PASSWORD="$DB_PASSWORD" \
DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" venv/bin/python - <<'PY'
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

url = (
    f"postgresql+asyncpg://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
)

EXPECTED = [
    "client",
    "purchase",
    "campaign",
    "campaign_category",
    "campaign_target",
]


async def check() -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            tables = [row[0] for row in result]
    except Exception as exc:  # noqa: BLE001 - нужен понятный вывод
        print(f"    ❌ Не удалось подключиться к {url}: {exc!r}")
        return False
    finally:
        await engine.dispose()

    print(f"    Таблиц в схеме public: {len(tables)} -> {', '.join(tables)}")

    missing = [name for name in EXPECTED if name not in tables]
    if missing:
        print(f"    ❌ Отсутствуют таблицы: {', '.join(missing)}")
        return False

    print("    ✅ Все ожидаемые таблицы на месте")
    return True


sys.exit(0 if asyncio.run(check()) else 1)
PY

success "База данных готова"

# ==========================================================
# 9. ИТОГ
# ==========================================================
TOTAL_STEPS="готово"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   ПРОЕКТ РАЗВЁРНУТ                     ${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}📋 PostgreSQL:${NC}"
echo "   Host:     $DB_HOST"
echo "   Port:     $DB_PORT"
echo "   Database: $DB_NAME"
echo "   User:     $DB_USER"
echo "   DSN:      postgresql+asyncpg://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
echo "   (эти же реквизиты зашиты в src/DB/database.py)"
echo ""
echo -e "${BLUE}📦 Зависимости:${NC} requirements.txt (зафиксированные версии)"
echo ""
echo -e "${YELLOW}🚀 Дальнейшие шаги:${NC}"
echo "   1. Заполнить .env:      MAX_BOT_TOKEN и ADMIN_IDS"
echo "   2. Запустить бота:      venv/bin/python -m src.Bot.bot"
echo "   3. Запустить тесты:     ./run_tests.sh"
echo "   4. Docker-вариант:      cp .env.example .env && docker compose up -d"
echo ""
warning "Порт $DB_PORT занят локальным PostgreSQL — перед 'docker compose up'"
warning "остановите его: sudo service postgresql stop"
echo ""

if [ "$HAS_SYSTEMD" = false ]; then
    echo -e "${YELLOW}⚠️  Важно для WSL:${NC}"
    echo "   Сервисы не стартуют автоматически при запуске WSL."
    echo "   Перед работой: sudo service postgresql start"
    echo ""
fi

echo -e "${GREEN}✅ Развёртывание завершено ($TOTAL_STEPS)${NC}"
