#!/bin/bash

# Yadreno VPN — скрипт установки и управления
# Запуск: bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh)
# 
# === АВТОМАТИЧЕСКИЙ ЗАПУСК (БЕЗ ДИАЛОГОВ) ===
#
# 1. Запуск прямо с GitHub (для чистой установки или если папки ещё нет):
# bash <(curl -fsSL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) install <BOT_TOKEN> <ADMIN_ID> < /dev/null
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) update [COMMIT_OR_BRANCH]
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) reset [COMMIT_OR_BRANCH]
# bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh) rollback
#
# 2. Локальный запуск (если репозиторий уже установлен и нужно просто обновить/сбросить):
# bash install.sh update [COMMIT_OR_BRANCH]
# bash install.sh reset [COMMIT_OR_BRANCH]
# bash install.sh rollback
# bash install.sh web-setup --proxy managed-nginx --domain vpn.example.com --email admin@example.com --agree-tos --output json
# Without an explicit update/reset target, install/reinstall/update/reset select
# the latest first-parent origin/main commit whose subject does not start with '?'.

set -e

INSTALL_DIR="/root/YadrenoVPN"
REPO_URL="https://github.com/plushkinv/YadrenoVPN.git"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_FILE="yadreno-vpn.service"
UPDATER_SERVICE_FILE="yadreno-vpn-updater@.service"
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

# Resolve an already fetched target and emit only its immutable commit hash.
resolve_install_target() {
    local requested_target="${1:-}"
    local target=""
    if [ -n "$requested_target" ]; then
        if ! target=$(git rev-parse --verify "${requested_target}^{commit}" 2>/dev/null); then
            print_err "Целевая версия недоступна: $requested_target" >&2
            return 1
        fi
    else
        local history commit_hash commit_subject
        if ! history=$(git log origin/main --first-parent --format='%H|%s'); then
            print_err "Не удалось прочитать историю стабильных версий origin/main" >&2
            return 1
        fi
        while IFS='|' read -r commit_hash commit_subject; do
            if [ -n "$commit_hash" ] && [[ "$commit_subject" != \?* ]]; then
                target="$commit_hash"
                break
            fi
        done <<< "$history"
        if [ -z "$target" ]; then
            print_err "В истории origin/main не найдена стабильная версия без ? в начале заголовка" >&2
            return 1
        fi
    fi
    printf '%s\n' "$target"
}

# Create and verify the mandatory database snapshot before a direct reset.
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
        print_err "Не удалось создать и проверить backup базы данных. Перезапись отменена."
        return 1
    fi

    UPDATE_SNAPSHOT_ID=$(echo "$output" | tail -n 1 | tr -d '\r')
    if [ -z "$UPDATE_SNAPSHOT_ID" ]; then
        print_err "Исполнитель backup не вернул идентификатор точки отката"
        return 1
    fi
    print_ok "Создан pre-update backup: $UPDATE_SNAPSHOT_ID"
}

# Bind the direct Git reset to the verified manual rollback point.
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
    print_ok "Точка ручного отката привязана к установленному коммиту"
}

acquire_update_operation_lock() {
    if ! command -v flock > /dev/null 2>&1; then
        print_err "Команда flock не найдена; безопасная перезапись невозможна"
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
    sed -i "/^ADMIN_IDS = \[/,/^\]/s|12345678|$ADMIN_ID|g" "$INSTALL_DIR/config.py"

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

setup_ready_web_ui() {
    if [ -f "$INSTALL_DIR/web_tools/release.py" ]; then
        print_header "Подготовка готового интерфейса сайта и Mini App"
        if ! (cd "$INSTALL_DIR" && "$VENV_DIR/bin/python" -m web_tools.release ensure-base); then
            print_err "Готовый интерфейс не прошёл проверку. Веб не активирован."
            return 1
        fi
        print_ok "Интерфейс подготовлен без Node. Домен и HTTPS подключаются отдельно."
    fi
}

setup_web_toolchain() {
    if [ -f "$INSTALL_DIR/web_tools/toolchain.py" ]; then
        print_header "Подготовка инструментов веб-редактора"
        if (cd "$INSTALL_DIR" && "$VENV_DIR/bin/python" -m web_tools.toolchain prepare); then
            print_ok "Инструменты веб-редактора подготовлены"
        else
            print_warn "Инструменты веб-редактора пока недоступны. Бот и сайт продолжают работать; подготовка повторится при обновлении."
        fi
    fi
}

# Remove only untracked artifacts left by older installers. Some supported
# source versions ship the root service template as an ordinary tracked file.
cleanup_legacy_unit_files() {
    local old_unit tracked_unit
    for old_unit in "$SERVICE_FILE" "$UPDATER_SERVICE_FILE"; do
        if ! tracked_unit=$(git -C "$INSTALL_DIR" ls-files -- "$old_unit"); then
            print_err "Не удалось проверить старые unit-файлы в Git"
            return 1
        fi
        if [ -z "$tracked_unit" ] && ! rm -f "$INSTALL_DIR/$old_unit"; then
            print_err "Не удалось удалить старые unit-файлы из рабочего каталога"
            return 1
        fi
    done
}

# Repair the exact unstaged deletion caused by earlier setup_systemd versions
# before invoking an installed updater that correctly rejects a dirty tree.
recover_legacy_tracked_service_file() {
    local legacy_unit="yadreno-vpn.service"
    if [ -e "$INSTALL_DIR/$legacy_unit" ] || [ -L "$INSTALL_DIR/$legacy_unit" ]; then
        return 0
    fi
    local index_status=0
    git -C "$INSTALL_DIR" diff --cached --quiet --exit-code || index_status=$?
    if [ "$index_status" -eq 1 ]; then
        return 0
    fi
    if [ "$index_status" -ne 0 ]; then
        print_err "Не удалось проверить индекс Git перед обновлением"
        return 1
    fi
    local unit_status
    if ! unit_status=$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no); then
        print_err "Не удалось проверить шаблон службы в Git"
        return 1
    fi
    if [ "$unit_status" != " D $legacy_unit" ]; then
        return 0
    fi
    local legacy_installer legacy_line legacy_cleanup=0
    if ! legacy_installer=$(git -C "$INSTALL_DIR" show HEAD:install.sh 2>/dev/null); then
        return 0
    fi
    while IFS= read -r legacy_line; do
        if [ "$legacy_line" = '    if ! rm -f "$INSTALL_DIR/$SERVICE_FILE" "$INSTALL_DIR/$UPDATER_SERVICE_FILE"; then' ]; then
            legacy_cleanup=1
            break
        fi
    done <<< "$legacy_installer"
    if [ "$legacy_cleanup" -ne 1 ]; then
        return 0
    fi
    if ! git -C "$INSTALL_DIR" checkout -- "$legacy_unit"; then
        print_err "Не удалось восстановить удалённый старым установщиком шаблон службы"
        return 1
    fi
    print_ok "Отсутствующий шаблон yadreno-vpn.service восстановлен из текущей версии Git"
}

# Настройка systemd сервиса
setup_systemd() {
    print_header "Настройка автозапуска (systemd)"

    if ! cat > "/etc/systemd/system/$SERVICE_FILE" << EOF
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
    then
        print_err "Не удалось записать основной systemd-сервис"
        return 1
    fi

    cleanup_legacy_unit_files || return 1
    if ! "$VENV_DIR/bin/python" -m bot.services.update_rollback install-service \
        --project-root "$INSTALL_DIR" \
        --service-name yadreno-vpn > /dev/null 2>&1; then
        # A requested intermediate/older commit may not expose install-service
        # yet. Keep the current installer able to provision the stable unit.
        local updater_unit_stage_dir
        if ! updater_unit_stage_dir=$(mktemp -d "/etc/systemd/system/.yadreno-updater-unit.XXXXXX"); then
            print_err "Не удалось подготовить проверку updater-service"
            return 1
        fi
        local updater_unit_candidate="$updater_unit_stage_dir/$UPDATER_SERVICE_FILE"
        local updater_unit_error=""
        if ! cat > "$updater_unit_candidate" << EOF
[Unit]
Description=Yadreno VPN managed updater for snapshot %i
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/backup/pre_update/%i/service_runner.py service-request --project-root $INSTALL_DIR --snapshot-id %i --service-name yadreno-vpn
TimeoutStartSec=20min
UMask=0077
Environment=PYTHONUNBUFFERED=1
EOF
        then
            updater_unit_error="Не удалось записать постоянный updater-service"
        elif ! chmod 0644 "$updater_unit_candidate"; then
            updater_unit_error="Не удалось установить права updater-service"
        elif ! systemd-analyze verify "$updater_unit_candidate"; then
            updater_unit_error="Updater-service содержит недопустимые настройки"
        elif ! mv -f "$updater_unit_candidate" "/etc/systemd/system/$UPDATER_SERVICE_FILE"; then
            updater_unit_error="Не удалось установить updater-service"
        fi
        rm -f "$updater_unit_candidate"
        rmdir "$updater_unit_stage_dir" 2>/dev/null || true
        if [ -n "$updater_unit_error" ]; then
            print_err "$updater_unit_error"
            return 1
        fi
        if ! systemctl daemon-reload; then
            print_err "Не удалось зарегистрировать постоянный updater-service"
            return 1
        fi
    fi
    if ! systemctl enable yadreno-vpn > /dev/null 2>&1; then
        print_err "Не удалось включить автозапуск основного сервиса"
        return 1
    fi

    print_ok "Основной systemd-сервис установлен и включён в автозапуск"
    print_ok "Постоянный updater-service зарегистрирован"
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
            print_err "Переустановка не завершена; при необходимости выполните ручной откат"
            return 1
        fi
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
    git clone --branch main "$REPO_URL" "$INSTALL_DIR" -q
    cd "$INSTALL_DIR"
    print_ok "Репозиторий клонирован"

    local target
    if ! target=$(resolve_install_target); then
        return 1
    fi
    if ! git reset --hard "$target" -q; then
        print_err "Не удалось выбрать стабильную версию для установки"
        return 1
    fi
    print_ok "Выбрана стабильная версия: ${target:0:8}"

    # Запись config.py
    write_config

    # Виртуальное окружение и зависимости
    setup_venv

    setup_ready_web_ui || return 1

    # Настройка автозапуска
    setup_systemd

    # Запуск
    print_header "Запуск бота"
    start_service

    setup_web_toolchain

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
    # The downloaded installer may run against an older installed updater.
    # Resolve the marked stage here as well so that version cannot skip the
    # first blocking commit before the target code takes over this policy.
    if ! git fetch -q origin; then
        print_err "Не удалось получить список обновлений с GitHub"
        return 1
    fi
    local resolved_target
    if ! resolved_target=$(resolve_install_target "${TARGET_COMMIT:-}"); then
        return 1
    fi
    local requested_target="$resolved_target"
    print_ok "Выбрана версия обновления: ${resolved_target:0:8}"
    recover_legacy_tracked_service_file || return 1
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
    setup_systemd
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
    if ! acquire_update_operation_lock; then
        return 1
    fi

    if ! git fetch -q origin; then
        print_err "Не удалось загрузить целевую версию с GitHub"
        release_update_operation_lock
        return 1
    fi

    local target
    if ! target=$(resolve_install_target "${TARGET_COMMIT:-}"); then
        release_update_operation_lock
        return 1
    fi
    print_ok "Выбрана версия для перезаписи: ${target:0:8}"

    local mode="installer_reset"
    if [ "$REINSTALL_EXISTING" = "1" ]; then
        mode="installer_reinstall"
    fi
    local service_was_active=0
    if systemctl is-active --quiet yadreno-vpn; then
        service_was_active=1
    fi
    if ! systemctl stop yadreno-vpn; then
        print_err "Не удалось остановить бот перед перезаписью"
        release_update_operation_lock
        return 1
    fi

    UPDATE_SNAPSHOT_ID=""
    if ! prepare_update_snapshot "$mode" "$target"; then
        if [ "$service_was_active" = "1" ]; then
            systemctl start yadreno-vpn || true
        fi
        release_update_operation_lock
        return 1
    fi

    if ! git reset --hard "$target" -q; then
        print_err "Не удалось перезаписать Git-версию"
        systemctl start yadreno-vpn || true
        release_update_operation_lock
        return 1
    fi

    if ! mark_update_snapshot_applied; then
        print_err "Код перезаписан, но точку ручного отката не удалось завершить"
        release_update_operation_lock
        return 1
    fi
    if ! git clean -fd -q \
        -e backup/ \
        -e config.py \
        -e custom_extensions/ \
        -e custom_web/ \
        -e web_runtime/ \
        -e database/vpn_bot.db \
        -e database/vpn_bot.db-wal \
        -e database/vpn_bot.db-shm \
        -e logs/ \
        -e venv/; then
        print_err "Не удалось очистить файлы прежней версии"
        release_update_operation_lock
        return 1
    fi
    print_ok "Код перезаписан (${target:0:8})"

    if ! "$VENV_DIR/bin/python" -m pip install --upgrade -r requirements.txt -q; then
        print_err "Не удалось обновить зависимости. Выполните ручной откат."
        release_update_operation_lock
        return 1
    fi
    print_ok "Зависимости обновлены"

    if ! setup_systemd; then
        print_err "Не удалось обновить systemd-сервисы. Выполните ручной откат."
        release_update_operation_lock
        return 1
    fi

    if ! systemctl start yadreno-vpn; then
        print_err "Бот не запустился после перезаписи. Выполните ручной откат."
        release_update_operation_lock
        return 1
    fi
    sleep 2
    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот перезаписан и запущен"
        release_update_operation_lock
        return 0
    fi

    print_err "Бот не работает после перезаписи. Выполните ручной откат."
    echo "  bash install.sh rollback"
    release_update_operation_lock
    return 1
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

# The installed Python entry point owns validation, system changes and recovery.
# The four existing actions and their positional arguments remain unchanged.
do_web_setup() {
    local python_bin="$VENV_DIR/bin/python"
    if [ ! -x "$python_bin" ] || [ ! -f "$INSTALL_DIR/web_api/management.py" ]; then
        echo 'Не найдена установленная версия с Web Core и Python environment.' >&2
        if [[ " $* " == *" --output json "* ]] || [[ " $* " == *" --output=json "* ]]; then
            echo '{"ok":false,"code":"installation_missing","stage":"preflight","changed":false,"public_url":null,"listen":null,"button":{"changed":false,"code":"not_attempted"}}'
        fi
        return 3
    fi
    if [ "$AUTO_MODE" = "1" ]; then
        (cd "$INSTALL_DIR" && "$python_bin" -m web_api.management --project-root "$INSTALL_DIR" setup "$@" < /dev/null)
        return $?
    fi
    local web_proxy web_domain web_email web_tos web_origin
    local web_args=()
    echo 'Сайт и Mini App используют отдельный домен в корне /.'
    echo '1) Nginx и HTTPS на этом сервере; 2) Подготовленный внешний прокси'
    read -r -p 'Режим [1/2]: ' web_proxy
    case "$web_proxy" in
        1)
            read -r -p 'Домен или поддомен: ' web_domain
            read -r -p 'Email для сертификата: ' web_email
            echo 'Условия ACME: https://letsencrypt.org/repository/'
            read -r -p 'Принимаете условия выдачи сертификата? (yes/no): ' web_tos
            if [ "$web_tos" != 'yes' ]; then
                echo 'Подключение отменено.'
                return 0
            fi
            web_args=(--proxy managed-nginx --domain "$web_domain" --email "$web_email" --agree-tos)
            ;;
        2)
            read -r -p 'Готовый публичный HTTPS URL: ' web_origin
            web_args=(--proxy external --public-url "$web_origin")
            ;;
        *) echo 'Неверный режим.' >&2; return 2 ;;
    esac
    (cd "$INSTALL_DIR" && "$python_bin" -m web_api.management --project-root "$INSTALL_DIR" setup "${web_args[@]}" < /dev/null)
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
    echo "  5) 🌐 Подключить сайт и Mini App — домен и HTTPS"
    echo ""
    echo "  0) Выход"
    echo ""
    read -p "  Выберите действие [0-5]: " choice

    case $choice in
        1) do_install ;;
        2) do_soft_update ;;
        3) do_hard_reset ;;
        4) do_rollback ;;
        5) do_web_setup ;;
        0) echo "Пока! 👋"; exit 0 ;;
        *) echo "Неверный выбор"; return 1 ;;
    esac
}

# Проверка root-прав
if [ "$EUID" -ne 0 ]; then
    if [ "$1" = "web-setup" ]; then
        echo 'Подключение веба требует root (sudo).' >&2
        if [[ " $* " == *" --output json "* ]] || [[ " $* " == *" --output=json "* ]]; then
            echo '{"ok":false,"code":"root_required","stage":"preflight","changed":false,"public_url":null,"listen":null,"button":{"changed":false,"code":"not_attempted"}}'
        fi
        exit 3
    fi
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
        web-setup)
            shift
            if do_web_setup "$@"; then
                exit 0
            else
                exit $?
            fi
            ;;
        *)
            print_err "Неизвестное действие: $ACTION. Доступно: install, update, reset, rollback, web-setup"
            exit 1
            ;;
    esac
    exit 0
fi

show_menu
