# Discord → Telegram Voice Notifications

Уведомляет в Telegram, когда в пустой голосовой канал Discord заходит первый участник.

## Возможности

- Несколько голосовых каналов одного сервера.
- Уведомления в личный чат или группу со ссылкой на канал.
- Без записи аудио и уведомлений о занятых каналах при запуске.

## Быстрый старт

Боту Discord выдайте право **View Channel**.

```sh
cp .env.example .env
# Заполните .env
docker compose up -d
```

## Настройка

Все поля, кроме `BOT_IMAGE`, обязательны. Шаблон: [`.env.example`](.env.example).

| Поле | Значение / где получить |
| --- | --- |
| `DISCORD_BOT_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Reset Token |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | ID чата из [getUpdates](https://core.telegram.org/bots/api#getupdates), для групп — с минусом |
| `DISCORD_GUILD_ID` | ID сервера: режим разработчика Discord → Копировать ID |
| `DISCORD_VOICE_CHANNEL_IDS` | ID голосовых каналов через запятую |
| `BOT_IMAGE` | Необязательно: готовый образ `ghcr.io/owner/repository:latest` |
