import asyncio
import logging
import os
import re  # <--- Добавил этот импорт для очистки текста
import time

import aiosqlite
import yt_dlp
from aiogram import Bot
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

# --- КОНСТАНТЫ ---
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

DOWNLOAD_PATH = "downloads"
DB_PATH = "bot_database.db"
VIDEO_LIMIT_DURATION = 180
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


def get_cookies_file(url: str) -> str | None:
    for domain, filename in COOKIES_MAP.items():
        if domain in url:
            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                return filename
    return None


def normalize_url(url: str) -> str:
    if "youtube.com/shorts/" in url:
        try:
            video_id = url.split("shorts/")[-1].split("?")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        except IndexError:
            pass
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
        file_id = row[0]
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=f"👤 Заказал: @{username}\n🚀 <b>Из кэша (мгновенно)</b>\n🔗 <a href='{url}'>Источник</a>",
                parse_mode="HTML",
            )
            try:
                await message_with_url.delete()
            except:
                pass
            return
        except Exception as e:
            logger.warning(f"Кэш не сработал (file_id {file_id}), качаем заново: {e}")

    # 2. СКАЧИВАНИЕ
    cookies = get_cookies_file(url)
    loop = asyncio.get_running_loop()
    final_filename = None
    start_time = time.time()

    last_update_time = 0

    def progress_hook(d):
        nonlocal last_update_time
        if d["status"] == "downloading":
            current_time = time.time()
            # Обновляем сообщение не чаще чем раз в 3 секунды
            if current_time - last_update_time > 3:
                last_update_time = current_time

                # Получаем "грязные" проценты
                raw_percent = d.get("_percent_str", "").strip()

                # 👇 ЧИСТИМ ANSI-КОДЫ (ЦВЕТА)
                clean_percent = re.sub(r"\x1b\[[0-9;]*m", "", raw_percent)

                asyncio.run_coroutine_threadsafe(
                    safe_edit(message_with_url, f"⏳ Скачиваю... {clean_percent}"), loop
                )

    def check_duration_filter(info, *, incomplete):
        duration = info.get("duration")
        if duration and duration > VIDEO_LIMIT_DURATION:
            return f"Видео слишком длинное ({int(duration/60)} мин). Лимит: {int(VIDEO_LIMIT_DURATION/60)} мин."
        return None

    # --- НАСТРОЙКИ (FIX INSTAGRAM) ---
    ydl_opts = {
        # Ищем кодек h264 (avc) для Телеграма + лучший звук
        "format": "bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        # Обязательно склеиваем в MP4
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_PATH}/%(id)s_%(title).50s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "match_filter": check_duration_filter,
        "noplaylist": True,
        "overwrites": True,
        # Если в докере ffmpeg лежит стандартно:
        "ffmpeg_location": "/usr/bin/ffmpeg",
    }
    # ----------------------------------------------------

    if cookies:
        ydl_opts["cookiefile"] = cookies

    # Прокси оставлен как есть (для всех сайтов)
    if PROXY_URL:
        ydl_opts["proxy"] = PROXY_URL
        logger.info(f"Using proxy: {PROXY_URL}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)

            final_filename = ydl.prepare_filename(info)

            # Хак: если мы форсировали mp4, а prepare_filename вернул .webm или .mkv
            root, ext = os.path.splitext(final_filename)
            if ydl_opts.get("merge_output_format") == "mp4" and ext != ".mp4":
                final_filename = root + ".mp4"

        if not final_filename or not os.path.exists(final_filename):
            raise FileNotFoundError(f"Файл не найден: {final_filename}")

        elapsed = time.time() - start_time
        video_file = FSInputFile(final_filename)

        try:
            await message_with_url.delete()
        except:
            pass

        caption = (
            f"👤 Заказал: @{username}\n"
            f"⏱ Обработка: {elapsed:.1f} сек\n"
            f"🔗 <a href='{url}'>Источник</a>"
        )

        msg = await bot.send_video(
            chat_id=chat_id, video=video_file, caption=caption, parse_mode="HTML"
        )

        if msg.video and msg.video.file_id:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO video_cache (url, file_id) VALUES (?, ?)",
                    (url, msg.video.file_id),
                )
                await db.commit()

    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "Видео слишком длинное" in err_msg:
            # Очищаем сообщение от лишнего мусора yt-dlp
            clean_text = (
                err_msg.split(":", 1)[-1].strip()
                if ":" in err_msg
                else "Видео слишком длинное"
            )
            await safe_edit(message_with_url, f"⚠️ {clean_text}")
        elif "Sign in to confirm" in err_msg:
            await safe_edit(
                message_with_url,
                "🔒 Видео 18+ или требует входа (нужны cookies).",
            )
        else:
            logger.error(f"YT-DLP Error: {e}")
            await safe_edit(message_with_url, "❌ Ошибка при скачивании.")

    except Exception as e:
        logger.error(f"General Error: {e}")
        await safe_edit(message_with_url, "❌ Произошла ошибка бота.")

    finally:
        if final_filename and os.path.exists(final_filename):
            try:
                os.remove(final_filename)
            except Exception:
                pass
