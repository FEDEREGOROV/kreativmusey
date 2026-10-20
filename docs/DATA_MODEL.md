# Модель данных и инварианты

Реализовать миграциями, не созданием таблиц при каждом запуске. Имена могут быть адаптированы к существующему репозиторию при сохранении связей и ограничений.

| Сущность | Обязательные поля | Ограничения |
|---|---|---|
| visitor_sessions | id, token_hash, created_at, expires_at | Только hash секрета сессии, не raw cookie |
| conversations | id, visitor_session_id, channel, created_at, last_message_at | Внешний UUID; принадлежность сессии |
| messages | id, sequence, conversation_id, request_id, role, text, created_at, result_status, kb_version_id | role user/assistant; sequence монотонный; неизменное серверное время |
| chat_requests | id, user_message_id, conversation_id, client_message_id, state, result, kb_version_id, prompt_version, created_at, completed_at, lease_until | Unique conversation_id+client_message_id; один активный запрос на беседу |
| message_sources | message_id, chunk_revision_id, display_order | Указывает конкретную ревизию, а не изменяемый текущий текст |
| feedback | message_id, visitor_session_id, value, updated_at | Unique message_id+session; только assistant |
| api_calls | id, request_id/null, import_job_id/null, attempt, purpose, provider_response_id, model, usage_raw, token categories, pricing_version_id, cost, cost_status, duration_ms, created_at | Unique provider response when known; отдельная запись каждой попытки |
| pricing_versions | id, model, service_tier, currency, valid_from, valid_to, input_rate, read_rate, write_rate, output_rate, unit | Пересечения периодов одной комбинации запрещены |
| budget_reservations | id, request_id, period_day, period_month, amount, currency, state | Транзакционный резерв; unknown не становится released без сверки |
| kb_versions | id, name, season, status, source_hash, embedding_model, embedding_dimension, created_at, approved_at | Один активный published-профиль, атомарное переключение |
| kb_sources | id, kb_version_id, filename, hash, title, public_url, priority, season, verified_at | Закрытый исходник; URL из разрешенного реестра |
| kb_chunks | id, parent_id, source_id, content, section, topic, season, valid_from/to, approval_status, revision, embedding | В поиск идут только подходящие published/approved записи |
| faqs | id, current_revision_id, display_order, featured | Ревизии сохраняются, скрытие без удаления истории |
| faq_revisions | id, faq_id, question, answer, category, source_id, season, revision, status, created_at | Опубликованный текст связан с той же поисковой ревизией |
| admins | id, username, password_hash, created_at | Username уникален, нет открытого пароля |
| admin_sessions | id, admin_id, token_hash, csrf_hash, expires_at, last_seen_at | Logout делает сессию недействительной на сервере |
| export_jobs | id, created_by, filters, selection, snapshot_watermark, state, progress, actual_count, file_key, expires_at | Доступ только администратору, snapshot неизменяем |
| audit_events | id, admin_id/null, action, entity_type, entity_id, safe_metadata, created_at | Без паролей, API-ключей и полного текста диалогов |
| deletion_tombstones | entity_id, scope, deleted_at, reason_ref | Нужны для повторного применения удаления после restore |

## Обязательные инварианты

1. На вопрос создается одна user-реплика и не более одной финальной assistant-реплики. Повтор API не увеличивает число user-реплик.
2. Каждая финальная реплика имеет связь с запросом, опубликованной версией знаний и инструкцией. Отмена публикации текущего источника не разрушает историю того, чем был обоснован прошлый ответ.
3. Токены хранятся в api_calls. Их нельзя дублировать как полную стоимость одновременно в строке вопроса и ответа и потом суммировать по messages.
4. Unknown cost — null, отдельно учитываемое состояние. Известный ноль, например локальная обработка без API, имеет явное основание.
5. Decimal для денег; UTC для времени; календарные фильтры преобразуются в границы Europe/Moscow один раз на сервере.
6. Экспорт отражает выбранные сообщения на snapshot; новые сообщения и изменения фильтров после создания job не меняют файл.
7. Публикация FAQ и поисковой ревизии согласована транзакцией или staged job. Нет состояния, где на сайте новый ответ, а в поиске еще старый.
8. Известный conversation_id не дает доступ к беседе. Visitor принадлежность и admin permission проверяются на чтение и запись.

## Индексы

conversations(last_message_at,id), messages(conversation_id,sequence), messages(sequence), chat_requests(conversation_id,state), api_calls(created_at,model,purpose), export_jobs(created_by,created_at), FAQ(status,display_order), полнотекстовый индекс messages для admin-поиска, полнотекстовый и векторный индекс опубликованных chunks с фильтрами версии и сезона.

Поисковые индексы и материалы чата не открывать браузеру напрямую через публичный DB-ключ. Ограничение чтения не должно зависеть только от фильтра frontend.
