# Worker

Локальная версия выполняет короткую обработку чата через FastAPI BackgroundTasks, а экспорт создаёт потоковый приватный файл. Для серверного размещения этот модуль выносится в отдельный процесс очереди без изменения HTTP-контракта.

Операционные команды базы знаний сейчас доступны через API-пакет:

```bash
cd apps/api
uv run python -m app.cli kb import --source ../../knowledge/canonical_source.txt --season 2025-2026
uv run python -m app.cli kb validate --version VERSION_ID
uv run python -m app.cli kb publish --version VERSION_ID
uv run python -m app.cli kb rollback --to VERSION_ID
```
