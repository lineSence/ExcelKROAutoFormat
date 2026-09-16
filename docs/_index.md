# L1. Оглавление знаний

Резервный уровень: читать, если `docs/_routing.md` не дал совпадения.
Путь — одна строка о содержании.

## Ядро (L0)

| Файл | Что внутри |
| --- | --- |
| `agents_context.md` | Конституция: core-правила, категории знаний, цикл SEK |
| `docs/00-agents-context.md` | Протокол SEK: стадии знания, промоут, отзыв |

## Маршрутизация (L1)

| Файл | Что внутри |
| --- | --- |
| `docs/_routing.md` | Ключевые слова → файлы, YAML |
| `docs/_index.md` | Этот список |

## Правила (L2, грузятся по триггеру)

| Файл | Теги | Что внутри |
| --- | --- | --- |
| `docs/rules/domain.md` | `DOM-*` | Предметная область: столбцы, раскраска, пороги, шапка |
| `docs/rules/web.md` | `WEB-*` | Роутеры, шаблоны, формы, редиректы, фоновые задачи |
| `docs/rules/security.md` | `SEC-*` | Охрана доступа, Basic, источник запроса, токены |
| `docs/rules/settings.md` | `CFG-*` | `runtime.json`, `Settings`, маскирование ключей |
| `docs/rules/external-models.md` | `AI-*` | GigaChat, OpenRouter, эмбеддинги, почта, пределы трат |
| `docs/rules/deploy-ota.md` | `OTA-*` | Служба, обновление по кнопке, права файлов |
| `docs/rules/agent-workflow.md` | `DEV-*` | Коммиты, тесты, документация, грабли инструментов |

## Справочники (L2)

| Файл | Что внутри |
| --- | --- |
| `docs/references/architecture-map.md` | Карта кода и состояние в памяти |
| `docs/references/known-issues.md` | Архив грабель: симптом → причина → тег правила |
| `docs/01-input-1c.md` | Формат выгрузки 1С |
| `docs/02-output-format.md` | Оформление готового файла |
| `docs/03-transform-rules.md` | Правила преобразования |
| `docs/04-open-questions.md` | Открытые вопросы к владельцу |
| `docs/05-architecture.md` | Устройство приложения |
| `docs/06-resort-rules.md` | Пересорты: схожесть и группы |
| `docs/07-ml-verifier.md` | Второй слой: регрессия, эмбеддинги, LLM-судья |
| `docs/08-claims-registry.md` | Заявки и реестр |
| `docs/09-ota-update.md` | Обновление по кнопке |
| `docs/10-photo-vision.md` | Зрение и распознавание фото |
| `docs/11-mail.md` | Почта ревизоров |
| `docs/12-progress.md` | Ход разбора сверки |
| `docs/13-companion.md` | Программа-компаньон |
| `docs/13-ui-and-settings.md` | Интерфейс и пользовательские настройки |

## Память (L3)

| Файл | Что внутри |
| --- | --- |
| `docs/memory/inbox.md` | Черновые наблюдения агента (не больше 50) |
| `docs/memory/journal.md` | Журнал промоутов и отзывов правил |
| `docs/backlog.md` | Отложенные задачи |
