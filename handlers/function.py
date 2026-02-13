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

# Папка для обмена файлами
DOWNLOAD_PATH = "/var/lib/telegram-bot-api"
DB_PATH = "bot_database.db"

logger = logging.getLogger(__name__)

# Гарантируем, что папка существует
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

# Даем права 777 на саму папку
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
    start_time = time.time()  # ⏱ Засекаем время начала

    # 1. Проверка кэша
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT file_id FROM video_cache WHERE url = ?", (url,)
        ) as cursor:
            row = await cursor.fetchone()

    if row:
        try:
            caption = (
                f"👤 Заказ для: @{username}\n"
                f"🚀 <b>Из кэша (мгновенно)</b>\n"
                f"🔗 Источник\n{url}"
            )
            await bot.send_video(
                chat_id=chat_id, video=row[0], caption=caption, parse_mode="HTML"
            )
            await message_with_url.delete()
            return
        except Exception:
            pass

    # 2. Настройки скачивания (ЗОЛОТАЯ СЕРЕДИНА: Качество + Совместимость)
    ydl_opts = {
        # Приоритет:
        # 1. H.264 видео + m4a аудио (Идеально для iPhone/Android)
        # 2. H.264 видео + любое аудио
        # 3. Лучшее видео + лучшее аудио (если нет H.264)
        "format": "bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[vcodec^=avc]+bestaudio/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_PATH}/%(id)s.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "overwrites": True,
        # 👇 МАГИЯ FFMPEG: Чиним звук и FPS без потери качества картинки
        "postprocessor_args": [
            "-c:v",
            "copy",  # Видео копируем как есть (0% потери качества, быстро)
            "-c:a",
            "aac",  # Звук кодируем в AAC (чтобы работал везде)
            "-b:a",
            "192k",  # Высокий битрейт звука
            "-strict",
            "experimental",
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

        # Скачивание + Обработка ffmpeg (происходит внутри yt_dlp)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            final_abs_path = ydl.prepare_filename(info)
            if not final_abs_path.endswith(".mp4"):
                final_abs_path = os.path.splitext(final_abs_path)[0] + ".mp4"

        # Права доступа для сервера
        if os.path.exists(final_abs_path):
            os.chmod(final_abs_path, 0o644)

        # Считаем время
        elapsed = time.time() - start_time

        caption = (
            f"👤 Заказ для: @{username}\n"
            f"⏱ Обработка заняла: {elapsed:.1f} сек\n"
            f"🔗 Источник\n{url}"
        )

        # ОТПРАВКА
        try:
            msg = await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(final_abs_path),
                caption=caption,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"First send attempt failed: {e}")
            raise e

        # Кэшируем
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
            try:
                os.remove(final_abs_path)
            except Exception:
                pass
