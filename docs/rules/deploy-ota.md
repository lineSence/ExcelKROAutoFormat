# Правила: развёртывание и обновление (`OTA-*`)

Код — `deploy/*`, `app/core/update.py`, `app/web/routers/update.py`.
Подробности — `docs/09-ota-update.md`.

## `[OTA-ROOT]` Служба работает от root — это учтено осознанно

Зрелость: validated · Дата: 2026-09-16 · Источник: `deploy/app.service`

При работе от root `sudo` не нужен, поэтому вызовы идут через
`update.as_root()`, который сам решает, нужна ли приставка. Новый
привилегированный вызов — только через эту функцию.

## `[OTA-UNIT]` Обновление делает отдельный юнит, а не сама служба

Зрелость: core · Дата: 2026-09-16 · Источник: `deploy/excelkro-update@.service`

Процесс не может перезапускать себя: при `systemctl restart`
собственной службы он умирает посреди работы. Поэтому
`/update/check` и `/update/apply` только запускают
`excelkro-update@check|apply` через `--no-block` и сразу отвечают.
Шаги скрипта — `git pull --ff-only`, `bash deploy/install-ota.sh`,
`systemctl restart excelkro`; ход пишется в `update-state.json` и
`update.log` в `/var/lib/excelkro`.

## `[OTA-EXEC-BIT]` Скрипты запускаются через `bash`

Зрелость: core · Дата: 2026-09-16 · Источник: грабли

Право на запуск не сохраняется надёжно при переносе файлов,
поэтому везде явно `bash …/script.sh`, а в юните —
`ExecStart=/bin/bash …/ota-update.sh %i`. Полагаться на `+x` нельзя.

## `[OTA-SUDO-NARROW]` Разрешения sudo точечные

Зрелость: core · Дата: 2026-09-16 · Источник: `deploy/sudoers-excelkro`

В `Cmnd_Alias EXCELKRO_OTA` ровно два разрешённых запуска юнитов.
Расширять список — только с решением владельца, см.
`[SEC-OTA-SUDO]`.

## `[OTA-STUCK]` Зависание при остановке — известное поведение

Зрелость: validated · Дата: 2026-09-16 · Источник: грабли

Фоновые задачи не отменяются, и служба застревает в
`deactivating (stop-sigterm)`. Лечение сейчас —
`systemctl kill -s SIGKILL excelkro`. Правильное решение (отмена
задач плюс `TimeoutStopSec`) — в `docs/backlog.md`.

## `[OTA-STATE-OUTSIDE]` Обновление не должно терять настройки

Зрелость: core · Дата: 2026-09-16 · Источник: `[CFG-STATE-PATH]`

Код лежит в `/opt/excelkro` и перезаписывается, состояние и
настройки — в `/var/lib/excelkro` и переживают обновление.
Проверка после обновления:
`systemctl show -p User -p ExecStart --value excelkro`.
