import asyncio
import logging
import os
import sys
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from dotenv import load_dotenv

from handlers import commands

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_PATH = "bot_database.db"

if not BOT_TOKEN:
    logging.critical("❌ ОШИБКА: Не найден BOT_TOKEN в .env файле!")
    sys.exit(1)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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


async def start_handler(message: types.Message):
    await log_user(message.from_user)
    await message.answer(
        f"👋 Привет, {message.from_user.full_name}!\nЯ скачиваю видео из соцсетей. Просто отправь ссылку."
    )


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    await init_db()

    # 👇 НАСТРОЙКА ЛОКАЛЬНОГО СЕРВЕРА
    session = AiohttpSession(
        api=TelegramAPIServer.from_base("http://telegram-bot-api:8081")
    )

    bot = Bot(token=BOT_TOKEN, session=session, parse_mode="HTML")
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.include_router(commands.router)

    logging.info("🚀 Бот запущен через локальный API сервер...")
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
