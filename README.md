# VB Assistant

Telegram-бот ранкової підтримки команди після важких ночей (тривоги,
обстріли). Щоранку о 07:01 оцінює ніч за логом тривог alerts.in.ua,
і якщо вона була важкою (пороги — `config/thresholds.json`) — шле
адмінам прев'ю готового повідомлення з кнопками (Надіслати / Інший
варіант / Стриманий тон / Пропустити), і лише після їхньої дії (або
самостійно о 08:00 — вимкнено за замовчуванням перший місяць) публікує
в груповий чат General.

Вимоги: `docs/BRD.md` → `docs/HLD.md` → `docs/LLD.md` → код.

## Встановлення на сервер

Одна команда на чистому Ubuntu-сервері (`sudo bash deploy/install.sh`)
ставить усе потрібне й запускає бота автозапуском через systemd —
повна покрокова інструкція в **[docs/INSTALL.md](docs/INSTALL.md)**.

## Налаштування (локальний запуск)

1. Створити бота через [@BotFather](https://t.me/BotFather), отримати
   `TELEGRAM_TOKEN`.
2. Отримати ключ [alerts.in.ua](https://alerts.in.ua/) (лист на
   api@alerts.in.ua).
3. Скопіювати `.env.example` → `.env`, заповнити (токен, мінімум двоє
   `ADMIN_USER_IDS`, `GROUP_CHAT_ID`, `ALERTS_API_TOKEN`).
4. `pip install -r requirements.txt`
5. `python -m vb_assistant_bot`

Тексти повідомлень і пороги тригера — `config/texts.json` і
`config/thresholds.json`, редагуються без розробника.

## Розробка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .      # vb_assistant_bot імпортується з src/ у тестах
ruff format . && ruff check . && pytest -v
```

CI (`.github/workflows/ci.yml`) прогонить те саме на кожен push/PR.

## Команди бота (особисті, лише адміни)

- `/support` — прев'ю ранкового повідомлення просто зараз, навіть якщо
  алгоритм не визнав ніч важкою.
- `/markweekend [дд.мм.рррр]`, `/markworkday [дд.мм.рррр]` — вручну
  позначити дату вихідним/робочим днем (свята тощо); без дати —
  сьогодні.

## Перший місяць — ручний режим

`AUTO_PUBLISH_ENABLED=false` за замовчуванням: о 08:00 без реакції на
прев'ю бот лише нагадує адмінам, ніколи не публікує сам. Свідоме
рішення ТЗ — перевірити точність тригера, перш ніж довіряти
автопублікацію.

## Ручний деплой (systemd, без install.sh)

```bash
sudo mkdir -p /opt/vb-assistant-bot /etc/vb-assistant-bot
sudo cp -r . /opt/vb-assistant-bot
cd /opt/vb-assistant-bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp .env.example /etc/vb-assistant-bot/env   # заповнити реальними значеннями, chmod 600
sudo cp deploy/systemd/vb-assistant-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vb-assistant-bot
systemctl status vb-assistant-bot
journalctl -u vb-assistant-bot -f
```
