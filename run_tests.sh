#!/bin/bash

# ==========================================
# Скрипт для запуска тестов проекта Win-Back
# ==========================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
error() { echo -e "\n${RED}❌ Ошибка: $1${NC}"; exit 1; }

# 1. Проверка виртуального окружения
if [ ! -d "venv" ]; then
    error "Виртуальное окружение 'venv' не найдено. Сначала выполни ./setup_db.sh"
fi

info "Активирую виртуальное окружение..."
source venv/bin/activate

# 2. Проверка зависимостей для тестов
info "Проверяю тестовые зависимости..."
pip install --quiet pytest pytest-asyncio httpx pytest-mock pytest-cov

# 3. Проверка доступности БД и Redis
if ! redis-cli ping > /dev/null 2>&1; then
    error "Redis не отвечает. Запусти его: sudo service redis-server start"
fi

if ! python -c "from src.DB.database import engine; import asyncio; asyncio.run(engine.connect())" > /dev/null 2>&1; then
    warning "Не удалось подключиться к БД. Убедись, что PostgreSQL запущен (sudo service postgresql start)"
fi

# 4. Создание папки fixtures для тестов (если генератор CSV её использует)
mkdir -p tests/fixtures

# 5. Запуск pytest
echo ""
echo -e "${YELLOW}🚀 Запускаю тесты...${NC}"
echo "----------------------------------------"

# --asyncio-mode=auto критически важен для современных версий pytest-asyncio
# -v: подробный вывод
# -s: показывать print() и логи
# --cov=src: (опционально) показать покрытие кода
PYTHONPATH=. pytest tests/ -v -s --asyncio-mode=auto --cov=src --cov-report=term-missing

echo "----------------------------------------"
success "Тестирование завершено!"