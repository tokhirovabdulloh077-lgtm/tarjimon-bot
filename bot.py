import asyncio
import html
import logging
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from dotenv import load_dotenv

from database import (
    get_all_user_ids,
    get_recent_users,
    get_setting,
    get_stats,
    init_db,
    is_admin,
    set_setting,
    upsert_user,
)
from translator import smart_translate

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tarjimon_bot")

router = Router()
BOT_USERNAME: str = ""
DEPLOY_VERSION = "v5.3-ai"


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user:
        await upsert_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name or "",
            last_name=message.from_user.last_name or "",
            username=message.from_user.username or "",
            increment_count=False,
        )

    text = (
        "👋 <b>Assalomu alaykum! O'zbekcha Tarjimon botiga xush kelibsiz!</b>\n\n"
        "Menga istalgan tilda (ingliz, rus, turk, nemis, arab, hind, xitoy va boshqa har qanday tilda) "
        "matn yuboring — men uni <b>O'zbek tiliga</b> tarjima qilib beraman! 🇺🇿\n\n"
        "💡 <i>Agar o'zbek tilida yozsangiz, uni ingliz tiliga o'girib beraman! 🇬🇧</i>\n\n"
        "✍️ <b>Istalgan so'z yoki gap yuborib sinab ko'ring!</b>"
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@router.message(Command("stat", "admin"))
async def cmd_stat(message: Message) -> None:
    if not message.from_user:
        return
    is_adm = await is_admin(message.from_user.id)
    if not is_adm:
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    stats = await get_stats()
    text = (
        "📊 <b>Bot Statistikasi:</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {stats['total_users']} ta\n"
        f"💬 <b>Jami qilingan tarjimalar:</b> {stats['total_translations']} ta\n"
        f"🔥 <b>Bugun faol bo'lganlar:</b> {stats['today_active']} ta\n"
        f"✨ <b>Bugun yangi qo'shilganlar:</b> {stats['today_new']} ta\n\n"
        "💡 <i>Foydalanuvchilar ro'yxati: /users\n"
        "E'lon yuborish: /broadcast &lt;matn&gt;</i>"
    )
    await message.reply(text)


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not message.from_user:
        return
    is_adm = await is_admin(message.from_user.id)
    if not is_adm:
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    users = await get_recent_users(limit=30)
    if not users:
        await message.reply("Hozircha birorta ham foydalanuvchi yo'q.")
        return

    lines = ["📋 <b>Oxirgi faol foydalanuvchilar:</b>\n"]
    for idx, u in enumerate(users, start=1):
        name = html.escape(f"{u['first_name']} {u['last_name']}".strip() or "Nomsiz")
        uname = f"@{u['username']}" if u['username'] else f"<code>ID: {u['user_id']}</code>"
        admin_badge = " 👑 (Admin)" if u['is_admin'] else ""
        lines.append(
            f"{idx}. <b>{name}</b> ({uname}){admin_badge}\n"
            f"   └ <i>Tarjimalar: {u['translations_count']} ta | Oxirgi faollik: {u['last_active']}</i>"
        )

    await message.reply("\n".join(lines))


@router.message(Command("broadcast", "xabar"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    is_adm = await is_admin(message.from_user.id)
    if not is_adm:
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    target_msg = message.reply_to_message
    parts = message.text.split(maxsplit=1)
    if not target_msg and len(parts) < 2:
        await message.reply(
            "📢 <b>Barcha obunachilarga xabar yuborish uchun:</b>\n\n"
            "Yubormoqchi bo'lgan xabaringizga <code>reply</code> qilib /broadcast deb yozing yoki:\n"
            "<code>/broadcast Salom hammaga!</code> deb yuboring."
        )
        return

    text_to_send = parts[1] if len(parts) > 1 else ""
    user_ids = await get_all_user_ids()
    status_msg = await message.reply(f"⏳ Xabar {len(user_ids)} ta obunachiga yuborilmoqda...")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            if target_msg:
                await target_msg.copy_to(chat_id=uid)
            else:
                await bot.send_message(chat_id=uid, text=text_to_send)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>Xabar yuborish yakunlandi!</b>\n\n"
        f"📬 Yetib bordi: <b>{sent} ta</b>\n"
        f"❌ Yetib bormadi (botni to'xtatgan): <b>{failed} ta</b>"
    )


@router.message(Command("setkey"))
async def cmd_setkey(message: Message) -> None:
    if not message.from_user:
        return
    is_adm = await is_admin(message.from_user.id)
    if not is_adm:
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        current_key = await get_setting("gemini_api_key", "")
        status = "✅ Ulangan (AI faol)" if current_key else "❌ Ulanmagan"
        await message.reply(
            f"🤖 <b>Gemini Sun'iy Intellekt (AI) holati:</b> {status}\n\n"
            "Kalitni o'rnatish uchun:\n"
            "<code>/setkey AIzaSy...</code> deb yuboring.\n\n"
            "<i>(Kalitni aistudio.google.com saytidan 100% tekinga olish mumkin)</i>"
        )
        return

    key = parts[1].strip()
    await set_setting("gemini_api_key", key)
    await message.reply("🔥 <b>Gemini AI kaliti muvaffaqiyatli saqlandi!</b>\n\nEndi bot har qanday gapni 100% jonli va tabiiy so'zlashuv uslubida tarjima qiladi!")


@router.message(F.text)
async def handle_translation(message: Message) -> None:
    text = message.text.strip()
    if not text:
        return

    # Check if this is a group chat
    is_group = message.chat.type in ["group", "supergroup"]
    if is_group:
        # If in group, only respond if tagged, replied to, or command used
        bot_tag = f"@{BOT_USERNAME}".lower()
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.username
            and message.reply_to_message.from_user.username.lower() == BOT_USERNAME.lower()
        )
        is_tagged = bot_tag in text.lower()

        if not (is_tagged or is_reply_to_bot or text.startswith("/tr")):
            return

        # If tagged or replied, clean the mention
        text = re.sub(rf"@{BOT_USERNAME}\b", "", text, flags=re.IGNORECASE).strip()
        if text.startswith("/tr"):
            text = text[3:].strip()

        # If user replied to another message with just @tarjima01_bot or /tr, translate the replied message!
        if not text and message.reply_to_message and message.reply_to_message.text:
            text = message.reply_to_message.text.strip()

    if not text or text.startswith("/"):
        return

    # Record user in database
    if message.from_user:
        await upsert_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name or "",
            last_name=message.from_user.last_name or "",
            username=message.from_user.username or "",
            increment_count=True,
        )

    gemini_key = await get_setting("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))

    try:
        res = await smart_translate(text, gemini_api_key=gemini_key)
        src_name = res["src_name"]
        src_flag = res["flag"]
        dst_name = res.get("dst_name", "O'zbekcha")
        dst_flag = res.get("dst_flag", "🇺🇿")
        translated = res["translated"]

        response_text = (
            f"{src_flag} <b>{src_name} ➔ {dst_flag} {dst_name}:</b>\n\n"
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

    if inline_query.from_user:
        await upsert_user(
            user_id=inline_query.from_user.id,
            first_name=inline_query.from_user.first_name or "",
            last_name=inline_query.from_user.last_name or "",
            username=inline_query.from_user.username or "",
            increment_count=True,
        )

    gemini_key = await get_setting("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))

    try:
        res = await smart_translate(query, gemini_api_key=gemini_key)
        src_name = res["src_name"]
        src_flag = res["flag"]
        dst_name = res.get("dst_name", "O'zbekcha")
        dst_flag = res.get("dst_flag", "🇺🇿")
        translated = res["translated"]

        title = f"{src_flag} ➔ {dst_flag} {dst_name}:"
        result_text = f"{dst_flag} {translated}"

        article = InlineQueryResultArticle(
            id="1",
            title=title,
            description=translated,
            input_message_content=InputTextMessageContent(
                message_text=result_text
            ),
        )
        await inline_query.answer([article], cache_time=5)
    except Exception:
        pass


async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text=f"Tarjimon bot {DEPLOY_VERSION} is live and healthy!")


async def start_web_server() -> None:
    """Run a small dummy HTTP server so Render Web Service health-check passes."""
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server running on port {port}")


async def run_startup_diagnostics(bot: Bot) -> None:
    try:
        r1 = await smart_translate("you are idiot")
        r2 = await smart_translate("what should we do")
        r3 = await smart_translate("nima gap")
        r4 = await smart_translate("Привет, как дела?")
        diag = (
            f"🇺🇿🇬🇧 {DEPLOY_VERSION} | "
            f"1:{r1['translated']} ({r1['src_lang']}) | "
            f"2:{r2['translated']} | "
            f"3:{r3['translated']}"
        )[:118]
        await bot.set_my_short_description(short_description=diag)
        logger.info(f"Startup diagnostics passed: {diag} | 4:{r4['translated']}")
    except Exception as e:
        err_msg = f"⚠️ {DEPLOY_VERSION} ERR: {type(e).__name__}: {str(e)}"[:118]
        try:
            await bot.set_my_short_description(short_description=err_msg)
        except Exception:
            pass
        logger.exception("Startup diagnostics failed")


async def main() -> None:
    global BOT_USERNAME
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN topilmadi! .env fayliga BotFather tokenini kiriting.")
        return

    await init_db()
    await start_web_server()

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
    await run_startup_diagnostics(bot)
    logger.info(f"Tarjimon bot ishga tushdi: @{me.username} ({me.full_name}) [{DEPLOY_VERSION}]")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
