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
            echo "  1) Безопасно переустановить на месте"
            echo "  2) Отмена"
            read -p "Выберите [1-2]: " reinstall_choice
        fi
        if [ "$reinstall_choice" != "1" ]; then
            echo "Установка отменена."
            return 0
        fi
        REINSTALL_EXISTING=1
        ask_config
        write_config
        install_system_deps
        if ! do_hard_reset; then
            print_err "Безопасная переустановка не завершена"
            return 1
        fi
        setup_systemd
        print_header "✅ Переустановка завершена!"
        echo -e "  Директория: ${GREEN}$INSTALL_DIR${NC}"
        echo -e "  Все копии БД сохранены в: ${GREEN}$INSTALL_DIR/backup${NC}"
        return 0
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
# ПУНКТ 2: ШТАТНОЕ МЯГКОЕ ОБНОВЛЕНИЕ (fast-forward)
# ============================================================
do_soft_update() {
    print_header "🔄 Мягкое обновление"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    cd "$INSTALL_DIR"
    local requested_target="origin/main"
    if [ -n "$TARGET_COMMIT" ]; then
        requested_target="$TARGET_COMMIT"
    fi

    # The downloaded installer may run against an older installed updater.
    # Resolve the marked stage here as well so that version cannot skip the
    # first blocking commit before the target code takes over this policy.
    if ! git fetch -q origin; then
        print_err "Не удалось получить список обновлений с GitHub"
        return 1
    fi
    local resolved_target
    if ! resolved_target=$(git rev-parse --verify "${requested_target}^{commit}" 2>/dev/null); then
        print_err "Целевая версия обновления недоступна: $requested_target"
        return 1
    fi
    local blocking_commit=""
    local commit_subject=""
    while IFS='|' read -r commit_hash commit_subject; do
        if [[ "$commit_subject" == \!* ]]; then
            blocking_commit="$commit_hash"
            break
        fi
    done < <(git log "HEAD..$resolved_target" --format='%H|%s' --reverse)

    if [ -n "$blocking_commit" ]; then
        requested_target="$blocking_commit"
        print_warn "Сначала будет установлена обязательная переходная версия ${blocking_commit:0:8}"
    fi
    local update_args=(
        update
        --project-root "$INSTALL_DIR"
        --mode "installer_update"
        --target "$requested_target"
        --strategy "pull"
        --actor "installer"
        --service-name "yadreno-vpn"
    )
    if [ -n "$blocking_commit" ]; then
        update_args+=(--block-updates)
    fi

    local output
    if ! output=$(
        "$VENV_DIR/bin/python" -m bot.services.update_rollback "${update_args[@]}" 2>&1
    ); then
        print_err "Обновление не установлено"
        echo "$output"
        return 1
    fi
    print_ok "$output"
}

# ============================================================
# ПУНКТ 3: АВАРИЙНАЯ ЖЁСТКАЯ ПЕРЕЗАПИСЬ (git fetch + reset)
# ============================================================
do_hard_reset() {
    print_header "⚠️  Жёсткая перезапись"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    echo -e "${RED}Внимание! Все локальные изменения в коде будут перезаписаны.${NC}"
    echo -e "${YELLOW}config.py, данные бота и каталог backup/ будут сохранены.${NC}"
    if [ "$AUTO_MODE" = "1" ] || [ "$REINSTALL_EXISTING" = "1" ]; then
        confirm="y"
    else
        read -p "Продолжить? (y/N): " confirm
    fi
    if [[ ! "$confirm" =~ ^[YyДд]$ ]]; then
        echo "Отменено."
        return 0
    fi

    cd "$INSTALL_DIR"
    local target="origin/main"
    if [ -n "$TARGET_COMMIT" ]; then
        target="$TARGET_COMMIT"
    fi

    local mode="installer_reset"
    if [ "$REINSTALL_EXISTING" = "1" ]; then
        mode="installer_reinstall"
    fi
    local output
    if ! output=$(
        "$VENV_DIR/bin/python" -m bot.services.update_rollback update \
            --project-root "$INSTALL_DIR" \
            --mode "$mode" \
            --target "$target" \
            --strategy "reset" \
            --actor "installer" \
            --service-name "yadreno-vpn" \
            --clean-untracked 2>&1
    ); then
        print_err "Перезапись не установлена"
        echo "$output"
        return 1
    fi
    print_ok "$output"
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
    cd "$INSTALL_DIR"
    local python_bin="$VENV_DIR/bin/python"
    if [ ! -x "$python_bin" ]; then
        python_bin=$(command -v python3 || true)
    fi
    if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
        print_err "Не найден Python для автономного отката"
        return 1
    fi

    local runner=""
    local candidate
    for candidate in "$INSTALL_DIR"/backup/pre_update/*/rollback_runner.py; do
        if [ ! -f "$candidate" ]; then
            continue
        fi
        if [ -z "$runner" ] || [ "$candidate" -nt "$runner" ]; then
            runner="$candidate"
        fi
    done
    if [ -z "$runner" ]; then
        print_err "В backup/pre_update не найден автономный исполнитель отката"
        return 1
    fi

    "$python_bin" "$runner" interactive \
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
            if do_soft_update; then
                exit 0
            fi
            exit 1
            ;;
        reset)
            export TARGET_COMMIT="$2"
            if do_hard_reset; then
                exit 0
            fi
            exit 1
            ;;
        rollback)
            if do_rollback; then
                exit 0
            fi
            exit 1
            ;;
        *)
            print_err "Неизвестное действие: $ACTION. Доступно: install, update, reset, rollback"
            exit 1
            ;;
    esac
    exit 0
fi

show_menu
