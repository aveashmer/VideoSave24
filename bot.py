import asyncio
import logging
import os
import sys
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile
from dotenv import load_dotenv

# Импортируем роутер с логикой обработки ссылок
from handlers import commands

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_PATH = "bot_database.db"
VIDEO_LIMIT_DURATION = 180  # 3 минуты

if not BOT_TOKEN:
    logging.critical("❌ ОШИБКА: Не найден BOT_TOKEN в .env файле!")
    sys.exit(1)


# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Таблица пользователей
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # 2. НОВАЯ ТАБЛИЦА ДЛЯ КЭША (file_id)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_cache (
                url TEXT PRIMARY KEY,
                file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        await db.commit()


async def log_user(user: types.User):
    """Сохраняет или обновляет данные пользователя."""
    if not user:
        return
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, full_name, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_seen=excluded.last_seen
        """,
            (user.id, user.username, user.full_name, now),
        )
        await db.commit()


async def get_stats():
    """Считает количество пользователей."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = await cursor.fetchone()
            return total[0] if total else 0


# --- ХЕНДЛЕРЫ ---


async def start_handler(message: types.Message):
    await log_user(message.from_user)
    await message.answer(
        f"👋 Привет, {message.from_user.full_name}!\n\n"
        "Я скачиваю видео из TikTok, YouTube Shorts, Facebook и Instagram.\n"
        "Просто отправь мне ссылку.\n\n"
    )


async def stats_command(message: types.Message):
    if not ADMIN_ID or str(message.from_user.id) != str(ADMIN_ID):
        return
    count = await get_stats()

    # Добавим в статистику инфу о размере кэша
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM video_cache") as cursor:
            cache_count = await cursor.fetchone()
            cache_total = cache_count[0] if cache_count else 0

    await message.answer(
        f"📊 Статистика бота:\n\n"
        f"👥 Пользователей: {count}\n"
        f"💾 Видео в кэше: {cache_total}"
    )


async def export_users_command(message: types.Message):
    if not ADMIN_ID or str(message.from_user.id) != str(ADMIN_ID):
        return

    await message.answer("⏳ Собираю список пользователей...")
    file_path = "users_list.txt"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, full_name, joined_at FROM users"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("В базе пока пусто.")
        return

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f"Всего пользователей: {len(rows)}\n")
        f.write("-" * 50 + "\n")
        for user_id, username, full_name, date in rows:
            u_name = f"@{username}" if username else "No username"
            clean_date = date.split("T")[0] if date else "??"
            f.write(f"{user_id} | {u_name} | {full_name} | {clean_date}\n")

    try:
        doc = FSInputFile(file_path)
        await message.answer_document(doc, caption="📂 Список пользователей")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


# --- ЗАПУСК ---


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.message.register(stats_command, Command("stats"))
    dp.message.register(export_users_command, Command("users"))

    dp.include_router(commands.router)

    logging.info("🚀 Бот запущен...")

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
