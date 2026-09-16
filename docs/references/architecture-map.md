# Карта кода

Справочник для навигации: поиск по коду в GitHub не работает
(`[DEV-NO-CODE-SEARCH]`), поэтому файлы открываются по этой карте.

## Верхний уровень

| Путь | Что там |
| --- | --- |
| `app/main.py` | сборка приложения, подключение `ROUTERS` и безопасности |
| `app/config.py` | `Settings` — админский слой настроек |
| `app/security.py` | Basic, проверка источника, `SecurityMiddleware` |
| `app/deps.py` | общие зависимости и доступ к памяти |
| `app/core/` | предметная логика (29 модулей) |
| `app/state/` | состояние в памяти процесса |
| `app/web/` | роутеры, формы, сообщения, рендер |
| `app/services/` | длительные задачи (разбор загрузки) |
| `app/templates/`, `app/static/` | Jinja2-шаблоны без сборки |
| `companion/` | агент для сетевой папки на Windows |
| `deploy/` | юниты, скрипты, sudoers, пример окружения |
| `tests/` | 22 файла тестов, без сети |

## Предметная логика (`app/core/`)

| Задача | Модули |
| --- | --- |
| разбор и сборка файла | `parse.py`, `pipeline.py`, `format.py`, `style.py`, `repair.py`, `meta.py` |
| пересорт и сверка | `resort.py`, `prev.py`, `report.py`, `claims.py` |
| справочники | `refs.py`, `refs_sync.py` |
| проверка моделями | `verify.py`, `embed.py`, `judge.py`, `learning.py` |
| зрение | `vision.py`, `photo_mark.py` |
| почта | `mail.py`, `mail_match.py`, `mail_store.py`, `mail_vision.py` |
| служебное | `runtime.py`, `progress.py`, `schedule.py`, `guard.py`, `update.py` |

Самые крупные модули — `mail.py` (~43 КБ), `format.py` (~38 КБ),
`vision.py` (~37 КБ), `refs.py` (~30 КБ). При правке читается нужная
функция, а не весь файл.

## Маршруты (`app/web/routers/`)

| Файл | Главное |
| --- | --- |
| `upload.py` | `POST /upload`, `GET /upload/state/{билет}`, страница хода |
| `result.py` | `/result/{token}`, `/meta`, `/confirm`, `/claims`, `/download`, `/export`, `/cleanup` |
| `refs.py` | справочники и подтверждение значений |
| `training.py` | эмбеддинги, судья, проверки ключей |
| `vision.py` | ключ и проверка GigaChat |
| `mail.py` | `/mail`, `/mail/settings`, `/mail/check`, `/mail/fetch`, `/mail/parse/{uid}` |
| `companion.py` | `/api/companion/*` — билеты и выгрузки |
| `update.py` | `/update`, `/update/check`, `/update/apply`, `/update/state` |
| `health.py` | `/health` — единственный публичный путь |

## `[STATE-MEMORY]` Состояние в памяти

Зрелость: validated · Дата: 2026-09-16 · Источник: `app/state/`

`jobs`, `letters`, `exports`, `photos`, `refs_notes`, `samples`, `progress`
живут в памяти одного процесса. Следствия, которые нельзя
нарушать: служба работает в одном воркере; перезапуск теряет
билеты и письма (об этом предупреждает страница обновления);
долговременное хранится только в `/var/lib/excelkro`.

Пределы: `progress` — 20 билетов, хранится час, предупреждение
после 180 с; `exports` — 50 записей, сутки, состояния «ждёт» →
«забирается» → «выгружено»/«сбой».

## Продуктовый аналог SEK

У самого продукта есть такой же цикл обучения: ответы ревизора
(`store_answers`) → `app/state/samples.py` → `app/core/learning.py` →
автоподтверждение справочников (`refs_auto_confirm`,
`refs_confirm_min_score`). Знание агента и знание продукта
движутся по одной схеме: наблюдение → подтверждение → правило.
