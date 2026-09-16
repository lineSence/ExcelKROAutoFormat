# L1. Маршрутизация знаний

Машинно-читаемая таблица «ключевые слова → файлы». Агент читает её, если
задача не покрыта ядром из `agents_context.md`. Формат — YAML, чтобы
разбор не зависел от толкования прозы.

Правило чтения: совпало ключевое слово — грузим все файлы из `load`.
Ничего не совпало — смотрим `docs/_index.md`.

```yaml
triggers:
  - id: domain
    keywords: [сверка, столбец, излишек, недостача, пересорт, раскраска, рамка, не+, 1С]
    load:
      - docs/rules/domain.md
      - docs/01-input-1c.md
      - docs/02-output-format.md
      - docs/03-transform-rules.md

  - id: sellers
    keywords: [продавец, продавцы, часы, файл продавцов, доли, share_mode, sellers]
    load:
      - docs/rules/domain.md
      - docs/01-input-1c.md
      - docs/references/architecture-map.md

  - id: resort
    keywords: [схожесть, similarity, группа, порог, mixed_score, logic_weight, влияние логики]
    load:
      - docs/rules/domain.md
      - docs/06-resort-rules.md

  - id: verify-ml
    keywords: [verify, judge, LLM, эмбеддинги, embed, OpenRouter, обучение, модель, регрессия]
    load:
      - docs/rules/external-models.md
      - docs/07-ml-verifier.md

  - id: vision
    keywords: [фото, снимок, зрение, GigaChat, распознавание, нет фото, photo_mark]
    load:
      - docs/rules/external-models.md
      - docs/10-photo-vision.md

  - id: mail
    keywords: [почта, письмо, IMAP, ящик, ревизор, вложение, папка, mail_match]
    load:
      - docs/rules/external-models.md
      - docs/11-mail.md

  - id: web
    keywords: [маршрут, роутер, шаблон, форма, редирект, token, nav_items, Jinja, страница]
    load:
      - docs/rules/web.md
      - docs/references/architecture-map.md

  - id: progress
    keywords: [ход разбора, билет, этап, полоса, фоновая задача, upload, progress]
    load:
      - docs/rules/web.md
      - docs/12-progress.md

  - id: security
    keywords: [авторизация, Basic, Origin, Referer, CSRF, туннель, health, доступ]
    load:
      - docs/rules/security.md
      - docs/05-architecture.md

  - id: settings
    keywords: [настройки, runtime.json, Settings, config, env, ключ, пароль, маска]
    load:
      - docs/rules/settings.md
      - docs/13-ui-and-settings.md

  - id: companion
    keywords: [компаньон, companion, выгрузка, очередь, exports, папка, .part, трей]
    load:
      - docs/rules/web.md
      - docs/13-companion.md

  - id: deploy-ota
    keywords: [развёртывание, обновление, OTA, ветка, systemd, unit, sudoers, перезапуск]
    load:
      - docs/rules/deploy-ota.md
      - docs/09-ota-update.md

  - id: refs
    keywords: [справочник, шапка, подпись, REFS_CELL, причина, проверяющий, ревизоры]
    load:
      - docs/rules/domain.md
      - docs/rules/settings.md

  - id: agent-work
    keywords: [коммит, тест, pytest, ruff, документация, инструмент, push_files, search_code]
    load:
      - docs/rules/agent-workflow.md
      - docs/memory/inbox.md

  - id: debug
    keywords: [ошибка, симптом, не работает, падает, Internal server error, зависла, грабли]
    load:
      - docs/references/known-issues.md
      - docs/memory/inbox.md

  - id: knowledge
    keywords: [SEK, правило, промоут, promote, revoke, inbox, память, знания]
    load:
      - docs/00-agents-context.md
      - docs/memory/inbox.md
```
