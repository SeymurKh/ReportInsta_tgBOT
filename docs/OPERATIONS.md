# Операции на проде

Короткая шпаргалка по серверу, добавлению аккаунтов и проверкам. Все команды
безопасны для чтения, если не помечены «изменяет».

## 1. Доступ

- Сервер: `root@49.13.20.118` (`ubuntu-4gb-fsn1-1`, Ubuntu 26.04).
- SSH-ключ: `~/.ssh/reportinsta_ed25519`, алиас в `~/.ssh/config`:
  ```bash
  ssh reportinsta
  ```
- Скрипты аудита используют `audit_snapshot/ssh.py`: сначала `SERVER_SSH_KEY`,
  затем дефолтный ключ, затем `SERVER_SSH_PASSWORD` (fallback, в репозитории
  не хранится).
- Проверка доступа: `python audit_snapshot/check_whitelist.py`.

## 2. Что где лежит

| Что | Путь |
|---|---|
| Код | `/opt/reportinsta` (не git-репозиторий — деплой копированием) |
| Venv | `/opt/reportinsta/.venv` (Python 3.14) |
| Сервис | `systemd` unit `reportinsta`, пользователь `reportbot` |
| БД (истина) | `/opt/reportinsta/data/instagram_bot.db` (SQLite, WAL) |
| Конфиг | `/opt/reportinsta/.env` (600, `reportbot:reportbot`) |
| Логи | `/opt/reportinsta/logs/bot.log` |
| Бэкапы | `/opt/reportinsta/data/backups` (ретеншен 14 дней) |

## 3. Добавление Instagram-аккаунта

Аккаунты живут в `.env` (`INSTAGRAM_ACCOUNTS`), при старте `seed_accounts()`
синхронизирует их в таблицу `accounts`.

1. Preflight токена (read-only):
   ```
   GET https://graph.instagram.com/v25.0/me?fields=user_id,username,name,account_type,followers_count
   ```
   Нужен Business/Creator профиль, long-lived токен (~60 дней).
2. Запись в прод-`.env` (изменяет, dry-run без `--apply`):
   ```powershell
   $env:NEW_IG_TOKEN = "<token>"
   python audit_snapshot/add_account.py --name <handle> --user-id <ig_user_id> --apply
   ```
   (bash: `export NEW_IG_TOKEN=<token>`)
   Скрипт делает бэкап `.env.bak-account-<stamp>`, сохраняет владельца/права и
   валидирует конфиг через `settings.validate()`; при ошибке откатывает `.env`.
3. Рестарт и первичный синк:
   ```bash
   ssh reportinsta "systemctl restart reportinsta"
   ssh reportinsta "tail -20 /opt/reportinsta/logs/bot.log"   # Account @... saved to DB
   ```
   Стартовый цикл синка сам заполняет 30 дней. Сторис задним числом не
   собираются — только с момента добавления.
4. Проверка:
   ```bash
   ssh reportinsta "cd /opt/reportinsta && .venv/bin/python scripts/verify_api_vs_db.py --days 14 --accounts <handle>"
   ```
   Расхождения по «сегодня» и счётчику подписчиков — ожидаемая живая динамика.

## 4. Аудит и снапшоты

- Снапшот прод-БД (read-only на сервере, временный файл удаляется):
  `python audit_snapshot/fetch_snapshot.py`
- Аудит отчётов/сравнений на снапшоте (AI и API замоканы):
  `python scripts/audit_reports.py --db audit_snapshot/server_snapshot.db [--accounts name]`
- Живая сверка API↔БД: `scripts/verify_api_vs_db.py`
- Разовая синхронизация: `scripts/sync_check.py --days 30 [--accounts name]`
- Добор истории публикаций (watch time, просмотры фото/каруселей):
  `scripts/backfill_post_insights.py --missing-only` — 1 запрос на пост,
  не упирается в лимит API
- Тесты: `python -m pytest -q`

## 5. Google Sheets (выгрузка отчётов)

Под отчётом есть кнопка **«☁️ В Google Sheets»**: бот берёт тот же Excel,
извлекает значения листов и отправляет их в Google Apps Script Web App,
который создаёт Google-таблицу в папке.

Настройка `.env`:
```
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_WEBAPP_URL=https://script.google.com/macros/s/.../exec
GOOGLE_SHEETS_WEBAPP_TOKEN=<общий секрет со скриптом>
GOOGLE_SHEETS_TIMEOUT=60
GOOGLE_SHEETS_MODE=convert
GOOGLE_SHEETS_FOLDER_URL=https://drive.google.com/drive/folders/<folder_id>
```

`GOOGLE_SHEETS_MODE=convert` отправляет сам `.xlsx`: Google импортирует его и
сохраняет оформление (заливки, шрифты, границы, объединения, ширины). Для этого
в Apps Script нужно добавить сервис **Drive API** (Services → Drive API → Add).
`values` — резервный режим: только значения и простая верстка. Если импорт
недоступен, бот автоматически откатывается на `values`.

Скрипт (Google Apps Script): `doPost` принимает `{token, title, sheets:[{name, rows}]}`,
создаёт таблицу, переносит её в папку и возвращает `{url}`. Деплой:
**Deploy → New deployment → Web app**, `Execute as: Me`, `Who has access: Anyone`.
Токен должен совпадать с `GOOGLE_SHEETS_WEBAPP_TOKEN`. URL веб-приложения —
секрет: хранится только в `.env` (`600`, `reportbot`).

Проверка: `GET <webapp_url>` → `{"ok":true}`; кнопка в боте создаёт таблицу
и присылает ссылку. Оформление базовое (шапка, закреплённая строка, числа),
данные и структура листов совпадают с Excel.

## 6. Токены Instagram

- Авто-refresh в коде отсутствует: long-lived токен живёт ~60 дней.
- Обновлять вручную (Meta dashboard или `grant_type=ig_refresh_token`) и
  заменять в `.env`; `add_account.py` умеет обновить токен уже добавленного
  аккаунта (повторный запуск с тем же `--user-id`).
- Истечение токена бот сообщает админу в Telegram (`TokenExpiredError`).
- Массовая замена токенов (несколько страниц сразу, один бэкап и одна
  валидация; аккаунты вне списка не меняются):
  ```powershell
  @' {"room.airport": "IGAA...", "cms.space": "IGAA..."} '@ | python audit_snapshot/replace_account_tokens.py --local --apply --tokens-file -
  @' {...} '@ | python audit_snapshot/replace_account_tokens.py --remote --apply --tokens-file -
  ```
  Ключи — `name` из `INSTAGRAM_ACCOUNTS`; секреты читаются из stdin (`-`), не
  из файла. Скрипт сверяет каждый токен через `GET /me` с `user_id` записи и
  при несовпадении ничего не пишет, печатает только маску токена, а после
  записи проверяет, что «чужие» аккаунты не изменились, и валидирует
  `.env` через `settings.validate()`. Без `--apply` — dry run; `--remote`
  требует рестарта сервиса.

## 7. Деплой изменений

Git на сервере нет. Порядок: скопировать изменённые файлы (SFTP), проверить
контрольную сумму, затем `systemctl restart reportinsta` и посмотреть лог.
Пример точечной выкладки — `audit_snapshot/deploy_ai_analyzer.py`.

## 8. Безопасность

- Секреты не коммитятся: `.env`, `.env.bak-*`, `*.db`, `*.log` — в `.gitignore`.
- Права: `.env` и его бэкапы — `600`, владелец `reportbot`.
- Root-пароль, попавший в переписку/логи, менять; вход — по ключу.
- Локальный `.env` — отдельный тестовый бот, прод не дублировать: один polling
  на токен.
