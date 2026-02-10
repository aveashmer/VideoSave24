import logging

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ChatType
from aiogram.utils.chat_action import ChatActionSender

# Импортируем функцию скачивания
from handlers import function as hf

router = Router()
logger = logging.getLogger(__name__)

# Список доменов
SUPPORTED_DOMAINS = [
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "shorts",  # Иногда ссылка может быть просто youtube.com/shorts/...
]


def is_supported_link(text: str) -> bool:
    """Проверяет, содержит ли сообщение ссылку на поддерживаемый сервис."""
    if not text:
        return False
    text = text.lower().strip()
    # Простая проверка: есть ли домен в тексте
    return any(domain in text for domain in SUPPORTED_DOMAINS)


@router.message(F.text.func(is_supported_link))
async def video_request(message: types.Message):
    """
    Хендлер, который ловит ссылки и запускает скачивание.
    """
    # 1. Пропускаем лишние чаты (каналы и т.д., если нужно)
    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE]:
        return

    user_info = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"ID:{message.from_user.id}"
    )
    logger.info(f"Получена ссылка от {user_info}: {message.text[:50]}...")

    # 2. Удаляем сообщение пользователя (чистота чата)
    try:
        await message.delete()
    except Exception:
        # Бот может не иметь прав на удаление в чужой группе
        pass

    # 3. Отправляем "Загрузку"
    placeholder = await message.answer("⏳ Ищу видео...")

    # 4. Запускаем "вечный" статус отправки видео, пока функция не закончит работу
    # Это создает эффект "Бот отправляет видео..." в шапке чата
    async with ChatActionSender.upload_video(chat_id=message.chat.id, bot=message.bot):
        try:
            await hf.download_and_send_media(
                bot=message.bot,
                chat_id=message.chat.id,
                url=message.text.strip(),
                message_with_url=placeholder,
                username=message.from_user.username or message.from_user.first_name,
            )
        except Exception as e:
            logger.error(f"Ошибка в хендлере: {e}")
            try:
                await placeholder.edit_text(
                    f"❌ Что-то пошло не так. Попробуйте позже."
                )
            except:
                pass
