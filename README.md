# VB Assistant

Telegram-бот ранкової підтримки команди після важких ночей (тривоги,
обстріли). Щоранку о 07:01 оцінює ніч за логом тривог alerts.in.ua,
і якщо вона була важкою (пороги — `config/thresholds.json`) — шле
адмінам прев'ю готового повідомлення з кнопками (Надіслати / Інший
варіант / Стриманий тон / Пропустити). «Надіслати» питає, коли
публікувати: зараз, о фіксованому часі (за замовчуванням 7:30) чи
довільно обраному — і лише тоді (або самостійно о 7:30, якщо ніхто не
відреагував на важку ніч — вимкнено за замовчуванням перший місяць)
публікує в груповий чат General.

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

## Команди й кнопки бота (особисті, лише адміни)

Постійна клавіатура внизу екрана дублює всі команди:

| Кнопка | Команда | Дія |
|---|---|---|
| 🌅 Прев'ю зараз | `/support` | прев'ю ранкового повідомлення зараз, навіть якщо ніч не визнана важкою |
| 📝 Своє повідомлення | `/custom` | власне повідомлення в General на обрані дату й час |
| 🗓 Заплановані | `/scheduled` | усе, що чекає на публікацію, з кнопками керування |
| 📋 Статус | `/status` | тип дня, стан прев'ю, автопублікація, свіжість даних про тривоги, заплановане, помилки |
| 🌴 Вихідний / 💼 Робочий сьогодні | `/markweekend [дд.мм.рррр]`, `/markworkday [дд.мм.рррр]` | ручна позначка типу дня (свята тощо) |
| 🕘 Історія | `/history` | хто що робив за останні 7 днів |
| ✏️ Тексти | `/texts` | додати / змінити / вимкнути варіанти ранкових текстів |
| ❌ Скасувати | `/cancel` | скинути незавершений ввід |
| ❓ Довідка | `/help` | довідка |

Усі адміни бачать, хто з них виконав дію (нікнейм у дужках). Під
опублікованим повідомленням — «✏️ Виправити текст» і «🗑 Видалити з
General» (видалення — до 48 год, обмеження Telegram).

Бот сам повідомляє адмінів: про помилку в боті й про повернення до
штатної роботи; якщо alerts.in.ua не відповідає понад 30 хв (і коли
дані знову оновлюються; у прев'ю — попередження про неповну
статистику); якщо своє повідомлення не вдалося опублікувати (і коли
таки опубліковано). Після оновлення кожен адмін один раз отримує
«🆕 Що нового» разом із новою клавіатурою (`whats_new.py` —
підніміть `VERSION` і перепишіть `TEXT` при наступних змінах для адмінів).

## Автопублікація (робочий режим)

`AUTO_PUBLISH_ENABLED=true` за замовчуванням: якщо по важкій ночі жоден
адмін не відреагував на прев'ю, о 7:30 (`autopublish_time` у
`config/thresholds.json`) бот сам публікує повідомлення в General.
Спокійна ніч без реакції — тиша. У суботу й неділю бот сам не
публікує, лише нагадує адмінам (ручні `/markweekend`/`/markworkday` на
це не впливають). `AUTO_PUBLISH_ENABLED=false` повертає
ручний режим тестового періоду (ТЗ п.13): лише нагадування адмінам.

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
