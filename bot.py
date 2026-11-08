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
    export_full_translation_history,
    get_all_user_ids,
    get_recent_users,
    get_setting,
    get_stats,
    get_translation_history,
    get_user_languages,
    init_db,
    is_admin,
    record_translation_history,
    set_setting,
    set_user_native_lang,
    set_user_target_lang,
    upsert_user,
)
from translator import (
    LANG_NAMES,
    TOP_LANG_CODES,
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
DEPLOY_VERSION = "v6.5-history-pro"
MAX_MEDIA_SIZE_MB = 20
MAX_MEDIA_SIZE_BYTES = MAX_MEDIA_SIZE_MB * 1024 * 1024

translation_cache: Dict[str, Dict] = {}
pdf_cache: Dict[str, Dict] = {}

ALL_SORTED_LANGUAGES = sorted(
    [
        (code, name, flag)
        for code, (name, flag) in LANG_NAMES.items()
        if code not in ("zh-CN", "iw")
    ],
    key=lambda x: x[1],
)


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


def build_top_lang_markup(
    current_code: str, mode: str = "target"
) -> InlineKeyboardMarkup:
    rows = []
    current_row = []
    for code in TOP_LANG_CODES:
        name, flag = LANG_NAMES.get(code, (code.upper(), "🌐"))
        check = " ✅" if code == current_code else ""
        btn_text = f"{flag} {name}{check}"
        current_row.append(
            InlineKeyboardButton(
                text=btn_text, callback_data=f"set_lang:{mode}:{code}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    nav_row = [
        InlineKeyboardButton(
            text="📋 Barcha tillar (A-Z)", callback_data=f"lang_page:{mode}:0"
        ),
    ]
    if mode == "target":
        nav_row.append(
            InlineKeyboardButton(
                text="🏠 Ona tilimni sozlash", callback_data="lang_menu:native"
            )
        )
    else:
        nav_row.append(
            InlineKeyboardButton(
                text="🎯 Maqsadli tilga qaytish", callback_data="lang_menu:target"
            )
        )
    rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_all_lang_markup(
    current_code: str, page: int = 0, mode: str = "target"
) -> InlineKeyboardMarkup:
    per_page = 8
    total_pages = (len(ALL_SORTED_LANGUAGES) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    page_items = ALL_SORTED_LANGUAGES[start : start + per_page]

    rows = []
    current_row = []
    for code, name, flag in page_items:
        check = " ✅" if code == current_code else ""
        btn_text = f"{flag} {name}{check}"
        current_row.append(
            InlineKeyboardButton(
                text=btn_text, callback_data=f"set_lang:{mode}:{code}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Oldingi", callback_data=f"lang_page:{mode}:{page - 1}"
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"📄 {page + 1}/{total_pages}", callback_data="noop"
        )
    )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Keyingi ➡️", callback_data=f"lang_page:{mode}:{page + 1}"
            )
        )
    rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Top tillarga qaytish", callback_data=f"lang_menu:{mode}"
            ),
            InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    row3 = [
        InlineKeyboardButton(
            text="🌐 Barcha tillar", callback_data="lang_menu:target"
        ),
        InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="menu:settings"),
    ]

    return InlineKeyboardMarkup(inline_keyboard=[[tts_btn], row1, row2, row3])


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
    user_id = message.from_user.id if message.from_user else 0
    first_name = message.from_user.first_name or "" if message.from_user else ""
    last_name = message.from_user.last_name or "" if message.from_user else ""
    username = message.from_user.username or "" if message.from_user else ""

    if user_id:
        await upsert_user(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            increment_count=False,
        )

    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    user_display = first_name or "Do'stim"
    text = (
        f"👋 <b>Assalomu alaykum, {html.escape(user_display)}!</b>\n"
        f"<b>Universal Sun'iy Intellektli Tarjimon botiga xush kelibsiz!</b> 🌍🤖\n\n"
        f"🎯 <b>Sizning hozirgi yo'nalishingiz:</b>\n"
        f"• {native_flag} <b>{native_name} yozsangiz ➔</b> {target_flag} <b>{target_name}</b> tiliga o'giriladi\n"
        f"• 🌍 <b>Boshqa tillarda yozsangiz ➔</b> {native_flag} <b>{native_name}</b> tiliga o'giriladi\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Bot qanday imkoniyatlarga ega?</b>\n"
        f"💬 <b>Matn:</b> Adabiy, qisqartma, so'zlashuv va ko'cha slenglarini tabiiy tarjima qiladi.\n"
        f"🎙 <b>Ovozli xabar (Voice):</b> Ovozli xabardagi nutqni eshitib, matnga o'giradi va tarjima qiladi.\n"
        f"🖼 <b>Rasm / Skrinshot:</b> Rasmdagi har qanday yozuvni o'qib, aniq tarjima qiladi.\n"
        f"🎭 <b>Stiker:</b> Yozuvli va hissiyotli stikerlar ma'nosini tarjima qiladi.\n"
        f"🎞 <b>GIF va Video:</b> Kadrdagi subtitrlar va dialoglarni tarjima qiladi (20 MB gacha).\n"
        f"📚 <b>PDF Kitob va Hujjat:</b> <i>Martin Iden</i> va b. kitoblarni tahlil qiladi, 1-10 betlarini yoki matnli fayl qilib beradi.\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✨ <b>Qo'shimcha qulayliklar:</b>\n"
        f"🔊 <b>Jonli Talaffuz:</b> Har bir tarjima ostida <i>🔊 Talaffuz</i> tugmasi orqali sun'iy intellekt ovozida eshitish.\n"
        f"🔄 <b>1-bosishda o'girish:</b> Tarjima qilingan matnni boshqa istalgan tilga tezkor o'tkazish.\n"
        f"⚡️ <b>Inline rejim:</b> Istalgan chatda <code>@{BOT_USERNAME or 'tarjima01_bot'} so'z</code> deb yozib tarjima qilish.\n\n"
        f"👇 <b>O'zingizga qulay tilni tanlang yoki to'g'ridan-to'g'ri xabar yuboring!</b>"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Tarjima tilini sozlash",
                    callback_data="lang_menu:target",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Kitob / PDF qo'llanma",
                    callback_data="menu:pdf_help",
                ),
                InlineKeyboardButton(
                    text="⚙️ Sozlamalarim", callback_data="menu:settings"
                ),
            ],
        ]
    )
    await message.answer(text, reply_markup=markup)


@router.message(Command("lang"))
async def cmd_lang(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    text = (
        f"🌐 <b>Tarjima tili sozlamalari</b>\n\n"
        f"📌 <b>Hozirgi sozlamangiz:</b>\n"
        f"• {native_flag} <b>{native_name}</b> yozsangiz ➔ {target_flag} <b>{target_name}</b> tiliga o'giriladi!\n"
        f"• 🌍 <b>Boshqa tillarda</b> yozsangiz ➔ {native_flag} <b>{native_name}</b> tiliga o'giriladi.\n\n"
        f"👇 <b>{native_name} so'zlaringiz qaysi tilga tarjima qilinishini tanlang:</b>"
    )
    markup = build_top_lang_markup(target_lang, mode="target")
    await message.answer(text, reply_markup=markup)


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        return
    target_lang, native_lang = await get_user_languages(user_id)
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    user_name = message.from_user.first_name or "Foydalanuvchi"

    text = (
        f"⚙️ <b>Sizning shaxsiy sozlamalaringiz:</b>\n\n"
        f"👤 <b>Ism:</b> {html.escape(user_name)}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"🎯 <b>Maqsadli til:</b> {target_flag} {target_name}\n"
        f"🏠 <b>Ona tilingiz:</b> {native_flag} {native_name}\n\n"
        f"👇 <i>Tilni o'zgartirish uchun kerakli tugmani bosing:</i>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Maqsadli tilni o'zgartirish",
                    callback_data="lang_menu:target",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Ona tilimni o'zgartirish",
                    callback_data="lang_menu:native",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Bosh sahifa", callback_data="menu:home"
                )
            ],
        ]
    )
    await message.answer(text, reply_markup=markup)


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
        "💡 <b>Admin buyruqlari:</b>\n"
        "• /history — Jonli tarjimalar tarixi (Monitoring)\n"
        "• /export_history — Tarixni fayl (.txt) qilib yuklash\n"
        "• /users — Foydalanuvchilar ro'yxati\n"
        "• /broadcast &lt;matn&gt; — Barchaga xabar yuborish"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Tarjimalar tarixi", callback_data="hist_page:0"
                ),
                InlineKeyboardButton(
                    text="📥 Fayl qilib olish (.txt)",
                    callback_data="hist_export",
                ),
            ]
        ]
    )
    await message.reply(text, reply_markup=markup)


async def build_history_view(
    page: int = 0, user_filter: str = ""
) -> Tuple[str, InlineKeyboardMarkup]:
    per_page = 6
    offset = page * per_page
    rows, total = await get_translation_history(
        limit=per_page, offset=offset, user_filter=user_filter
    )

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    if not rows:
        text = (
            "📭 <b>Hozircha yangi tarjimalar tarixi mavjud emas.</b>\n\n"
            "Foydalanuvchilar botga matn, audio, rasm yoki video yuborishi bilan bu yerda real vaqtda ko'rina boshlaydi!"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Yangilash", callback_data="hist_page:0"
                    )
                ]
            ]
        )
        return text, markup

    filter_note = (
        f" (Filtr: <code>{html.escape(user_filter)}</code>)"
        if user_filter
        else ""
    )
    lines = [
        f"📜 <b>Jonli Tarjimalar Tarixi{filter_note}</b>\n"
        f"<i>Jami yozuvlar: {total} ta | Sahifa: {page + 1}/{total_pages}</i>\n"
    ]

    media_icons = {
        "text": "💬 Matn",
        "voice": "🎙 Ovozli xabar",
        "audio": "🎵 Audio",
        "image": "🖼 Rasm",
        "sticker": "🎭 Stiker",
        "gif": "🎞 GIF",
        "video": "🎬 Video",
        "pdf": "📚 PDF Kitob",
        "button_switch": "🔄 Til almashtirish",
    }

    for idx, r in enumerate(rows, start=offset + 1):
        uname = (
            f"@{r['username']}"
            if r["username"]
            else f"<code>ID: {r['user_id']}</code>"
        )
        name = html.escape(r["first_name"] or "Nomsiz")
        kind_label = media_icons.get(r["media_kind"], f"📁 {r['media_kind']}")
        src_n, src_f = LANG_NAMES.get(
            r["src_lang"], (r["src_lang"].upper(), "🌐")
        )
        dst_n, dst_f = LANG_NAMES.get(
            r["dst_lang"], (r["dst_lang"].upper(), "🌐")
        )

        orig_clean = html.escape(r["original_text"] or "")
        trans_clean = html.escape(r["translated_text"] or "")
        if len(orig_clean) > 80:
            orig_clean = orig_clean[:80] + "..."
        if len(trans_clean) > 80:
            trans_clean = trans_clean[:80] + "..."

        lines.append(
            f"<b>{idx}. {name}</b> ({uname})\n"
            f"   🕒 <i>{r['created_at']}</i> | {kind_label}\n"
            f"   🌐 {src_f} ➔ {dst_f}\n"
            f"   📥 <b>Asl:</b> <code>{orig_clean}</code>\n"
            f"   📤 <b>Tarjima:</b> <code>{trans_clean}</code>\n"
        )

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Oldingi", callback_data=f"hist_page:{page - 1}"
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"📄 {page + 1}/{total_pages}", callback_data="noop"
        )
    )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Keyingi ➡️", callback_data=f"hist_page:{page + 1}"
            )
        )

    bottom_row = [
        InlineKeyboardButton(
            text="🔄 Yangilash", callback_data=f"hist_page:{page}"
        ),
        InlineKeyboardButton(
            text="📥 Fayl qilib olish (.txt)", callback_data="hist_export"
        ),
    ]

    kb = [nav_row, bottom_row] if nav_row else [bottom_row]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("history", "tarix"))
async def cmd_history(message: Message) -> None:
    if not await _ensure_user_and_check_admin(message):
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    parts = message.text.split(maxsplit=1)
    user_filter = parts[1].strip() if len(parts) > 1 else ""

    text, markup = await build_history_view(page=0, user_filter=user_filter)
    await message.reply(text, reply_markup=markup)


@router.message(Command("export_history", "tarix_fayl"))
async def cmd_export_history(message: Message) -> None:
    if not await _ensure_user_and_check_admin(message):
        await message.reply("🔒 <i>Kechirasiz, bu buyruq faqat bot admini uchun.</i>")
        return

    status_msg = await message.reply(
        "⏳ <i>Tarjimalar tarixi fayli tayyorlanmoqda...</i>"
    )
    content = await export_full_translation_history(limit=1000)
    doc_bytes = content.encode("utf-8")
    now_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"tarjimalar_tarixi_{now_tag}.txt"

    await message.reply_document(
        document=BufferedInputFile(doc_bytes, filename=filename),
        caption="✅ <b>Tarjimalar tarixi muvaffaqiyatli yuklandi!</b>",
    )
    try:
        await status_msg.delete()
    except Exception:
        pass


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

    user_id = reply_target.from_user.id if reply_target.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )

    try:
        res = await smart_translate_media(
            media_bytes=media_bytes,
            mime_type=mime_type,
            extra_context=extra_context,
            media_kind=media_kind,
            target_lang=target_lang,
            native_lang=native_lang,
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

        u_id = reply_target.from_user.id if reply_target.from_user else 0
        u_fn = (
            reply_target.from_user.first_name or ""
            if reply_target.from_user
            else ""
        )
        u_un = (
            reply_target.from_user.username or ""
            if reply_target.from_user
            else ""
        )
        await record_translation_history(
            user_id=u_id,
            first_name=u_fn,
            username=u_un,
            src_lang=res.get("src_lang", "auto"),
            dst_lang=res.get("dst_lang", "uz"),
            original_text=extra_context or f"[{media_kind}]",
            translated_text=translated,
            media_kind=media_kind,
        )

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

    user_id = message.from_user.id if message.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

    try:
        res = await smart_translate(
            text,
            target_lang=target_lang,
            native_lang=native_lang,
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

        u_id = message.from_user.id if message.from_user else 0
        u_fn = message.from_user.first_name or "" if message.from_user else ""
        u_un = message.from_user.username or "" if message.from_user else ""
        await record_translation_history(
            user_id=u_id,
            first_name=u_fn,
            username=u_un,
            src_lang=res.get("src_lang", "auto"),
            dst_lang=res.get("dst_lang", "uz"),
            original_text=text,
            translated_text=translated,
            media_kind="text",
        )

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

    u_id = callback.from_user.id if callback.from_user else 0
    u_fn = callback.from_user.first_name or "" if callback.from_user else ""
    u_un = callback.from_user.username or "" if callback.from_user else ""
    await record_translation_history(
        user_id=u_id,
        first_name=u_fn,
        username=u_un,
        src_lang=item.get("src_lang", "auto"),
        dst_lang=target_code,
        original_text=original_text,
        translated_text=new_tr,
        media_kind="button_switch",
    )


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

    u_id = callback.from_user.id if callback.from_user else 0
    u_fn = callback.from_user.first_name or "" if callback.from_user else ""
    u_un = callback.from_user.username or "" if callback.from_user else ""
    await record_translation_history(
        user_id=u_id,
        first_name=u_fn,
        username=u_un,
        src_lang="auto",
        dst_lang="uz",
        original_text=f"PDF ({desc}): {pdf_item.get('name', 'kitob')}",
        translated_text=result_text[:400] + "...",
        media_kind="pdf",
    )


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("hist_page:"))
async def on_hist_page(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    if not await is_admin(user_id=user_id):
        await callback.answer("🔒 Faqat bot admini uchun.", show_alert=True)
        return

    parts = callback.data.split(":")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    text, markup = await build_history_view(page=page)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "hist_export")
async def on_hist_export(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    if not await is_admin(user_id=user_id):
        await callback.answer("🔒 Faqat bot admini uchun.", show_alert=True)
        return

    await callback.answer("📥 Fayl tayyorlanmoqda...")
    content = await export_full_translation_history(limit=1000)
    doc_bytes = content.encode("utf-8")
    now_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"tarjimalar_tarixi_{now_tag}.txt"

    await callback.message.reply_document(
        document=BufferedInputFile(doc_bytes, filename=filename),
        caption="✅ <b>Tarjimalar tarixi fayl ko'rinishida yuklandi!</b>",
    )


@router.callback_query(F.data.startswith("lang_menu:"))
async def on_lang_menu(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    mode = parts[1] if len(parts) > 1 else "target"
    user_id = callback.from_user.id if callback.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    current_code = target_lang if mode == "target" else native_lang
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    if mode == "target":
        title = "🎯 <b>Maqsadli tilni tanlash</b>"
        desc = (
            f"• {native_flag} <b>{native_name} yozsangiz ➔</b> tanlangan tilga o'giriladi\n"
            f"• 🌍 <b>Boshqa tillarda yozsangiz ➔</b> {native_flag} {native_name} tiliga o'giriladi\n\n"
            f"👇 <i>{native_name} so'zlaringiz qaysi tilga tarjima qilinishini tanlang:</i>"
        )
    else:
        title = "🏠 <b>Ona tilingizni tanlash</b>"
        desc = (
            "• Chet tillaridagi xabarlar qaysi tilga tarjima qilinishini belgilang.\n\n"
            "👇 <i>Ona tilingizni tanlang:</i>"
        )

    text = f"{title}\n\n{desc}"
    markup = build_top_lang_markup(current_code, mode=mode)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("lang_page:"))
async def on_lang_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, mode, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    user_id = callback.from_user.id if callback.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    current_code = target_lang if mode == "target" else native_lang

    title = (
        "🎯 <b>Barcha jahon tillari (A-Z)</b>"
        if mode == "target"
        else "🏠 <b>Barcha tillar (A-Z)</b>"
    )
    text = (
        f"{title}\n\n"
        "Ro'yxatdan o'zingizga kerakli tilni tanlang:\n"
        "<i>(Sahifalar orasida o'tish uchun quyidagi tugmalardan foydalaning)</i>"
    )
    markup = build_all_lang_markup(current_code, page=page, mode=mode)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("set_lang:"))
async def on_set_lang(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, mode, code = parts
    user_id = callback.from_user.id if callback.from_user else 0
    if not user_id:
        return

    name, flag = LANG_NAMES.get(code, (code.upper(), "🌐"))
    if mode == "target":
        await set_user_target_lang(user_id, code)
        await callback.answer(
            f"✅ Maqsadli til: {flag} {name} tanlandi!", show_alert=False
        )
    else:
        await set_user_native_lang(user_id, code)
        await callback.answer(
            f"✅ Ona tili: {flag} {name} tanlandi!", show_alert=False
        )

    target_lang, native_lang = await get_user_languages(user_id)
    t_name, t_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    n_name, n_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    title = "Maqsadli til" if mode == "target" else "Ona tili"
    text = (
        f"✅ <b>{title} muvaffaqiyatli saqlandi: {flag} {name}!</b>\n\n"
        f"📌 <b>Yangi faol yo'nalishingiz:</b>\n"
        f"• {n_flag} <b>{n_name} yozsangiz ➔</b> {t_flag} <b>{t_name}</b> tiliga o'giriladi\n"
        f"• 🌍 <b>Boshqa tillarda yozsangiz ➔</b> {n_flag} <b>{n_name}</b> tiliga o'giriladi\n\n"
        f"✍️ <i>Istalgan matn, ovoz, rasm yoki video yuborib sinab ko'rishingiz mumkin!</i>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Boshqa tilni tanlash",
                    callback_data=f"lang_menu:{mode}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Bosh sahifa", callback_data="menu:home"
                )
            ],
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data == "menu:home")
async def on_menu_home(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    first_name = (
        callback.from_user.first_name or "Do'stim"
        if callback.from_user
        else "Do'stim"
    )
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    text = (
        f"👋 <b>Assalomu alaykum, {html.escape(first_name)}!</b>\n"
        f"<b>Universal Sun'iy Intellektli Tarjimon botiga xush kelibsiz!</b> 🌍🤖\n\n"
        f"🎯 <b>Sizning hozirgi yo'nalishingiz:</b>\n"
        f"• {native_flag} <b>{native_name} yozsangiz ➔</b> {target_flag} <b>{target_name}</b> tiliga o'giriladi\n"
        f"• 🌍 <b>Boshqa tillarda yozsangiz ➔</b> {native_flag} <b>{native_name}</b> tiliga o'giriladi\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Bot qanday imkoniyatlarga ega?</b>\n"
        f"💬 <b>Matn:</b> Adabiy, qisqartma, so'zlashuv va ko'cha slenglarini tabiiy tarjima qiladi.\n"
        f"🎙 <b>Ovozli xabar (Voice):</b> Ovozli xabardagi nutqni eshitib, matnga o'giradi va tarjima qiladi.\n"
        f"🖼 <b>Rasm / Skrinshot:</b> Rasmdagi har qanday yozuvni o'qib, aniq tarjima qiladi.\n"
        f"🎭 <b>Stiker:</b> Yozuvli va hissiyotli stikerlar ma'nosini tarjima qiladi.\n"
        f"🎞 <b>GIF va Video:</b> Kadrdagi subtitrlar va dialoglarni tarjima qiladi (20 MB gacha).\n"
        f"📚 <b>PDF Kitob va Hujjat:</b> <i>Martin Iden</i> va b. kitoblarni tahlil qiladi, 1-10 betlarini yoki matnli fayl qilib beradi.\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✨ <b>Qo'shimcha qulayliklar:</b>\n"
        f"🔊 <b>Jonli Talaffuz:</b> Har bir tarjima ostida <i>🔊 Talaffuz</i> tugmasi orqali sun'iy intellekt ovozida eshitish.\n"
        f"🔄 <b>1-bosishda o'girish:</b> Tarjima qilingan matnni boshqa istalgan tilga tezkor o'tkazish.\n"
        f"⚡️ <b>Inline rejim:</b> Istalgan chatda <code>@{BOT_USERNAME or 'tarjima01_bot'} so'z</code> deb yozib tarjima qilish.\n\n"
        f"👇 <b>O'zingizga qulay tilni tanlang yoki to'g'ridan-to'g'ri xabar yuboring!</b>"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Tarjima tilini sozlash",
                    callback_data="lang_menu:target",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Kitob / PDF qo'llanma",
                    callback_data="menu:pdf_help",
                ),
                InlineKeyboardButton(
                    text="⚙️ Sozlamalarim", callback_data="menu:settings"
                ),
            ],
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    user_name = (
        callback.from_user.first_name or "Foydalanuvchi"
        if callback.from_user
        else "Foydalanuvchi"
    )
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    target_name, target_flag = LANG_NAMES.get(target_lang, ("Inglizcha", "🇬🇧"))
    native_name, native_flag = LANG_NAMES.get(native_lang, ("O'zbekcha", "🇺🇿"))

    text = (
        f"⚙️ <b>Sizning shaxsiy sozlamalaringiz:</b>\n\n"
        f"👤 <b>Ism:</b> {html.escape(user_name)}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"🎯 <b>Maqsadli til:</b> {target_flag} {target_name}\n"
        f"🏠 <b>Ona tilingiz:</b> {native_flag} {native_name}\n\n"
        f"👇 <i>Tilni o'zgartirish uchun kerakli tugmani bosing:</i>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Maqsadli tilni o'zgartirish",
                    callback_data="lang_menu:target",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Ona tilimni o'zgartirish",
                    callback_data="lang_menu:native",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Bosh sahifa", callback_data="menu:home"
                )
            ],
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:pdf_help")
async def on_menu_pdf_help(callback: CallbackQuery) -> None:
    text = (
        "📚 <b>Kitoblar va PDF hujjatlar bilan ishlash qo'llanmasi:</b>\n\n"
        "Menga <b>Martin Iden</b> yoki istalgan kitob/hujjatning PDF faylini yuboring (20 MB gacha).\n"
        "Faylni yuklaganingizda, bot sizga 4 xil variant taqdim etadi:\n\n"
        "1. 📖 <b>1-10 betlar tarjimasi</b> — Kitobning dastlabki sahifalarini o'qib, o'zbek tiliga o'girib beradi.\n"
        "2. 📝 <b>Kitob tahlili & Mazmuni</b> — Kitobning to'liq syujeti, qahramonlar fe'l-atvori va bosh g'oyasini batafsil tahlil qiladi.\n"
        "3. 📄 <b>1-bob / Boshlang'ich qism</b> — Asarning kirish yoki birinchi bobini adabiy tarjima qiladi.\n"
        "4. 📑 <b>To'liq fayl (.txt)</b> — Telegram cheklovlarisiz to'liq matnli fayl ko'rinishida taqdim etadi.\n\n"
        "👇 <i>Sinab ko'rish uchun PDF faylni botga yuboring!</i>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Bosh sahifa", callback_data="menu:home"
                )
            ]
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer()


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

    user_id = inline_query.from_user.id if inline_query.from_user else 0
    target_lang, native_lang = (
        await get_user_languages(user_id) if user_id else ("en", "uz")
    )
    gemini_key = await get_setting("gemini_api_key", DEFAULT_GEMINI_KEY)

    try:
        res = await smart_translate(
            query,
            target_lang=target_lang,
            native_lang=native_lang,
            gemini_api_key=gemini_key,
        )
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


async def _configure_bot_profile(bot: Bot) -> None:
    try:
        await bot.set_my_commands(
            [
                BotCommand(
                    command="start",
                    description="Botni ishga tushirish va to'liq qo'llanma",
                ),
                BotCommand(
                    command="lang",
                    description="Tarjima tilini tanlash (Rus, Ingliz, Turk va b.)",
                ),
                BotCommand(
                    command="settings",
                    description="Mening sozlamalarim va faol tilim",
                ),
                BotCommand(
                    command="help",
                    description="Yordam va foydalanish yo'riqnomasi",
                ),
            ]
        )
    except Exception as e:
        logger.warning(f"set_my_commands error: {e}")

    try:
        short_desc = (
            "AI Tarjimon: Matn, Ovoz, Rasm, GIF, Video va PDF Kitoblar tarjimasi. "
            "🔊 Jonli talaffuz va 100+ tillar!"
        )[:120]
        await bot.set_my_short_description(short_description=short_desc)
    except Exception as e:
        logger.warning(f"set_my_short_description error: {e}")

    try:
        desc_text = (
            "👋 Universal AI Tarjimon botiga xush kelibsiz!\n\n"
            "Menga istalgan tildagi:\n"
            "💬 Matn va 🎙 Ovozli xabar (Voice)\n"
            "🖼 Rasm, Skrinshot va 🎭 Stikerlar\n"
            "🎞 GIF va 🎬 Video (subtitr va nutq)\n"
            "📚 PDF Kitoblar va Hujjatlar (Martin Iden va b.)\n\n"
            "✨ Imkoniyatlar:\n"
            "• 🔊 Jonli neyron talaffuz (ovozli eshitish)\n"
            "• 🌐 Istalgan tilni tanlash (Ingliz, Rus, Turk, Arab va 100+ til)\n"
            "• ⚡️ So'zlashuv, sleng va adabiy uslublarni tushunish\n\n"
            "Boshlash uchun pastdagi Start tugmasini bosing! 👇"
        )
        await bot.set_my_description(description=desc_text)
    except Exception as e:
        logger.warning(f"set_my_description error: {e}")


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
        )
        logger.info(f"Startup diagnostics passed: {diag}")
    except Exception as e:
        logger.exception(f"Startup diagnostics failed: {e}")


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

    await _configure_bot_profile(bot)
    await run_startup_diagnostics(bot)

    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    port = int(os.getenv("PORT", 8080))

    if render_url:
        webhook_path = "/webhook"
        webhook_url = f"{render_url}{webhook_path}"

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
