#!/bin/bash

# Yadreno VPN — скрипт установки и управления
# Запуск: bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh)
# 
# === АВТОМАТИЧЕСКИЙ ЗАПУСК (БЕЗ ДИАЛОГОВ) ===
#
# 1. Запуск прямо с GitHub (для чистой установки или если папки ещё нет):
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) install <BOT_TOKEN> <ADMIN_ID>
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) update [COMMIT_OR_BRANCH]
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) reset [COMMIT_OR_BRANCH]
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) rollback
#
# 2. Локальный запуск (если репозиторий уже установлен и нужно просто обновить/сбросить):
# bash install.sh update [COMMIT_OR_BRANCH]
# bash install.sh reset [COMMIT_OR_BRANCH]
# bash install.sh rollback

set -e

INSTALL_DIR="/root/YadrenoVPN"
REPO_URL="https://github.com/plushkinv/YadrenoVPN.git"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_FILE="yadreno-vpn.service"
DB_PATH="$INSTALL_DIR/database/vpn_bot.db"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "\n${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}\n"
}

print_ok() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_err() {
    echo -e "${RED}[✗]${NC} $1"
}

# Создание обязательной точки отката перед изменением Git-версии
prepare_update_snapshot() {
    local update_mode="$1"
    local requested_target="$2"
    local python_bin="$VENV_DIR/bin/python"

    if [ ! -x "$python_bin" ]; then
        print_err "Python из виртуального окружения не найден: $python_bin"
        return 1
    fi

    local output
    if ! output=$(
        cd "$INSTALL_DIR" &&
        "$python_bin" -m bot.services.update_rollback prepare \
            --project-root "$INSTALL_DIR" \
            --mode "$update_mode" \
            --requested-target "$requested_target" \
            --actor "installer"
    ); then
        print_err "Не удалось создать и проверить backup базы данных. Обновление отменено."
        return 1
    fi

    UPDATE_SNAPSHOT_ID=$(echo "$output" | tail -n 1 | tr -d '\r')
    if [ -z "$UPDATE_SNAPSHOT_ID" ]; then
        print_err "Исполнитель backup не вернул идентификатор точки отката"
        return 1
    fi
    print_ok "Создан pre-update backup: $UPDATE_SNAPSHOT_ID"
}

# Фиксация целевого коммита после успешного изменения Git-версии
mark_update_snapshot_applied() {
    local python_bin="$VENV_DIR/bin/python"
    local runner="$INSTALL_DIR/backup/pre_update/$UPDATE_SNAPSHOT_ID/rollback_runner.py"

    if [ -z "$UPDATE_SNAPSHOT_ID" ] || [ ! -f "$runner" ]; then
        print_err "Не найден исполнитель созданной точки отката"
        return 1
    fi
    "$python_bin" "$runner" mark-applied \
        --project-root "$INSTALL_DIR" \
        --snapshot-id "$UPDATE_SNAPSHOT_ID" \
        > /dev/null
    print_ok "Точка отката привязана к установленному коммиту"
}

# Общая блокировка не допускает одновременный update и rollback
acquire_update_operation_lock() {
    if ! command -v flock > /dev/null 2>&1; then
        print_err "Команда flock не найдена; безопасное обновление невозможно"
        return 1
    fi
    mkdir -p "$INSTALL_DIR/backup/pre_update"
    exec 9> "$INSTALL_DIR/backup/pre_update/.operation.lock"
    if ! flock -n 9; then
        print_err "Уже выполняется другое обновление или откат"
        exec 9>&-
        return 1
    fi
}

release_update_operation_lock() {
    flock -u 9 2>/dev/null || true
    exec 9>&-
}

# Запрос настроек у пользователя
ask_config() {
    print_header "Настройка конфигурации"

    if [ "$AUTO_MODE" = "1" ]; then
        NEED_WRITE_CONFIG=1
        print_ok "Автоматический режим: используем переданные параметры"
        return 0
    fi

    if [ -f "$INSTALL_DIR/config.py" ]; then
        echo -e "${YELLOW}Обнаружен существующий config.py${NC}"
        read -p "Использовать существующие настройки? (Y/n): " use_existing
        use_existing=${use_existing:-Y}
        if [[ "$use_existing" =~ ^[YyДд]$ ]]; then
            print_ok "Используем существующий config.py"
            return 0
        fi
    fi

    echo ""
    echo -e "${CYAN}Введите данные для настройки бота:${NC}"
    echo ""

    while true; do
        read -p "BOT_TOKEN (от @BotFather): " bot_token
        if [ -n "$bot_token" ]; then
            break
        fi
        print_err "BOT_TOKEN не может быть пустым!"
    done

    while true; do
        read -p "ADMIN_IDS (ваш Telegram ID): " admin_id
        if [ -n "$admin_id" ] && [[ "$admin_id" =~ ^[0-9]+$ ]]; then
            break
        fi
        print_err "ADMIN_IDS должен быть числом!"
    done

    BOT_TOKEN="$bot_token"
    ADMIN_ID="$admin_id"
    NEED_WRITE_CONFIG=1
    print_ok "Данные получены"
}

# Создание/обновление config.py
write_config() {
    if [ "$NEED_WRITE_CONFIG" != "1" ]; then
        return 0
    fi

    cp "$INSTALL_DIR/config.py.example" "$INSTALL_DIR/config.py"

    sed -i "s|\"ВАШ_ТОКЕН_БОТА\"|\"$BOT_TOKEN\"|g" "$INSTALL_DIR/config.py"
    sed -i "s|12345678|$ADMIN_ID|g" "$INSTALL_DIR/config.py"

    print_ok "config.py создан с вашими настройками"
}

# Установка системных пакетов
install_system_deps() {
    print_header "Установка системных зависимостей"

    export DEBIAN_FRONTEND=noninteractive
    export NEEDRESTART_MODE=a

    apt-get update -qq
    apt-get install -y -qq \
        python3-venv \
        python3-pip \
        git \
        > /dev/null 2>&1

    print_ok "Системные пакеты обновлены"
    print_ok "python3-venv, python3-pip, git установлены"
}

# Создание виртуального окружения и установка зависимостей
setup_venv() {
    print_header "Настройка виртуального окружения Python"

    python3 -m venv "$VENV_DIR"
    print_ok "Виртуальное окружение создано: $VENV_DIR"

    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip -q
    pip install --upgrade -r "$INSTALL_DIR/requirements.txt" -q
    deactivate

    print_ok "Зависимости Python установлены в venv"
}

# Настройка systemd сервиса
setup_systemd() {
    print_header "Настройка автозапуска (systemd)"

    cat > "$INSTALL_DIR/$SERVICE_FILE" << EOF
[Unit]
Description=Yadreno VPN Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    cp "$INSTALL_DIR/$SERVICE_FILE" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable yadreno-vpn > /dev/null 2>&1

    print_ok "systemd сервис установлен и включён в автозапуск"
}

# Запуск сервиса
start_service() {
    systemctl start yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот запущен и работает!"
    else
        print_err "Бот не запустился. Проверьте логи:"
        echo "  systemctl status yadreno-vpn"
        echo "  journalctl -u yadreno-vpn -n 50"
    fi
}

# ============================================================
# ПУНКТ 1: УСТАНОВКА
# ============================================================
do_install() {
    print_header "🚀 Установка Yadreno VPN"

    # Проверяем, не установлен ли уже
    if [ -d "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.git" ]; then
        print_warn "Yadreno VPN уже установлен в $INSTALL_DIR"
        if [ "$AUTO_MODE" = "1" ]; then
            print_warn "Автоматический режим: принудительная переустановка"
            reinstall_choice="1"
        else
            echo ""
            echo "  1) Переустановить (удалить и установить заново)"
            echo "  2) Отмена"
            read -p "Выберите [1-2]: " reinstall_choice
        fi
        if [ "$reinstall_choice" != "1" ]; then
            echo "Установка отменена."
            return 0
        fi
        systemctl stop yadreno-vpn 2>/dev/null || true
        # Сохраняем config.py и базу данных
        if [ -f "$INSTALL_DIR/config.py" ]; then
            cp "$INSTALL_DIR/config.py" /tmp/yadreno_config_backup.py
            BACKUP_CONFIG=1
        fi
        if [ -f "$DB_PATH" ]; then
            cp "$DB_PATH" /tmp/yadreno_db_backup.db
            BACKUP_DB=1
        fi
        rm -rf "$INSTALL_DIR"
    fi

    # Запрашиваем настройки до начала установки
    ask_config

    # Установка системных зависимостей
    install_system_deps

    # Клонирование репозитория
    print_header "Загрузка Yadreno VPN"
    git clone "$REPO_URL" "$INSTALL_DIR" -q
    cd "$INSTALL_DIR"
    print_ok "Репозиторий клонирован"

    # Восстановление backup'ов при переустановке
    if [ "$BACKUP_CONFIG" = "1" ] && [ -f "/tmp/yadreno_config_backup.py" ]; then
        cp /tmp/yadreno_config_backup.py "$INSTALL_DIR/config.py"
        rm /tmp/yadreno_config_backup.py
        print_ok "config.py восстановлен из резервной копии"
        NEED_WRITE_CONFIG=0
    fi
    if [ "$BACKUP_DB" = "1" ] && [ -f "/tmp/yadreno_db_backup.db" ]; then
        mkdir -p "$INSTALL_DIR/database"
        cp /tmp/yadreno_db_backup.db "$DB_PATH"
        rm /tmp/yadreno_db_backup.db
        print_ok "База данных восстановлена из резервной копии"
    fi

    # Запись config.py
    write_config

    # Виртуальное окружение и зависимости
    setup_venv

    # Настройка автозапуска
    setup_systemd

    # Запуск
    print_header "Запуск бота"
    start_service

    print_header "✅ Установка завершена!"
    echo -e "  Директория: ${GREEN}$INSTALL_DIR${NC}"
    echo -e "  Виртуальное окружение: ${GREEN}$VENV_DIR${NC}"
    echo -e "  Управление сервисом:"
    echo -e "    ${CYAN}systemctl status yadreno-vpn${NC}   — статус"
    echo -e "    ${CYAN}systemctl restart yadreno-vpn${NC}  — перезапуск"
    echo -e "    ${CYAN}systemctl stop yadreno-vpn${NC}     — остановка"
    echo -e "    ${CYAN}journalctl -u yadreno-vpn -f${NC}   — логи"
}

# ============================================================
# ПУНКТ 2: МЯГКОЕ ОБНОВЛЕНИЕ (git pull)
# ============================================================
do_soft_update() {
    print_header "🔄 Мягкое обновление"
    STASHED=0
    UPDATE_SNAPSHOT_ID=""

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    cd "$INSTALL_DIR"
    acquire_update_operation_lock

    local requested_target="origin/main"
    if [ -n "$TARGET_COMMIT" ]; then
        requested_target="$TARGET_COMMIT"
    fi
    prepare_update_snapshot "installer_update" "$requested_target"

    # Сохраняем текущие изменения в stash (если есть)
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
        print_warn "Обнаружены локальные изменения — сохраняем через git stash"
        git stash -q
        STASHED=1
    fi

    if [ -n "$TARGET_COMMIT" ]; then
        git fetch -q origin
        git checkout -q "$TARGET_COMMIT"
    else
        git checkout -q main
        git pull -q origin main
    fi

    if [ "$STASHED" = "1" ]; then
        git stash pop -q 2>/dev/null || print_warn "Не удалось восстановить локальные изменения (конфликт)"
    fi

    mark_update_snapshot_applied

    print_ok "Код обновлён"

    # Обновляем зависимости
    source "$VENV_DIR/bin/activate"
    pip install --upgrade -r requirements.txt -q
    deactivate
    print_ok "Зависимости обновлены"

    # Перезапуск
    systemctl restart yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот перезапущен и работает!"
    else
        print_err "Бот не запустился после обновления"
        echo "  systemctl status yadreno-vpn"
    fi
    release_update_operation_lock
}

# ============================================================
# ПУНКТ 3: ЖЁСТКАЯ ПЕРЕЗАПИСЬ (git fetch + reset)
# ============================================================
do_hard_reset() {
    print_header "⚠️  Жёсткая перезапись"
    UPDATE_SNAPSHOT_ID=""

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    echo -e "${RED}Внимание! Все локальные изменения в коде будут перезаписаны.${NC}"
    echo -e "${YELLOW}config.py и database/vpn_bot.db затронуты НЕ будут.${NC}"
    if [ "$AUTO_MODE" = "1" ]; then
        confirm="y"
    else
        read -p "Продолжить? (y/N): " confirm
    fi
    if [[ ! "$confirm" =~ ^[YyДд]$ ]]; then
        echo "Отменено."
        return 0
    fi

    cd "$INSTALL_DIR"
    acquire_update_operation_lock

    # Жёсткая перезапись: config.py и database/vpn_bot.db игнорируются Git
    git fetch origin -q
    local target="origin/main"
    if [ -n "$TARGET_COMMIT" ]; then
        target="$TARGET_COMMIT"
    fi
    prepare_update_snapshot "installer_reset" "$target"
    git reset --hard "$target" -q
    git clean -fd -q \
        -e backup/ \
        -e config.py \
        -e custom_extensions/ \
        -e database/vpn_bot.db \
        -e database/vpn_bot.db-wal \
        -e database/vpn_bot.db-shm \
        -e logs/ \
        -e venv/
    mark_update_snapshot_applied
    print_ok "Код перезаписан ($target)"

    # Обновляем зависимости
    source "$VENV_DIR/bin/activate"
    pip install --upgrade -r requirements.txt -q
    deactivate
    print_ok "Зависимости обновлены"

    # Перезапуск
    systemctl restart yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот перезапущен и работает!"
    else
        print_err "Бот не запустился после перезаписи"
        echo "  systemctl status yadreno-vpn"
    fi
    release_update_operation_lock
}

# ============================================================
# ПУНКТ 4: ОТКАТ ПО PRE-UPDATE BACKUP
# ============================================================
do_rollback() {
    print_header "↩️ Откат обновления"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        print_err "Python из виртуального окружения не найден: $VENV_DIR/bin/python"
        return 1
    fi

    cd "$INSTALL_DIR"
    "$VENV_DIR/bin/python" -m bot.services.update_rollback interactive \
        --project-root "$INSTALL_DIR" \
        --service-name "yadreno-vpn"
}

# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================
show_menu() {
    clear
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       🌐 Yadreno VPN Manager         ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo -e "${NC}"
    echo "  1) 🚀 Установка"
    echo "  2) 🔄 Мягкое обновление (git pull)"
    echo "  3) ⚠️  Жёсткая перезапись (с GitHub)"
    echo "  4) ↩️  Откат обновления"
    echo ""
    echo "  0) Выход"
    echo ""
    read -p "  Выберите действие [0-4]: " choice

    case $choice in
        1) do_install ;;
        2) do_soft_update ;;
        3) do_hard_reset ;;
        4) do_rollback ;;
        0) echo "Пока! 👋"; exit 0 ;;
        *) echo "Неверный выбор"; return 1 ;;
    esac
}

# Проверка root-прав
if [ "$EUID" -ne 0 ]; then
    print_err "Скрипт должен быть запущен от root (sudo)"
    exit 1
fi

# Проверка на автоматический режим (передан аргумент действия)
if [ -n "$1" ]; then
    ACTION="$1"
    export AUTO_MODE="1"
    
    case "$ACTION" in
        install)
            if [ -z "$2" ] || [ -z "$3" ]; then
                print_err "Для автоматической установки требуются BOT_TOKEN и ADMIN_ID"
                echo "Использование: bash install.sh install <BOT_TOKEN> <ADMIN_ID>"
                exit 1
            fi
            export BOT_TOKEN="$2"
            export ADMIN_ID="$3"
            do_install 
            ;;
        update)
            export TARGET_COMMIT="$2"
            do_soft_update 
            ;;
        reset)
            export TARGET_COMMIT="$2"
            do_hard_reset 
            ;;
        rollback)
            do_rollback
            ;;
        *)
            print_err "Неизвестное действие: $ACTION. Доступно: install, update, reset, rollback"
            exit 1
            ;;
    esac
    exit 0
fi

show_menu
