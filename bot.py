import asyncio
import logging
import os
from typing import List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from dotenv import load_dotenv

from translator import translate_text

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tarjimon_bot")

router = Router()
BOT_USERNAME: str = ""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "👋 <b>Assalomu alaykum! O'zbekcha Tarjimon botiga xush kelibsiz!</b>\n\n"
        "Menga istalgan tilda (ingliz, rus, turk, arab, xitoy, koreys, nemis va h.k.) "
        "matn yuboring — men uni <b>darhol O'zbek tiliga</b> tarjima qilib beraman! 🇺🇿\n\n"
        "💡 <i>Agar o'zbekcha yozsangiz, ingliz tiliga o'girib beraman.</i>\n\n"
        "✍️ <b>Istalgan so'z yoki matn yuborib sinab ko'ring!</b>"
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@router.message(F.text)
async def handle_translation(message: Message) -> None:
    text = message.text.strip()
    if not text:
        return

    # In groups, if message starts with '/', ignore unknown commands
    if text.startswith("/"):
        return

    try:
        res = await translate_text(text)
        src_lang = res["src_lang"]
        flag = res["flag"]
        src_name = res["src_name"]
        translated = res["translated"]
        extra_en = res.get("extra_en")

        if src_lang == "uz" and extra_en:
            response_text = (
                f"🇺🇿 <b>O'zbekcha ➔ 🇬🇧 Inglizcha:</b>\n\n"
                f"<code>{extra_en}</code>"
            )
        else:
            response_text = (
                f"{flag} <b>{src_name} ➔ 🇺🇿 O'zbekcha:</b>\n\n"
                f"<code>{translated}</code>"
            )

        await message.reply(response_text)
    except Exception as e:
        logger.exception("Translation error")
        await message.reply("❌ Matnni tarjima qilishda xatolik yuz berdi. Iltimos qaytadan urinib ko'ring.")


@router.inline_query()
async def inline_translate(inline_query: InlineQuery) -> None:
    query = inline_query.query.strip()
    if not query:
        return

    try:
        res = await translate_text(query)
        src_lang = res["src_lang"]
        flag = res["flag"]
        src_name = res["src_name"]
        translated = res["translated"]
        extra_en = res.get("extra_en")

        if src_lang == "uz" and extra_en:
            title = "🇬🇧 Inglizchaga tarjima:"
            result_text = f"🇬🇧 {extra_en}"
            description = extra_en
        else:
            title = f"{flag} O'zbekchaga tarjima:"
            result_text = f"🇺🇿 {translated}"
            description = translated

        article = InlineQueryResultArticle(
            id="1",
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(
                message_text=result_text
            ),
        )
        await inline_query.answer([article], cache_time=5)
    except Exception:
        pass


async def main() -> None:
    global BOT_USERNAME
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN topilmadi! .env fayliga BotFather tokenini kiriting.")
        return

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Botni ishga tushirish"),
            BotCommand(command="help", description="Yordam va qo'llanma"),
        ]
    )
    logger.info(f"Tarjimon bot ishga tushdi: @{me.username} ({me.full_name})")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
