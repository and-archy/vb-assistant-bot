# Встановлення VB Assistant на сервер

Інструкція — для людини без досвіду адміністрування Linux. Кожен крок
можна скопіювати й вставити в термінал по черзі.

## Що знадобиться

1. **Сервер на Ubuntu**, доступ по SSH.
2. **Токен бота від Telegram** — [Крок 1](#крок-1-створити-бота-в-telegram).
3. **Telegram ID щонайменше двох адмінів** — [Крок 2](#крок-2-дізнатись-telegram-id-адмінів).
4. **Chat ID групи General** — [Крок 3](#крок-3-дізнатись-chat-id-групи-general).
5. **Ключ alerts.in.ua** — [Крок 4](#крок-4-отримати-ключ-alertsinua).

## Крок 1. Створити бота в Telegram

1. Знайди в Telegram **@BotFather**, напиши `/newbot`.
2. Дай боту ім'я та username, що закінчується на `bot` (наприклад,
   `vb_assistant_bot`).
3. BotFather надішле **токен** — довгий рядок виду
   `123456789:AAHkxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Збережи його,
   нікому не показуй.

## Крок 2. Дізнатись Telegram ID адмінів

1. Знайди бота **@userinfobot**, напиши `/start`.
2. Він надішле твій **Id** — число. Це і є твій Telegram user ID.
3. Потрібно щонайменше двоє адмінів (щоб не залежати від однієї
   людини) — попроси другу людину так само дізнатись свій ID.

## Крок 3. Дізнатись Chat ID групи General

1. Додай бота **@getidsbot** у групу General (потім можна видалити).
2. Він одразу напише в групу ID чату — від'ємне число, наприклад
   `-1001234567890`.
3. Додай свого бота (з Кроку 1) у групу General з правом писати
   повідомлення, після чого @getidsbot можна прибрати.

## Крок 4. Отримати ключ alerts.in.ua

Напиши на **api@alerts.in.ua** із проханням про безкоштовний ключ для
розробників — на момент написання ТЗ це стандартна практика проєкту.
У відповідь отримаєш токен для заголовка `Authorization: Bearer`.

## Крок 5. Підключитись до сервера

```bash
ssh твій_користувач@ip-адреса-сервера
```

## Крок 6. Завантажити бота на сервер

```bash
sudo apt-get update
sudo apt-get install -y git
sudo git clone <URL-цього-репозиторію> /opt/vb-assistant-bot
cd /opt/vb-assistant-bot
```

## Крок 7. Запустити встановлення

```bash
sudo bash deploy/install.sh
```

Скрипт запитає підтвердження, тоді сам:

- встановить системні пакети (Python, git, curl);
- створить окремого системного користувача (не root);
- підготує ізольоване Python-оточення;
- запитає **токен бота**, **ID адмінів** (мінімум двоє), **Chat ID
  групи General**, **ключ alerts.in.ua** (і uid регіону — Enter для
  дефолтного `31`, м. Київ), **часовий пояс** (Enter для `Europe/Kyiv`);
- зареєструє бота як systemd-службу з автозапуском.

## Крок 8. Перевірити роботу

У Telegram напиши боту `/start` в особисті — має відповісти довідкою
(якщо твій ID у списку адмінів). Виклич `/support` — має прийти прев'ю
зі статистикою ночі, готовим текстом і кнопками, навіть якщо тривог
не було.

## Перший місяць — лише ручний режим

За замовчуванням `AUTO_PUBLISH_ENABLED=false`: о 08:00, якщо прев'ю
без реакції, бот лише нагадує адмінам і **ніколи не публікує сам**.
Це свідоме рішення ТЗ — дати переконатись, наскільки точно спрацьовує
алгоритм тригера, перш ніж довіряти йому автопублікацію. Вмикати:

```bash
sudo nano /etc/vb-assistant-bot/env
# AUTO_PUBLISH_ENABLED=true
sudo systemctl restart vb-assistant-bot
```

## Як редагувати тексти й пороги без розробника

- `config/texts.json` — тексти всіх трьох наборів і стабільні блоки.
- `config/thresholds.json` — пороги тригера «важкої ночі», час
  прев'ю/автопублікації, інтервал опитування alerts.in.ua.

Обидва файли — прості JSON, редагуються `nano`. Після зміни:

```bash
cd /opt/vb-assistant-bot
sudo nano config/thresholds.json    # або config/texts.json
sudo systemctl restart vb-assistant-bot
```

Пороги майже напевно доведеться підкрутити після місяця роботи —
дивись реальну статистику ночей у БД (`send_log`, `alerts`).

## Як позначити свято вихідним днем

```
/markweekend 25.12.2026
```

Адмінська команда в особистих повідомленнях боту (без дати — сьогодні).
Прибрати позначку — `/markworkday` з тією самою датою.

## Як змінити налаштування пізніше

```bash
sudo nano /etc/vb-assistant-bot/env
sudo systemctl restart vb-assistant-bot
```

Або запусти `sudo bash deploy/install.sh` ще раз у
`/opt/vb-assistant-bot` — перепитає все і перезапише (безпечно).

## Як оновити бота

```bash
cd /opt/vb-assistant-bot
sudo git pull
sudo systemctl restart vb-assistant-bot
```

## Де зберігаються дані

- `/opt/vb-assistant-bot/data/vb_assistant.db` — лог тривог, історія
  ротації текстів, стан прев'ю, лог відправок.

Не в git, не губиться при `git pull`.

## Корисні команди

```bash
systemctl status vb-assistant-bot
journalctl -u vb-assistant-bot -f
journalctl -u vb-assistant-bot --since today
sudo systemctl restart vb-assistant-bot
sudo systemctl stop vb-assistant-bot
```

## Типові проблеми

**Бот не відповідає в Telegram.**
`systemctl status vb-assistant-bot` — якщо `Active: failed`, дивись
`journalctl -u vb-assistant-bot -n 50`. Найчастіше — неправильний
токен чи `ALERTS_API_TOKEN`.

**"Немає доступу" на команду в особистих.**
Твій Telegram ID не в `ADMIN_USER_IDS` (`/etc/vb-assistant-bot/env`).

**Бот не публікує в General.**
Перевір, що бот доданий у групу з правом писати повідомлення, і що
`GROUP_CHAT_ID` в env — саме той чат (від'ємне число).

**Прев'ю приходить, але о 08:00 нічого не публікується без натискань.**
Це очікувано, поки `AUTO_PUBLISH_ENABLED=false` (дефолт першого
місяця) — див. розділ вище.

**Хочу повністю прибрати бота із сервера.**
```bash
sudo systemctl disable --now vb-assistant-bot
sudo rm /etc/systemd/system/vb-assistant-bot.service /etc/vb-assistant-bot/env
sudo systemctl daemon-reload
sudo rm -rf /opt/vb-assistant-bot
```
