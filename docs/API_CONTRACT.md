# HTTP API контракт

Базовый префикс `/api/v1`. JSON UTF-8. UUID — внешние IDs. UTC ISO 8601 — хранение и обмен временем, Europe/Moscow — отображение. Все лимиты и права проверяются сервером. Схемы ниже обязательны по смыслу; OpenAPI из FastAPI должен отражать фактический контракт.

## Общие правила

Cookie создает анонимную или административную сессию. Не возвращать пароль и session_token в JSON. Для административных мутаций передавать CSRF-токен. Ошибка:

```json
{"error":{"code":"VALIDATION_ERROR","message":"Проверьте длину вопроса","trace_id":"uuid","retryable":false}}
```

Коды: VALIDATION_ERROR 422; UNAUTHENTICATED 401; FORBIDDEN 403; NOT_FOUND 404; CONFLICT 409; RATE_LIMITED 429 с Retry-After; BUDGET_EXCEEDED 429 с отдельным code; SERVICE_UNAVAILABLE 503. Чужую беседу допустимо возвращать как 404, не раскрывая ее существование. Ошибка провайдера оборачивается в безопасный код.

Пагинация списков: `items`, `next_cursor`, `has_more`; курсор непрозрачный, проверяется сервером. `limit` по умолчанию 50, максимум 100. Для отчетов можно возвращать дополнительно агрегаты, но сумма и список должны использовать один фильтр.

## Публичные методы

| Метод | Путь | Результат |
|---|---|---|
| GET | /session | Создать/вернуть сессию, текущую беседу, CSRF при необходимости; никаких чужих IDs |
| GET | /public/config | Название, контакты, ссылки, capabilities, demo flag; без секретов и внутренних промптов |
| GET | /faqs | Только опубликованные FAQ; featured и season фильтруются сервером |
| POST | /conversations | Новая беседа текущей сессии |
| GET | /conversations/{id}/messages | Сообщения владельца, курсор, порядок oldest-first |
| POST | /conversations/{id}/messages | Сохранить вопрос и запустить запрос, вернуть 202 |
| GET | /chat-requests/{id} | Статус обработки и итог для владельца |
| POST | /chat-requests/{id}/retry | Повтор допустимой неуспешной попытки без нового user-message |
| PUT | /messages/{id}/feedback | helpful / unhelpful / null, только на ответ в своей беседе |

Отправка:

```json
{"text":"А самозанятый может быть руководителем?","client_message_id":"uuid"}
```

`Idempotency-Key` соответствует операции; повтор того же ключа с другим телом дает CONFLICT. Ответ 202:

```json
{"request_id":"uuid","message_id":"uuid","status":"queued","poll_after_ms":750}
```

Состояние:

```json
{
  "id":"uuid",
  "status":"completed",
  "result":"answered",
  "assistant_message":{
    "id":"uuid","role":"assistant","text":"Подтвержденный ответ",
    "created_at":"2026-10-20T07:00:00Z","source_ids":["chunk-uuid"],
    "sources":[{"id":"chunk-uuid","title":"Руководитель проекта","section":"Участники","url":null}]
  },
  "error":null
}
```

`status`: queued, processing, completed, failed, blocked. `result` у completed: answered, clarification, no_answer, off_topic. Нет ответа GPT — нет assistant_message с выдуманным содержимым. Показ безопасной технической ошибки не требует новой реплики в БД. После восстановления сервера не оставлять requests в processing навсегда: lease и recovery worker обязательны.

## Авторизация администратора

| Метод | Путь | Действие |
|---|---|---|
| POST | /admin/auth/login | Проверить username и password, установить cookie, ротировать session ID |
| GET | /admin/auth/me | Проверить сессию, вернуть имя и права |
| POST | /admin/auth/logout | Завершить сессию на сервере и очистить cookie |

Неправильный логин и неправильный пароль дают одинаковое безопасное сообщение. Не включать пароль в query-string, audit log и аналитические события.

## Диалоги и экспорт

| Метод | Путь | Параметры |
|---|---|---|
| GET | /admin/conversations | q, from, to, result, feedback, channel, cursor, limit |
| GET | /admin/conversations/{id} | Метаданные и отдельная постраничная история |
| POST | /admin/exports | Создать snapshot-job |
| GET | /admin/exports/{id} | queued/processing/ready/failed/expired, число и прогресс |
| GET | /admin/exports/{id}/download | Проверить текущую admin-сессию, срок и существование файла |

Создание экспорта:

```json
{
  "format":"csv",
  "scope":"messages",
  "selection":{"mode":"last","limit":1000},
  "filters":{"from":"2026-10-01T00:00:00+03:00","to":"2026-11-01T00:00:00+03:00","q":null,"result":null,"channel":"web"}
}
```

`mode=all` запрещает limit. Для полной беседы `scope=conversation` и `conversation_id`, mode=all. Интервалы времени полуоткрытые [from,to), что исключает задвоение соседних периодов. Тип фильтра по статусу означает результат связанного запроса для обеих его реплик и описывается в интерфейсе.

Ready-метаданные: created_at, snapshot_watermark, filters, requested_count/null, actual_count, partial_conversations, expires_at, format. Скачать нельзя через публичный статический путь; постоянный URL с ID не заменяет авторизацию.

## FAQ редактор

| Метод | Путь | Поведение |
|---|---|---|
| GET | /admin/faqs | Все статусы, поиск, категория, сезон |
| POST | /admin/faqs | Создать draft |
| PATCH | /admin/faqs/{id} | Изменить draft с expected_revision |
| POST | /admin/faqs/{id}/publish | Проверить источник, обновить индекс, атомарно опубликовать |
| POST | /admin/faqs/{id}/hide | Скрыть запись и ее поисковую ревизию |
| PUT | /admin/faqs/order | IDs с новым порядком, только существующие записи |

Поле expected_revision предотвращает молчаливую потерю правок из двух вкладок. Публикация возвращает job_id или завершенный результат. До ready новая версия публично не появляется. Источники редактируются в пределах разрешенного реестра, а не произвольной загрузки пользователя.

## Расходы

`GET /admin/usage/summary`, `/admin/usage/daily`, `/admin/usage/calls`: фильтры from, to, model, purpose; calls имеет курсор. Возвращать известные суммы отдельно от unknown_count и reserved_cost. Если неизвестных нет, unknown_count=0.

`POST /admin/usage/exports` — CSV отчета за период, тот же механизм закрытых файлов. Денежные значения в JSON — decimal-строки с currency; токены — целые. Не возвращать null-usage как zero-usage.

`GET /health/live` проверяет процесс; `/health/ready` — необходимые зависимости без секретов. Административные подробности состояния доступны отдельно и защищены. Внешний GPT не вызывается health-check на каждый запрос.
