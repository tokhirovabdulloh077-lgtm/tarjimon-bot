import asyncio
import html
import io
import logging
import os
import re
import socket
import ssl
import time
import uuid
from typing import Dict, Optional, Tuple
import aiohttp
import certifi
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

from database import (
    DEFAULT_GEMINI_KEY,
    get_all_user_ids,
    get_recent_users,
    get_setting,
    get_stats,
    init_db,
    is_admin,
    set_setting,
    upsert_user,
)
from translator import (
    LANG_NAMES,
    generate_speech,
    smart_translate,
    smart_translate_media,
    smart_translate_pdf,
    translate_to_language,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tarjimon_bot")

router = Router()
BOT_USERNAME: str = ""
DEPLOY_VERSION = "v6.3-pro-features"
MAX_MEDIA_SIZE_MB = 20
MAX_MEDIA_SIZE_BYTES = MAX_MEDIA_SIZE_MB * 1024 * 1024

translation_cache: Dict[str, Dict] = {}
pdf_cache: Dict[str, Dict] = {}


def clean_caches() -> None:
    now = time.time()
    if len(translation_cache) > 400:
        keys_to_del = [
            k for k, v in translation_cache.items() if now - v.get("time", 0) > 7200
        ]
        for k in keys_to_del:
            translation_cache.pop(k, None)
    if len(pdf_cache) > 100:
        keys_to_del = [
            k for k, v in pdf_cache.items() if now - v.get("time", 0) > 7200
        ]
        for k in keys_to_del:
            pdf_cache.pop(k, None)


def build_translation_markup(
    job_id: str, current_dst: str = "uz"
) -> InlineKeyboardMarkup:
    tts_btn = InlineKeyboardButton(
        text="🔊 Talaffuz (Eshitish)", callback_data=f"tts:{job_id}"
    )

    all_langs = [
        ("uz", "🇺🇿 O'zbekcha"),
        ("en", "🇬🇧 Inglizcha"),
        ("ru", "🇷🇺 Ruscha"),
        ("tr", "🇹🇷 Turkcha"),
        ("ar", "🇸🇦 Arabcha"),
        ("de", "🇩🇪 Nemischa"),
        ("ko", "🇰🇷 Koreyscha"),
    ]
    other_langs = [l for l in all_langs if l[0] != current_dst]

    row1 = [
        InlineKeyboardButton(text=l[1], callback_data=f"lang:{l[0]}:{job_id}")
        for l in other_langs[:3]
    ]
    row2 = [
        InlineKeyboardButton(text=l[1], callback_data=f"lang:{l[0]}:{job_id}")
        for l in other_langs[3:6]
    ]

    return InlineKeyboardMarkup(inline_keyboard=[[tts_btn], row1, row2])


class MediaTooLargeError(Exception):
    def __init__(self, size_bytes: int = 0):
        self.size_bytes = size_bytes
        self.size_mb = (size_bytes / (1024 * 1024)) if size_bytes else 0.0


async def _ensure_user_and_check_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    u = message.from_user
    await upsert_user(
        user_id=u.id,
        first_name=u.first_name or "",
        last_name=u.last_name or "",
        username=u.username or "",
        increment_count=False,
    )
    return await is_admin(
        user_id=u.id,
        first_name=u.first_name or "",
        last_name=u.last_name or "",
        username=u.username or "",
    )


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
        "Menga istalgan tildagi:\n"
        "💬 <b>Matn</b> yoki 🎙 <b>Ovozli xabar (Voice)</b>\n"
        "🖼 <b>Rasm / Skrinshot</b> yoki 🎭 <b>Stiker</b>\n"
        "🎞 <b>GIF</b> yoki 🎬 <b>Video</b>\n"
        "📚 <b>PDF Kitob yoki Hujjat</b>\n"
        "yuboring — men uni <b>O'zbek tiliga</b> tabiiy tarjima qilib beraman! 🇺🇿\n\n"
        "💡 <i>Har bir tarjima tagida <b>🔊 Talaffuz</b> tugmasi orqali jonli ovozda eshitish va <b>boshqa tillarga o'girish</b> mumkin!</i>\n\n"
        "📹 <i>Maksimal fayl hajmi: 20 MB</i>"
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@router.message(Command("stat", "admin"))
async def cmd_stat(message: Message) -> None:
    if not await _ensure_user_and_check_admin(message):
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    stats = await get_stats()
    current_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)
    ai_badge = "✅ Faol (Gemini AI)" if current_key else "❌ O'chiq"
    text = (
        "📊 <b>Bot Statistikasi:</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {stats['total_users']} ta\n"
        f"💬 <b>Jami qilingan tarjimalar:</b> {stats['total_translations']} ta\n"
        f"🔥 <b>Bugun faol bo'lganlar:</b> {stats['today_active']} ta\n"
        f"✨ <b>Bugun yangi qo'shilganlar:</b> {stats['today_new']} ta\n"
        f"🤖 <b>Sun'iy intellekt (AI):</b> {ai_badge}\n\n"
        "💡 <i>Foydalanuvchilar ro'yxati: /users\n"
        "E'lon yuborish: /broadcast &lt;matn&gt;</i>"
    )
    await message.reply(text)


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not await _ensure_user_and_check_admin(message):
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
        admin_badge = " 👑 (Admin)" if u["is_admin"] else ""
        lines.append(
            f"{idx}. <b>{name}</b> ({uname}){admin_badge}\n"
            f"   └ <i>Tarjimalar: {u['translations_count']} ta | Oxirgi faollik: {u['last_active']}</i>"
        )

    await message.reply("\n".join(lines))


@router.message(Command("broadcast", "xabar"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not await _ensure_user_and_check_admin(message):
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
    if not await _ensure_user_and_check_admin(message):
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        current_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)
        status = "✅ Ulangan (AI doimiy faol)" if current_key else "❌ Ulanmagan"
        await message.reply(
            f"🤖 <b>Gemini Sun'iy Intellekt (AI) holati:</b> {status}\n\n"
            "Yangi kalit o'rnatish uchun:\n"
            "<code>/setkey KALIT</code> deb yuboring."
        )
        return

    key = parts[1].strip()
    await set_setting("gemini_api_key", key)
    await message.reply(
        "🔥 <b>Gemini AI kaliti bulutli xotiraga muvaffaqiyatli saqlandi!</b>\n\n"
        "Endi bot har qanday gapni 100% jonli va tabiiy uslubda tarjima qiladi!"
    )


def _clean_bot_mention(text: str) -> str:
    cleaned = re.sub(rf"@{BOT_USERNAME}\b", "", text, flags=re.IGNORECASE).strip()
    if cleaned.lower().startswith("/tr"):
        cleaned = cleaned[3:].strip()
    return cleaned


async def _extract_media_from_message(
    msg: Message, bot: Bot
) -> Optional[Tuple[Optional[bytes], str, str, str]]:
    """
    Returns (bytes, mime_type, caption, media_kind) where media_kind is:
    "video", "gif", "sticker", "image", "voice", "audio", or "pdf".
    Raises MediaTooLargeError if file exceeds MAX_MEDIA_SIZE_BYTES.
    """
    caption = _clean_bot_mention(msg.caption or "")

    # 1. Voice note (round / voice message)
    if msg.voice:
        v = msg.voice
        if v.file_size and v.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(v.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(v.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(v.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        return buf.getvalue(), "audio/ogg", "", "voice"

    # 2. Audio file
    if msg.audio:
        a = msg.audio
        if a.file_size and a.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(a.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(a.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(a.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        mime = a.mime_type or "audio/mp3"
        return buf.getvalue(), mime, caption, "audio"

    # 3. Video
    if msg.video:
        vid = msg.video
        if vid.file_size and vid.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(vid.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(vid.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(vid.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        mime = vid.mime_type or "video/mp4"
        return buf.getvalue(), mime, caption, "video"

    # 4. Animation (GIF)
    if msg.animation:
        anim = msg.animation
        if anim.file_size and anim.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(anim.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(anim.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(anim.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        mime = anim.mime_type or "video/mp4"
        return buf.getvalue(), mime, caption, "gif"

    # 5. Video Note (Round video)
    if msg.video_note:
        vn = msg.video_note
        if vn.file_size and vn.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(vn.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(vn.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(vn.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        return buf.getvalue(), "video/mp4", "", "video"

    # 6. Photo
    if msg.photo:
        photo = msg.photo[-1]
        if photo.file_size and photo.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(photo.file_size)
        buf = io.BytesIO()
        try:
            await bot.download(photo.file_id, destination=buf)
        except Exception as e:
            if "too big" in str(e).lower():
                raise MediaTooLargeError(photo.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
            raise
        return buf.getvalue(), "image/jpeg", caption, "image"

    # 7. Sticker
    if msg.sticker:
        st = msg.sticker
        extra = f"{st.emoji or ''}".strip()
        if st.file_size and st.file_size > MAX_MEDIA_SIZE_BYTES:
            raise MediaTooLargeError(st.file_size)
        if not st.is_animated and not st.is_video:
            buf = io.BytesIO()
            await bot.download(st.file_id, destination=buf)
            return buf.getvalue(), "image/webp", extra, "sticker"
        if st.thumbnail:
            buf = io.BytesIO()
            await bot.download(st.thumbnail.file_id, destination=buf)
            raw = buf.getvalue()
            mime = (
                "image/webp"
                if (len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
                else "image/jpeg"
            )
            return raw, mime, extra, "sticker"
        return None, "image/webp", extra or "🙂", "sticker"

    # 8. Document (PDF, Image, GIF or Video files)
    if msg.document:
        doc = msg.document
        mime = (doc.mime_type or "").lower()
        name = (doc.file_name or "").lower()

        is_pdf = mime == "application/pdf" or name.endswith(".pdf")
        if is_pdf:
            if doc.file_size and doc.file_size > MAX_MEDIA_SIZE_BYTES:
                raise MediaTooLargeError(doc.file_size)
            buf = io.BytesIO()
            try:
                await bot.download(doc.file_id, destination=buf)
            except Exception as e:
                if "too big" in str(e).lower():
                    raise MediaTooLargeError(doc.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
                raise
            return buf.getvalue(), "application/pdf", caption or doc.file_name or "kitob.pdf", "pdf"

        is_video = mime.startswith("video/") or any(
            name.endswith(ext)
            for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]
        )
        is_gif = mime == "image/gif" or name.endswith(".gif")
        is_image = mime.startswith("image/") or any(
            name.endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"]
        )

        if is_video or is_gif or is_image:
            if doc.file_size and doc.file_size > MAX_MEDIA_SIZE_BYTES:
                raise MediaTooLargeError(doc.file_size)
            buf = io.BytesIO()
            try:
                await bot.download(doc.file_id, destination=buf)
            except Exception as e:
                if "too big" in str(e).lower():
                    raise MediaTooLargeError(doc.file_size or (MAX_MEDIA_SIZE_BYTES + 1))
                raise
            kind = "video" if is_video else ("gif" if is_gif else "image")
            resolved_mime = mime or (
                "video/mp4" if is_video else ("image/gif" if is_gif else "image/jpeg")
            )
            return buf.getvalue(), resolved_mime, caption, kind

    return None


async def _process_and_reply_media(
    source_msg: Message, reply_target: Message, bot: Bot
) -> bool:
    try:
        media_info = await _extract_media_from_message(source_msg, bot)
    except MediaTooLargeError as err:
        size_info = f" ({err.size_mb:.1f} MB)" if err.size_mb > 0 else ""
        await reply_target.reply(
            f"⚠️ <b>Media hajmi juda katta{size_info}!</b>\n\n"
            f"Telegram bot orqali maksimal <b>{MAX_MEDIA_SIZE_MB} MB</b> gacha bo'lgan video, GIF va fayllarni tarjima qila olaman. "
            "Iltimos, hajmi 20 MB dan kichik bo'lgan media yuboring."
        )
        return True

    if not media_info:
        return False

    media_bytes, mime_type, extra_context, media_kind = media_info

    if reply_target.from_user:
        await upsert_user(
            user_id=reply_target.from_user.id,
            first_name=reply_target.from_user.first_name or "",
            last_name=reply_target.from_user.last_name or "",
            username=reply_target.from_user.username or "",
            increment_count=True,
        )

    # Special handling for PDF documents & books
    if media_kind == "pdf":
        clean_caches()
        pdf_job_id = uuid.uuid4().hex[:10]
        pdf_cache[pdf_job_id] = {
            "bytes": media_bytes,
            "name": extra_context or "kitob.pdf",
            "time": time.time(),
        }
        size_mb = len(media_bytes) / (1024 * 1024)
        pdf_caption = (
            f"📚 <b>PDF Hujjat / Kitob qabul qilindi:</b>\n"
            f"📄 Nomi: <code>{html.escape(extra_context or 'kitob.pdf')}</code>\n"
            f"💾 Hajmi: <b>{size_mb:.2f} MB</b>\n\n"
            "👇 <b>Qanday tarjima qilishni xohlaysiz? Quyidagi variantlardan birini tanlang:</b>"
        )
        pdf_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📖 1-10 betlar tarjimasi",
                        callback_data=f"pdf:p10:{pdf_job_id}",
                    ),
                    InlineKeyboardButton(
                        text="📝 Kitob tahlili & Mazmuni",
                        callback_data=f"pdf:sum:{pdf_job_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📄 1-bob / Boshlang'ich qism",
                        callback_data=f"pdf:ch1:{pdf_job_id}",
                    ),
                    InlineKeyboardButton(
                        text="📑 To'liq fayl (.txt)",
                        callback_data=f"pdf:file:{pdf_job_id}",
                    ),
                ],
            ]
        )
        await reply_target.reply(pdf_caption, reply_markup=pdf_markup)
        return True

    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

    status_msg = None
    if media_kind in ("video", "gif") and media_bytes and len(media_bytes) > 500 * 1024:
        action_name = "Video" if media_kind == "video" else "GIF"
        try:
            status_msg = await reply_target.reply(
                f"⏳ <i>{action_name} tahlil qilinmoqda, kuting...</i>"
            )
        except Exception:
            pass
    elif media_kind in ("voice", "audio"):
        try:
            status_msg = await reply_target.reply(
                "🎙 <i>Ovoz eshitilib, tarjima qilinmoqda...</i>"
            )
        except Exception:
            pass

    try:
        res = await smart_translate_media(
            media_bytes=media_bytes,
            mime_type=mime_type,
            extra_context=extra_context,
            media_kind=media_kind,
            gemini_api_key=gemini_key,
        )
        src_name = res["src_name"]
        src_flag = res["flag"]
        dst_name = res.get("dst_name", "O'zbekcha")
        dst_flag = res.get("dst_flag", "🇺🇿")
        translated = res["translated"]

        clean_caches()
        job_id = uuid.uuid4().hex[:10]
        translation_cache[job_id] = {
            "original": extra_context or translated,
            "translated": translated,
            "src_lang": res.get("src_lang", "auto"),
            "src_name": src_name,
            "src_flag": src_flag,
            "dst_lang": res.get("dst_lang", "uz"),
            "dst_name": dst_name,
            "dst_flag": dst_flag,
            "time": time.time(),
        }
        markup = build_translation_markup(job_id, res.get("dst_lang", "uz"))

        response_text = (
            f"{src_flag} <b>{src_name} ➔ {dst_flag} {dst_name}:</b>\n\n"
            f"<code>{html.escape(translated)}</code>"
        )
        if status_msg:
            await status_msg.edit_text(response_text, reply_markup=markup)
        else:
            await reply_target.reply(response_text, reply_markup=markup)
    except Exception:
        logger.exception("Media translation error")
        err_msg = "❌ Ushbu mediani tarjima qilishda xatolik yuz berdi. Iltimos qaytadan urinib ko'ring."
        if status_msg:
            await status_msg.edit_text(err_msg)
        else:
            await reply_target.reply(err_msg)
    return True


@router.message(
    F.photo
    | F.sticker
    | F.animation
    | F.video
    | F.video_note
    | F.voice
    | F.audio
    | F.document
)
async def handle_media_translation(message: Message, bot: Bot) -> None:
    # If document, ensure it is supported media
    if message.document:
        doc = message.document
        mime = (doc.mime_type or "").lower()
        name = (doc.file_name or "").lower()
        is_supported = (
            mime.startswith("image/")
            or mime.startswith("video/")
            or mime.startswith("audio/")
            or mime in ("image/gif", "application/pdf")
            or any(
                name.endswith(ext)
                for ext in [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".gif",
                    ".mp4",
                    ".mov",
                    ".avi",
                    ".mkv",
                    ".webm",
                    ".pdf",
                    ".mp3",
                    ".ogg",
                    ".wav",
                    ".m4a",
                ]
            )
        )
        if not is_supported:
            return

    is_group = message.chat.type in ["group", "supergroup"]
    if is_group:
        bot_tag = f"@{BOT_USERNAME}".lower()
        caption_text = (message.caption or "").strip()
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.username
            and message.reply_to_message.from_user.username.lower()
            == BOT_USERNAME.lower()
        )
        is_tagged = bot_tag in caption_text.lower() or caption_text.lower().startswith(
            "/tr"
        )
        if not (is_tagged or is_reply_to_bot):
            return

    await _process_and_reply_media(message, message, bot)


@router.message(F.text)
async def handle_translation(message: Message, bot: Bot) -> None:
    text = message.text.strip()
    if not text:
        return

    is_group = message.chat.type in ["group", "supergroup"]
    if is_group:
        bot_tag = f"@{BOT_USERNAME}".lower()
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.username
            and message.reply_to_message.from_user.username.lower()
            == BOT_USERNAME.lower()
        )
        is_tagged = bot_tag in text.lower()

        if not (is_tagged or is_reply_to_bot or text.startswith("/tr")):
            return

        text = _clean_bot_mention(text)

        if not text and message.reply_to_message:
            if await _process_and_reply_media(message.reply_to_message, message, bot):
                return
            if message.reply_to_message.text:
                text = message.reply_to_message.text.strip()
            elif message.reply_to_message.caption:
                text = message.reply_to_message.caption.strip()

    if not text or text.startswith("/"):
        return

    if message.from_user:
        await upsert_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name or "",
            last_name=message.from_user.last_name or "",
            username=message.from_user.username or "",
            increment_count=True,
        )

    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

    try:
        res = await smart_translate(text, gemini_api_key=gemini_key)
        src_name = res["src_name"]
        src_flag = res["flag"]
        dst_name = res.get("dst_name", "O'zbekcha")
        dst_flag = res.get("dst_flag", "🇺🇿")
        translated = res["translated"]

        clean_caches()
        job_id = uuid.uuid4().hex[:10]
        translation_cache[job_id] = {
            "original": text,
            "translated": translated,
            "src_lang": res.get("src_lang", "auto"),
            "src_name": src_name,
            "src_flag": src_flag,
            "dst_lang": res.get("dst_lang", "uz"),
            "dst_name": dst_name,
            "dst_flag": dst_flag,
            "time": time.time(),
        }
        markup = build_translation_markup(job_id, res.get("dst_lang", "uz"))

        response_text = (
            f"{src_flag} <b>{src_name} ➔ {dst_flag} {dst_name}:</b>\n\n"
            f"<code>{html.escape(translated)}</code>"
        )
        await message.reply(response_text, reply_markup=markup)
    except Exception:
        logger.exception("Translation error")
        await message.reply(
            "❌ Matnni tarjima qilishda xatolik yuz berdi. Iltimos qaytadan urinib ko'ring."
        )


@router.callback_query(F.data.startswith("tts:"))
async def on_tts_click(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 1)
    if len(parts) != 2:
        return
    job_id = parts[1]
    item = translation_cache.get(job_id)
    if not item:
        await callback.answer(
            "⚠️ Ushbu tarjima muddati o'tgan yoki topilmadi.", show_alert=True
        )
        return

    await callback.answer("🔊 Audio tayyorlanmoqda...")
    text_to_speak = item.get("translated", "")
    dst_lang = item.get("dst_lang", "uz")

    audio_bytes = await generate_speech(text_to_speak, dst_lang)
    if not audio_bytes:
        await callback.answer(
            "❌ Ovoz hosil qilishda xatolik yuz berdi.", show_alert=True
        )
        return

    lang_name = LANG_NAMES.get(dst_lang, (dst_lang.upper(), "🌐"))[0]
    preview = (
        text_to_speak[:120] + "..." if len(text_to_speak) > 120 else text_to_speak
    )
    caption = f"🔊 <b>Talaffuz ({lang_name}):</b>\n<code>{html.escape(preview)}</code>"
    try:
        await callback.message.reply_voice(
            voice=BufferedInputFile(
                audio_bytes, filename=f"pronunciation_{dst_lang}.ogg"
            ),
            caption=caption,
        )
    except Exception as e:
        logger.warning(f"reply_voice failed, falling back to reply_audio: {e}")
        await callback.message.reply_audio(
            audio=BufferedInputFile(
                audio_bytes, filename=f"pronunciation_{dst_lang}.mp3"
            ),
            caption=caption,
        )


@router.callback_query(F.data.startswith("lang:"))
async def on_lang_switch(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, target_code, job_id = parts
    item = translation_cache.get(job_id)
    if not item:
        await callback.answer(
            "⚠️ Ushbu tarjima muddati o'tgan yoki topilmadi.", show_alert=True
        )
        return

    target_name = LANG_NAMES.get(target_code, (target_code.upper(), "🌐"))[0]
    await callback.answer(f"🔄 {target_name}ga o'girilmoqda...")

    original_text = item.get("original", "")
    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

    res = await translate_to_language(
        original_text, target_code, gemini_api_key=gemini_key
    )
    new_tr = res["translated"]

    item["translated"] = new_tr
    item["dst_lang"] = target_code

    src_name = item.get("src_name", "Matn")
    src_flag = item.get("src_flag", "🌐")
    dst_name = res.get("dst_name", target_name)
    dst_flag = res.get("dst_flag", "🌐")

    response_text = (
        f"{src_flag} <b>{src_name} ➔ {dst_flag} {dst_name}:</b>\n\n"
        f"<code>{html.escape(new_tr)}</code>"
    )
    markup = build_translation_markup(job_id, target_code)
    try:
        await callback.message.edit_text(response_text, reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith("pdf:"))
async def on_pdf_action(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, action, job_id = parts
    pdf_item = pdf_cache.get(job_id)
    if not pdf_item:
        await callback.answer(
            "⚠️ PDF fayl muddati o'tgan. Iltimos faylni qaytadan yuboring.",
            show_alert=True,
        )
        return

    action_names = {
        "p10": "Dastlabki 1-10 sahifalar tarjimasi",
        "sum": "Kitob tahlili va to'liq mazmuni",
        "ch1": "1-bob tarjimasi",
        "file": "To'liq o'zbekcha matnli fayl",
    }
    desc = action_names.get(action, "Tarjima")
    await callback.answer(f"⏳ {desc} tayyorlanmoqda, iltimos kuting...")

    status_msg = await callback.message.reply(
        f"⏳ <b>{desc} tayyorlanmoqda, biroz kuting...</b>"
    )

    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)
    result_text = await smart_translate_pdf(
        pdf_item["bytes"], action=action, gemini_api_key=gemini_key
    )

    if action == "file" or len(result_text) > 4000:
        doc_bytes = result_text.encode("utf-8")
        base_name = os.path.splitext(pdf_item.get("name", "kitob"))[0]
        out_filename = f"{base_name}_Uzbekcha.txt"
        caption = f"✅ <b>{desc} tayyor!</b>\n📄 Fayl: <code>{out_filename}</code>"
        await callback.message.reply_document(
            document=BufferedInputFile(doc_bytes, filename=out_filename),
            caption=caption,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    else:
        await status_msg.edit_text(f"📖 <b>{desc}:</b>\n\n{result_text}")


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

    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

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
                message_text=html.escape(result_text)
            ),
        )
        await inline_query.answer([article], cache_time=5)
    except Exception:
        pass


async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text=f"Tarjimon bot {DEPLOY_VERSION} is live and healthy!")


async def start_web_server() -> None:
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server running on port {port}")


async def _keep_alive_loop(render_url: str) -> None:
    ping_url = f"{render_url}/health"
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    while True:
        await asyncio.sleep(240)
        try:
            conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=conn) as s:
                async with s.get(ping_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    logger.debug(f"Keep-alive ping status: {r.status}")
        except Exception:
            pass


async def run_startup_diagnostics(bot: Bot) -> None:
    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)
    try:
        r1 = await smart_translate(
            "People who aren't girls piss me off", gemini_api_key=gemini_key
        )
        r2 = await smart_translate("Yangi ovqat qildim", gemini_api_key=gemini_key)
        r3 = await smart_translate("Chichvor", gemini_api_key=gemini_key)
        diag = (
            f"🇺🇿🇬🇧 {DEPLOY_VERSION} | "
            f"1:{r1['translated']} | "
            f"2:{r2['translated']} | "
            f"3:{r3['translated']}"
        )[:118]
        await bot.set_my_short_description(short_description=diag)
        logger.info(f"Startup diagnostics passed: {diag}")
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

    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    port = int(os.getenv("PORT", 8080))

    if render_url:
        webhook_path = "/webhook"
        webhook_url = f"{render_url}{webhook_path}"
        try:
            desc_text = (
                "🇺🇿🇬🇧 Istalgan tildan O'zbekchaga va O'zbekchadan Inglizchaga sun'iy intellektli tezkor tarjimon bot!"
            )
            await bot.set_my_description(description=desc_text)
        except Exception:
            pass

        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "inline_query", "callback_query"],
            drop_pending_updates=False,
        )
        logger.info(f"Telegram Webhook o'rnatildi: {webhook_url}")

        app = web.Application()
        app.router.add_get("/", handle_ping)
        app.router.add_get("/health", handle_ping)

        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=webhook_path)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        asyncio.create_task(_keep_alive_loop(render_url))
        logger.info(
            f"Tarjimon bot Webhook rejimida ishga tushdi: @{me.username} [{port}-port] [{DEPLOY_VERSION}]"
        )
        await asyncio.Event().wait()
    else:
        await bot.delete_webhook(drop_pending_updates=False)
        await start_web_server()
        logger.info(
            f"Tarjimon bot Polling rejimida ishga tushdi: @{me.username} ({me.full_name}) [{DEPLOY_VERSION}]"
        )
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
