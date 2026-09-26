#!/bin/bash
# ==========================================
# Универсальный скрипт развертывания БД и Redis
# Для проекта Win-Back MAX MVP
# Работает в WSL, Docker и полноценном Linux
# ==========================================
set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Конфигурация БД
DB_NAME="winback_db"
DB_USER="admin"
DB_PASSWORD="admin123"
DB_HOST="localhost"
DB_PORT="5432"

# Функции вывода
info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error()   { echo -e "${RED}❌ $1${NC}"; exit 1; }

# ==========================================
# 1. ОПРЕДЕЛЕНИЕ СРЕДЫ
# ==========================================
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

# ==========================================
# 2. УСТАНОВКА POSTGRESQL
# ==========================================
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

# ==========================================
# 3. ЗАПУСК POSTGRESQL (АДАПТИВНО)
# ==========================================
info "Запускаю PostgreSQL..."
if pgrep -x "postgres" > /dev/null; then
    success "PostgreSQL уже запущен"
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
    if pgrep -x "postgres" > /dev/null; then
        success "PostgreSQL запущен"
    else
        error "Не удалось запустить PostgreSQL"
    fi
fi

# ==========================================
# 4. УСТАНОВКА И ЗАПУСК REDIS (НОВОЕ)
# ==========================================
info "Устанавливаю и настраиваю Redis..."
if command -v redis-server &> /dev/null; then
    success "Redis уже установлен ($(redis-server --version))"
else
    info "Устанавливаю Redis..."
    case $DISTRO in
    ubuntu|debian)
        sudo apt-get update -qq
        sudo apt-get install -y -qq redis-server > /dev/null
        ;;
    centos|rhel|fedora)
        sudo yum install -y -q epel-release
        sudo yum install -y -q redis
        ;;
    *)
        error "Дистрибутив $DISTRO не поддерживается для автоустановки Redis. Установи Redis вручную."
        ;;
    esac
    success "Redis установлен"
fi

# Запуск Redis
info "Запускаю Redis..."
if redis-cli ping &> /dev/null; then
    success "Redis уже запущен и отвечает"
else
    if [ "$HAS_SYSTEMD" = true ]; then
        # В Ubuntu/Debian сервис называется redis-server, в CentOS/RHEL - redis
        if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
            sudo systemctl start redis-server
            sudo systemctl enable redis-server
        else
            sudo systemctl start redis
            sudo systemctl enable redis
        fi
    else
        case $DISTRO in
        ubuntu|debian)
            if command -v service &> /dev/null; then
                sudo service redis-server start
            else
                # Запуск в фоне для WSL/контейнеров без service
                sudo redis-server --daemonize yes
            fi
            ;;
        centos|rhel|fedora)
            sudo redis-server --daemonize yes
            ;;
        esac
    fi
    sleep 2
    if redis-cli ping &> /dev/null; then
        success "Redis запущен"
    else
        error "Не удалось запустить Redis"
    fi
fi

# ==========================================
# 5. СОЗДАНИЕ ПОЛЬЗОВАТЕЛЯ И БД
# ==========================================
info "Создаю пользователя и базу данных..."
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" 2>/dev/null | grep -q 1; then
    warning "Пользователь $DB_USER уже существует"
else
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" > /dev/null
    success "Пользователь $DB_USER создан"
fi

if sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    warning "База данных $DB_NAME уже существует"
else
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" > /dev/null
    success "База данных $DB_NAME создана"
fi

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" > /dev/null
success "Права назначены"

# ==========================================
# 6. PYTHON И ВИРТУАЛЬНОЕ ОКРУЖЕНИЕ
# ==========================================
info "Настраиваю Python..."
if ! command -v python3 &> /dev/null; then
    error "Python 3 не установлен"
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
info "Версия Python: $PYTHON_VERSION"

# Создаём venv, если нет
if [ ! -d "venv" ]; then
    info "Создаю виртуальное окружение..."
    python3 -m venv venv
    success "Виртуальное окружение создано"
fi

# Активируем
# shellcheck disable=SC1091
source venv/bin/activate

# Обновляем pip
pip install --upgrade pip --quiet

# ==========================================
# 7. УСТАНОВКА ВСЕХ ЗАВИСИМОСТЕЙ
# ==========================================
info "Устанавливаю Python-зависимости..."
# КЛЮЧЕВОЕ: greenlet нужен для SQLAlchemy asyncio!
pip install --quiet \
"sqlalchemy[asyncio]" \
greenlet \
asyncpg \
psycopg2-binary \
alembic \
fastapi \
uvicorn \
arq \
redis \
pandas \
numpy \
python-multipart \
httpx \
phonenumbers \
pydantic
success "Все зависимости установлены"

# ==========================================
# 8. НАСТРОЙКА ALEMBIC
# ==========================================
info "Настраиваю Alembic..."
if [ ! -d "alembic" ]; then
    python -m alembic init alembic
    success "Alembic инициализирован"
else
    warning "Папка alembic уже существует"
fi

# Настраиваем alembic.ini
info "Настраиваю alembic.ini..."
sed -i "s|sqlalchemy.url = .*|sqlalchemy.url = postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME|" alembic.ini
success "alembic.ini настроен"

# ==========================================
# 9. НАСТРОЙКА alembic/env.py
# ==========================================
info "Настраиваю alembic/env.py..."
# ВАЖНО: импорт Base из src.DB.models (без вложенной db!)
# Это соответствует твоей реальной структуре проекта
cat > alembic/env.py << 'EOF'
import sys
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context

# Добавляем корень проекта в пути Python
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Импортируем Base из моделей (структура: src/DB/models.py)
from src.DB.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
EOF
success "alembic/env.py настроен"

# ==========================================
# 10. ПРИМЕНЕНИЕ МИГРАЦИЙ
# ==========================================
info "Применяю миграции..."
# Чистим старые миграции
if [ -d "alembic/versions" ]; then
    rm -f alembic/versions/*.py
    warning "Старые миграции удалены"
fi

# Удаляем служебную таблицу Alembic
psql "postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" \
-c "DROP TABLE IF EXISTS alembic_version;" > /dev/null 2>&1 || true

# Генерируем миграцию
python -m alembic revision --autogenerate -m "initial tables"
success "Миграция создана"

# Применяем
python -m alembic upgrade head
success "Миграция применена"

# ==========================================
# 11. ПРОВЕРКА РЕЗУЛЬТАТА
# ==========================================
info "Проверяю результат..."
cat > /tmp/check_db.py << EOF
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def check():
    engine = create_async_engine(
        "postgresql+asyncpg://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
    )
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """))
        tables = [row[0] for row in result]
        
        print("\n✅ Подключение к базе успешно!")
        print(f"📂 Найдено таблиц в схеме 'public': {len(tables)}")
        for t in tables:
            print(f"   - {t}")
            
        expected = ['alembic_version', 'bonus_transactions',
                    'business_admins', 'campaign_categories', 'campaigns']
                    
        if all(t in tables for t in expected):
            print("\n🎉 Все таблицы созданы успешно!")
            return True
        else:
            missing = [t for t in expected if t not in tables]
            print(f"\n⚠️  Отсутствуют таблицы: {missing}")
            return False

if __name__ == "__main__":
    success = asyncio.run(check())
    exit(0 if success else 1)
EOF

python /tmp/check_db.py
CHECK_RESULT=$?
rm -f /tmp/check_db.py

if [ $CHECK_RESULT -ne 0 ]; then
    error "Проверка БД не пройдена"
fi

# ==========================================
# 12. ФИНАЛЬНЫЕ ИНСТРУКЦИИ
# ==========================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   БАЗА ДАННЫХ И REDIS РАЗВЕРНУТЫ!      ${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}📋 Параметры подключения:${NC}"
echo "   [PostgreSQL]"
echo "   Host:     $DB_HOST"
echo "   Port:     $DB_PORT"
echo "   Database: $DB_NAME"
echo "   User:     $DB_USER"
echo "   Password: $DB_PASSWORD"
echo ""
echo "   [Redis]"
echo "   Host:     127.0.0.1"
echo "   Port:     6379"
echo "   Password: (не установлена по умолчанию)"
echo ""
echo -e "${BLUE}🔗 Строки подключения:${NC}"
echo "   FastAPI (async): postgresql+asyncpg://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
echo "   Alembic (sync):  postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
echo "   Redis:           redis://127.0.0.1:6379/0"
echo ""
echo -e "${YELLOW}🚀 Следующие шаги:${NC}"
echo "   1. Активируй venv:   source venv/bin/activate"
echo "   2. Запусти API:      python -m uvicorn src.API.api:app --reload"
echo "   3. Запусти Worker:   python -m arq src.worker.WorkerSettings"
echo ""

if [ "$HAS_SYSTEMD" = false ]; then
    echo -e "${YELLOW}⚠️  Важно для WSL:${NC}"
    echo "   Сервисы не запускаются автоматически при старте WSL."
    echo "   Перед работой выполни:"
    echo "   sudo service postgresql start"
    echo "   sudo service redis-server start"
    echo ""
fi

echo -e "${GREEN}✅ Готово к работе!${NC}"