# Отложенные задачи

Здесь только то, что решено сделать, но пока не сделано.
Спорное и неподтверждённое живёт в `docs/memory/inbox.md`.

## Безопасность

- `SEC-AUTH-ALWAYS` — требовать Basic независимо от `app_host`,
  единственное исключение — `/health`. Правится `_auth_required` в
  `app/security.py` + тест в `tests/test_security.py`.
- `SEC-COMPANION-TOKEN` — ввести отдельный токен компаньона в
  заголовке и убрать разрешение POST без `Origin`/`Referer`.
  Затрагивает `app/security.py`, `companion/client.py`, интерфейс
  настроек и `tests/test_companion.py`.

## Настройки

- `ENV-SYNC` — привести `deploy/.env.example` к `Settings`
  (`REFS_CELL_REASON=C6`, `REFS_CELL_ADMIN=L3`, `REFS_CELL_CHECKER=`,
  `REFS_CELL_AUDITORS=L5`, `TMP_DIR=/tmp/excelkro`) и добавить тест,
  сравнивающий ключи и дефолты двух файлов.

## Разработка

- `DEV-RUFF-CI` — добавить шаг `ruff check .` в
  `.github/workflows/ci.yml` и согласовать `target-version` с версией
  Python в CI.
- `DEV-DOC-13` — развести номера `docs/13-companion.md` и
  `docs/13-ui-and-settings.md`.
- Удалить слитые ветки `fix/p0-hardening`, `refactor/main-split`,
  `split`.

## Надёжность и обновление

- `OTA-STUCK` — отменять фоновые задачи при остановке и задать
  `TimeoutStopSec=20…30` в `deploy/app.service`.
- Прогнать `pytest` и `ruff` на живой машине и закрыть хвосты.

## Продукт

- Упомянуть зрение, почту и компаньона в `README.md`.
- Тесты на `/api/companion/*` и раздел `/mail`.
- `.exe` компаньона и автозапуск.
- Блок со снимками на странице результата.
- Выбор папки почты списком вместо ручного ввода.
- Тест сохранения остальных снимков письма.
- Проверить OpenRouter `chat/completions` на живом ключе.
