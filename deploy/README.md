# Развёртывание

Production-контур использует Nginx для статической сборки React и systemd для FastAPI на `127.0.0.1:8100`.

- Код: `/opt/kreativmusey/current`
- Python-окружение: `/opt/kreativmusey/venv`
- Переменные: `/etc/kreativmusey.env`
- API-сервис: `kreativmusey.service`
- Nginx: `/etc/nginx/sites-enabled/kreativmusey`

Секреты и SQLite-база не хранятся в Git. Перед внешней публикацией через домен необходимо включить HTTPS и установить `COOKIE_SECURE=true`.
