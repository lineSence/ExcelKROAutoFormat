# Журнал промоутов и отмен

Одна строка на событие. Записи не удаляются — видно, как
знание выросло и что владелец отменил.

Формат: `YYYY-MM-DD · действие · [TAG] · файл · основание`.
Действия: `promote` (в `validated`), `core` (по «да» владельца),
`revoke` (по `/revoke [TAG]`), `retire` (правило устарело).

## Записи

- 2026-09-16 · core · весь набор `DOM-*`, `WEB-*`, `SEC-*`, `CFG-*`,
  `AI-*`, `OTA-*`, `DEV-*` · `docs/rules/` · перенос старого
  `agents_context.md` в структуру SEK, ответы владельца от 16.09.2026.
- 2026-09-16 · core · `[SEC-AUTH-ALWAYS]` · `docs/rules/security.md` ·
  решение владельца: Basic всегда, кроме `/health`.
- 2026-09-16 · promote · `[SEC-COMPANION-TOKEN]` · `docs/rules/security.md` ·
  решение владельца: отдельный токен вместо пропуска без `Origin`.
- 2026-09-16 · core · `[CFG-TWO-LAYERS]` · `docs/rules/settings.md` ·
  уточнение старой формулировки «`.env` не используется».
- 2026-09-16 · promote · `[ENV-SYNC]` · `docs/rules/settings.md` ·
  аудит нашёл пять расхождений между `Settings` и примером окружения.
- 2026-09-16 · core · `[DEV-TESTS-REQUIRED]`, `[DEV-RULE-FORMAT]` ·
  `docs/rules/agent-workflow.md` · решения владельца от 16.09.2026.
