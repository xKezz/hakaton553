#!/bin/bash

# ==========================================
# Скрипт для запуска тестов проекта Win-Back MAX MVP.
# Redis и отдельный worker в архитектуре MVP не используются.
# ==========================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
error() { echo -e "${RED}❌ Ошибка: $1${NC}"; exit 1; }

# 1. Проверка виртуального окружения
if [ ! -d "venv" ]; then
    error "Виртуальное окружение 'venv' не найдено."
fi

info "Активирую виртуальное окружение..."
source venv/bin/activate

# 2. Запуск pytest.
# Параметры (testpaths, asyncio_mode=auto) заданы в pyproject.toml.
echo ""
echo -e "${YELLOW}🚀 Запускаю тесты...${NC}"
echo "----------------------------------------"

PYTHONPATH=. python -m pytest tests/ -q

echo "----------------------------------------"
success "Тестирование завершено!"
