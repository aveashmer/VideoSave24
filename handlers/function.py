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

# 👇 ВАЖНО: Качаем сразу в общую папку, которую видит локальный сервер Telegram
DOWNLOAD_PATH = "/var/lib/telegram-bot-api"
DB_PATH = "bot_database.db"
VIDEO_LIMIT_DURATION = 600

# Проверка, что папка существует (в докере она создастся через volumes, но на всякий случай)
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

    # 1. ПРОВЕРКА КЭША
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
        "outtmpl": f"{DOWNLOAD_PATH}/%(id)s.%(ext)s",  # Сохраняем сразу в общую папку
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "noplaylist": True,
        "overwrites": True,
    }

    if PROXY_URL:
        ydl_opts["proxy"] = PROXY_URL

    final_abs_path = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            final_abs_path = ydl.prepare_filename(info)

            # Если yt-dlp сделал merge в mp4, расширение в пути могло остаться старым
            if not final_abs_path.endswith(".mp4"):
                actual_path = os.path.splitext(final_abs_path)[0] + ".mp4"
                if os.path.exists(actual_path):
                    final_abs_path = actual_path

        # Отправляем видео, передавая СТРОКУ с абсолютным путем
        msg = await bot.send_video(
            chat_id=chat_id,
            video=final_abs_path,  # Для локального сервера это должен быть полный путь
            caption=f"👤 @{username}\n🔗 {url}",
            parse_mode="HTML",
        )

        if msg.video and msg.video.file_id:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO video_cache (url, file_id) VALUES (?, ?)",
                    (url, msg.video.file_id),
                )
                await db.commit()

        await message_with_url.delete()

    except Exception as e:
        logger.error(f"Error sending video: {e}")
        await safe_edit(message_with_url, "❌ Ошибка при отправке видео.")

    finally:
        # Удаляем файл после отправки, чтобы не забивать диск сервера
        if final_abs_path and os.path.exists(final_abs_path):
            try:
                os.remove(final_abs_path)
            except Exception:
                pass
