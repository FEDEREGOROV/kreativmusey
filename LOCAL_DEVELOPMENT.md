# Локальная разработка

Это локальная полнофункциональная версия сервиса. Публичный чат, история, импорт базы, FAQ, вход администратора, диалоги, расходы и экспорт работают через FastAPI и локальную SQLite. Интеграция Responses API реализована, но без ключа используется явно помеченный локальный поиск. Для серверного размещения предусмотрены PostgreSQL/pgvector и `docker-compose.yml`.

## Запуск в Visual Studio Code

1. Откройте `creative-museum.code-workspace` в Visual Studio Code.
2. Разрешите выполнение автоматической задачи `Dev: всё приложение` или запустите её через `Terminal → Run Task`.
3. Откройте <http://127.0.0.1:5173>.

API доступен на <http://127.0.0.1:8000/api/health>, документация FastAPI — на <http://127.0.0.1:8000/docs>.

Локальный вход администратора: `admin` / `konkursadmin`.

## Ручной запуск

В первом терминале:

```bash
cd apps/api
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Во втором терминале:

```bash
cd apps/web
PATH="$PWD/../../.tools/node/bin:$PATH" npm run dev
```

## Проверка

```bash
cd apps/api
uv run python -m pytest
PATH="$PWD/../../.tools/node/bin:$PATH" npm --prefix ../web run build
```

## Реальный OpenAI API

Создайте `.env` в корне проекта:

```dotenv
DEMO_MODE=false
OPENAI_API_KEY=ваш_новый_ключ
OPENAI_MODEL=gpt-6-luna
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
ADMIN_PASSWORD=уникальный_пароль
SESSION_SECRET=случайная_длинная_строка
ACTIVE_CONTEST_SEASON=утвержденный_сезон
```

Ключ используется только сервером. Без него приложение продолжает работать в локальном demo-режиме.

## База знаний

```bash
cd apps/api
uv run python -m app.cli kb import --source ../../knowledge/qa_source.txt --season 2025-2026
uv run python -m app.cli kb validate --version VERSION_ID
uv run python -m app.cli kb publish --version VERSION_ID
uv run python -m app.cli kb rollback --to VERSION_ID
```

## PostgreSQL для будущего сервера

После установки Docker запустите `docker compose up -d postgres` и задайте `DATABASE_URL=postgresql+psycopg://app:change_me@localhost:5432/creative_museum`. Миграции: `cd apps/api && uv run alembic -c alembic.ini upgrade head`.

Портативный Node находится в `.tools/node` и исключён из Git. Он добавлен только потому, что системный Node на компьютере отсутствовал.
