import asyncio
import logging
import os
import re
import time

import aiosqlite
import yt_dlp
from aiogram import Bot
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Качаем напрямую в общую папку
DOWNLOAD_PATH = "/var/lib/telegram-bot-api"
DB_PATH = "bot_database.db"

logger = logging.getLogger(__name__)


async def safe_edit(message: Message, text: str):
    try:
        if message.text == text:
            return
        await message.edit_text(text)
    except Exception:
        pass


async def download_and_send_media(
    bot: Bot, chat_id: int, url: str, message_with_url: Message, username: str
):
    # 1. Проверка кэша
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT file_id FROM video_cache WHERE url = ?", (url,)
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        try:
            await bot.send_video(
                chat_id=chat_id, video=row[0], caption=f"🚀 Из кэша\n🔗 {url}"
            )
            await message_with_url.delete()
            return
        except Exception:
            pass

    # 2. Настройки скачивания
    ydl_opts = {
        "format": "bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_PATH}/%(id)s.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "overwrites": True,
        "cookiefile": (
            "instagram_cookies.txt"
            if "instagram" in url
            else (
                "youtube_cookies.txt" if "youtube" in url or "youtu.be" in url else None
            )
        ),
    }
    if PROXY_URL:
        ydl_opts["proxy"] = PROXY_URL

    final_abs_path = None
    try:
        await safe_edit(message_with_url, "⏳ Начинаю скачивание...")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            final_abs_path = ydl.prepare_filename(info)
            if not final_abs_path.endswith(".mp4"):
                final_abs_path = os.path.splitext(final_abs_path)[0] + ".mp4"

        # ОТПРАВКА
        # Передаем строку с путем. Сервер с флагом --local её поймет.
        msg = await bot.send_video(
            chat_id=chat_id, video=final_abs_path, caption=f"👤 @{username}\n🔗 {url}"
        )

        if msg.video:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO video_cache (url, file_id) VALUES (?, ?)",
                    (url, msg.video.file_id),
                )
                await db.commit()

        await message_with_url.delete()

    except Exception as e:
        logger.error(f"Error sending video: {e}")
        await safe_edit(message_with_url, f"❌ Ошибка: {str(e)[:50]}...")
    finally:
        if final_abs_path and os.path.exists(final_abs_path):
            os.remove(final_abs_path)
