import asyncio
import logging
import os
import re
import shutil
import time

import aiosqlite
import yt_dlp
from aiogram import Bot
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")
DOWNLOAD_PATH = "downloads"
DB_PATH = "bot_database.db"
VIDEO_LIMIT_DURATION = 600  # Увеличил лимит до 10 минут, раз сервер теперь тянет
COOKIES_MAP = {
    "instagram.com": "instagram_cookies.txt",
    "youtube.com": "youtube_cookies.txt",
    "youtu.be": "youtube_cookies.txt",
}

if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

logger = logging.getLogger(__name__)


async def safe_edit(message: Message, text: str):
    try:
        if message.text == text:
            return
        await message.edit_text(text)
    except Exception:
        pass


def normalize_url(url: str) -> str:
    if "youtube.com/shorts/" in url:
        video_id = url.split("shorts/")[-1].split("?")[0]
        return f"https://www.youtube.com/watch?v={video_id}"
    return url.strip()


async def download_and_send_media(
    bot: Bot, chat_id: int, url: str, message_with_url: Message, username: str
):
    url = normalize_url(url)

    # 1. КЭШ
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

    # 2. СКАЧИВАНИЕ
    loop = asyncio.get_running_loop()
    last_update_time = 0

    def progress_hook(d):
        nonlocal last_update_time
        if d["status"] == "downloading":
            current_time = time.time()
            if current_time - last_update_time > 3:
                last_update_time = current_time
                raw_percent = d.get("_percent_str", "").strip()
                clean_percent = re.sub(r"\x1b\[[0-9;]*m", "", raw_percent)
                asyncio.run_coroutine_threadsafe(
                    safe_edit(message_with_url, f"⏳ Скачиваю... {clean_percent}"), loop
                )

    ydl_opts = {
        "format": "bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_PATH}/%(id)s.%(ext)s",
        "quiet": True,
        "progress_hooks": [progress_hook],
        "noplaylist": True,
    }

    if PROXY_URL:
        ydl_opts["proxy"] = PROXY_URL

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            temp_file = ydl.prepare_filename(info)
            if not temp_file.endswith(".mp4"):
                temp_file = os.path.splitext(temp_file)[0] + ".mp4"

        # ПУТИ ДЛЯ ЛОКАЛЬНОГО СЕРВЕРА
        file_name = os.path.basename(temp_file)
        server_shared_path = f"/var/lib/telegram-bot-api/{file_name}"

        # Перемещаем файл в общую папку сервера
        shutil.move(temp_file, server_shared_path)

        msg = await bot.send_video(
            chat_id=chat_id,
            video=server_shared_path,  # Передаем путь строкой!
            caption=f"👤 @{username}\n🔗 {url}",
        )

        # Сохраняем в кэш
        if msg.video:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO video_cache (url, file_id) VALUES (?, ?)",
                    (url, msg.video.file_id),
                )
                await db.commit()

        await message_with_url.delete()

    except Exception as e:
        logger.error(f"Error: {e}")
        await safe_edit(message_with_url, "❌ Ошибка при обработке видео.")

    finally:
        if "server_shared_path" in locals() and os.path.exists(server_shared_path):
            os.remove(server_shared_path)
