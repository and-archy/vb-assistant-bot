#!/usr/bin/env bash
# vb-assistant-bot — one-command install (Ubuntu/Debian, systemd).
# See docs/INSTALL.md for the full step-by-step guide.
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

SERVICE_NAME="vb-assistant-bot"
SERVICE_USER="vbassistant"
ENV_DIR="/etc/vb-assistant-bot"
ENV_FILE="$ENV_DIR/env"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
DATA_DIR="$REPO_DIR/data"
VENV_DIR="$REPO_DIR/.venv"

TELEGRAM_TOKEN=""
ADMIN_USER_IDS=""
GROUP_CHAT_ID=""
ALERTS_API_TOKEN=""
ALERTS_REGION_UID=""
TIMEZONE=""

require_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Цей скрипт треба запускати через sudo:" >&2
        echo "  sudo bash deploy/install.sh" >&2
        exit 1
    fi
}

require_apt() {
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Цей скрипт підтримує лише Ubuntu/Debian (потрібен apt-get)." >&2
        exit 1
    fi
}

install_packages() {
    echo "==> Встановлюю системні пакети (python3, venv, git, curl, tzdata)..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl tzdata
}

ensure_service_user() {
    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        return 0
    fi
    echo "==> Створюю системного користувача '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
}

check_repo_dir() {
    if ! su -s /bin/sh "$SERVICE_USER" -c "cd '$REPO_DIR'" 2>/dev/null; then
        echo "Помилка: користувач '$SERVICE_USER' не може перейти в теку $REPO_DIR" >&2
        echo "(найчастіше причина — репозиторій лежить у /root чи іншій домашній теці," >&2
        echo "яка доступна лише своєму власнику)." >&2
        echo >&2
        echo "Перенеси репозиторій в /opt і запусти встановлення звідти:" >&2
        echo "  sudo mv $REPO_DIR /opt/vb-assistant-bot" >&2
        echo "  cd /opt/vb-assistant-bot && sudo bash deploy/install.sh" >&2
        exit 1
    fi
}

setup_venv() {
    echo "==> Готую Python-оточення (venv)..."
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
    "$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
    "$VENV_DIR/bin/pip" install -e "$REPO_DIR" -q
}

verify_token() {
    local token="$1"
    local response
    response="$(curl -fsS "https://api.telegram.org/bot${token}/getMe" 2>/dev/null)" || return 1
    python3 - "$response" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(1)
if not data.get("ok"):
    sys.exit(1)
print(data["result"]["username"])
PY
}

prompt_token() {
    local attempt=0
    local token username
    echo
    echo "Токен бота отримуєш у Telegram через @BotFather (команда /newbot або /mybots)."
    while true; do
        read -rp "Токен бота: " token
        if [ -z "$token" ]; then
            echo "Токен не може бути порожнім."
            continue
        fi
        attempt=$((attempt + 1))
        if username="$(verify_token "$token")"; then
            echo "Токен валідний, бот: @${username}"
            TELEGRAM_TOKEN="$token"
            return 0
        fi
        echo "Не вдалося перевірити токен через Telegram API (спроба $attempt/3)."
        if [ "$attempt" -ge 3 ]; then
            echo "Продовжую без перевірки — переконайся, що токен правильний, і онови його пізніше через $ENV_FILE, якщо треба."
            TELEGRAM_TOKEN="$token"
            return 0
        fi
    done
}

prompt_admin_ids() {
    local ids
    echo
    echo "Свій Telegram ID дізнаєшся, написавши боту @userinfobot."
    echo "Потрібно щонайменше двоє адмінів (ТЗ — щоб не залежати від однієї людини)."
    while true; do
        read -rp "Telegram ID адмінів через кому (напр. 111111111,222222222): " ids
        ids="${ids// /}"
        if [[ "$ids" =~ ^[0-9]+(,[0-9]+)+$ ]]; then
            ADMIN_USER_IDS="$ids"
            return 0
        fi
        echo "Формат: щонайменше двоє, лише цифри, розділені комою, без пробілів."
    done
}

prompt_group_chat_id() {
    local id
    echo
    echo "Chat ID групи General дізнаєшся, додавши бота @getidsbot у групу"
    echo "(або переславши повідомлення з групи боту @getidsbot у особисті)."
    echo "Це буде від'ємне число, напр. -1001234567890."
    while true; do
        read -rp "Group Chat ID: " id
        if [[ "$id" =~ ^-?[0-9]+$ ]]; then
            GROUP_CHAT_ID="$id"
            return 0
        fi
        echo "Формат: ціле число (може бути від'ємним)."
    done
}

prompt_alerts_token() {
    echo
    echo "Ключ alerts.in.ua отримуєш, написавши на api@alerts.in.ua."
    read -rp "ALERTS_API_TOKEN: " ALERTS_API_TOKEN
    read -rp "uid регіону моніторингу [31 — м. Київ]: " ALERTS_REGION_UID
    ALERTS_REGION_UID="${ALERTS_REGION_UID:-31}"
}

verify_timezone() {
    python3 -c "from zoneinfo import ZoneInfo; ZoneInfo('$1')" 2>/dev/null
}

prompt_timezone() {
    local tz
    echo
    while true; do
        read -rp "Часовий пояс [Europe/Kyiv]: " tz
        tz="${tz:-Europe/Kyiv}"
        if verify_timezone "$tz"; then
            TIMEZONE="$tz"
            return 0
        fi
        echo "Невідомий часовий пояс: '$tz'. Приклад правильного формату: Europe/Kyiv."
    done
}

prepare_data_dir() {
    echo "==> Готую теку даних ($DATA_DIR)..."
    mkdir -p "$DATA_DIR"
    chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
}

write_env_file() {
    echo "==> Записую налаштування в $ENV_FILE..."
    mkdir -p "$ENV_DIR"
    cat > "$ENV_FILE" <<EOF
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ADMIN_USER_IDS=$ADMIN_USER_IDS
GROUP_CHAT_ID=$GROUP_CHAT_ID
ALERTS_API_TOKEN=$ALERTS_API_TOKEN
ALERTS_REGION_UID=$ALERTS_REGION_UID
DB_PATH=$DATA_DIR/vb_assistant.db
TIMEZONE=$TIMEZONE
TEXTS_CONFIG_PATH=$REPO_DIR/config/texts.json
THRESHOLDS_CONFIG_PATH=$REPO_DIR/config/thresholds.json
AUTO_PUBLISH_ENABLED=false
LOG_FILE=
EOF
    chmod 600 "$ENV_FILE"
    chown root:root "$ENV_FILE"
}

write_unit_file() {
    echo "==> Реєструю systemd-службу..."
    cat > "$UNIT_PATH" <<EOF
[Unit]
Description=VB Assistant Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python -m vb_assistant_bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

enable_service() {
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null
    systemctl restart "$SERVICE_NAME"
}

print_summary() {
    echo
    echo "==> Готово. Статус служби:"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
    echo
    echo "Живі логи:   journalctl -u $SERVICE_NAME -f"
    echo "Налаштування: $ENV_FILE (зміни -> sudo systemctl restart $SERVICE_NAME)"
    echo "Тексти повідомлень: $REPO_DIR/config/texts.json"
    echo "Пороги тригера:     $REPO_DIR/config/thresholds.json"
    echo "(зміни в config/*.json теж потребують sudo systemctl restart $SERVICE_NAME)"
    echo
    echo "УВАГА: AUTO_PUBLISH_ENABLED=false — перший місяць бот лише"
    echo "надсилає прев'ю адмінам і НІКОЛИ не публікує сам о 08:00."
    echo "Увімкни autopublish в $ENV_FILE, коли довіра до алгоритму підтверджена."
}

main() {
    require_root
    require_apt

    echo "Встановлення vb-assistant-bot в $REPO_DIR"
    echo "Скрипт встановить системні пакети, створить окремого користувача '$SERVICE_USER'"
    echo "і зареєструє бота як systemd-службу з автозапуском після перезавантаження."
    read -rp "Продовжити? [Y/n]: " confirm
    if [[ "$confirm" =~ ^[Nn] ]]; then
        echo "Скасовано."
        exit 0
    fi

    install_packages
    ensure_service_user
    check_repo_dir
    setup_venv

    prompt_token
    prompt_admin_ids
    prompt_group_chat_id
    prompt_alerts_token
    prompt_timezone

    prepare_data_dir
    write_env_file
    write_unit_file
    enable_service
    print_summary
}

main "$@"
