import asyncio
import logging
import os
import time

import aiosqlite
import yt_dlp
from aiogram import Bot
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Путь к общей папке с сервером Telegram
DOWNLOAD_PATH = "/var/lib/telegram-bot-api"
DB_PATH = "bot_database.db"

logger = logging.getLogger(__name__)

# Гарантируем права доступа к папке при запуске
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)
try:
    os.chmod(DOWNLOAD_PATH, 0o777)
except Exception:
    pass


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
    start_time = time.time()

    # 1. Проверка кэша БД
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT file_id FROM video_cache WHERE url = ?", (url,)
        ) as cursor:
            row = await cursor.fetchone()

    if row:
        try:
            caption = f"👤 Заказ для: @{username}\n🚀 <b>Из кэша (мгновенно)</b>\n🔗 Источник\n{url}"
            await bot.send_video(
                chat_id=chat_id, video=row[0], caption=caption, parse_mode="HTML"
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
        # Исправляем звук и структуру файла для Telegram
        "postprocessor_args": [
            "-c:v",
            "copy",  # Видео не трогаем (качество 100%)
            "-c:a",
            "aac",  # Звук в AAC (решает проблему тишины на длинных видео)
            "-b:a",
            "192k",  # Качественный битрейт звука
            "-map_metadata",
            "0",  # Переносим теги
            "-movflags",
            "faststart",  # Инстаграм-фикс: переносим заголовок в начало
        ],
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

        # Права для локального сервера
        if os.path.exists(final_abs_path):
            os.chmod(final_abs_path, 0o644)

        elapsed = time.time() - start_time
        caption = f"👤 Заказ для: @{username}\n⏱ Обработка заняла: {elapsed:.1f} сек\n🔗 Источник\n{url}"

        # Отправка через локальный сервер
        msg = await bot.send_video(
            chat_id=chat_id,
            video=FSInputFile(final_abs_path),
            caption=caption,
            parse_mode="HTML",
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
        logger.error(f"Error: {e}")
        await safe_edit(message_with_url, f"❌ Ошибка: {str(e)[:50]}...")
    finally:
        if final_abs_path and os.path.exists(final_abs_path):
            try:
                os.remove(final_abs_path)
            except Exception:
                pass
