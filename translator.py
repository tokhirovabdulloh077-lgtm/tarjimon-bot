import html
import json
import logging
import re
import socket
import urllib.parse
from typing import Dict, Tuple

import aiohttp

logger = logging.getLogger("tarjimon_bot.translator")

LANG_NAMES: Dict[str, Tuple[str, str]] = {
    "en": ("Inglizcha", "🇬🇧"),
    "ru": ("Ruscha", "🇷🇺"),
    "tr": ("Turkcha", "🇹🇷"),
    "ar": ("Arabcha", "🇸🇦"),
    "de": ("Nemischa", "🇩🇪"),
    "fr": ("Fransuzcha", "🇫🇷"),
    "es": ("Ispancha", "🇪🇸"),
    "zh-CN": ("Xitoycha", "🇨🇳"),
    "zh": ("Xitoycha", "🇨🇳"),
    "ko": ("Koreyscha", "🇰🇷"),
    "ja": ("Yaponcha", "🇯🇵"),
    "fa": ("Forscha", "🇮🇷"),
    "hi": ("Hindcha", "🇮🇳"),
    "it": ("Italyancha", "🇮🇹"),
    "pt": ("Portugalcha", "🇵🇹"),
    "kk": ("Qozoqcha", "🇰🇿"),
    "ky": ("Qirg'izcha", "🇰🇬"),
    "tg": ("Tojikcha", "🇹🇯"),
    "az": ("Ozarbayjoncha", "🇦🇿"),
    "uz": ("O'zbekcha", "🇺🇿"),
}

UZBEK_WORDS = {
    "ha", "haa", "haaa", "xaa", "yoq", "yo'q", "salom", "qalesan", "qalaysiz", 
    "nima", "gap", "rahmat", "xop", "xo'p", "yaxshi", "qilyapsan", "ishlar", 
    "qalay", "tushundim", "bilmayman", "bormi", "kerak", "nega", "chunki", 
    "lekin", "ammo", "men", "sen", "biz", "siz", "bugun", "havo", "kecha", 
    "ertaga", "rahmat", "yaxshimisiz", "charchamang", "bopman", "boldi", "bo'ldi"
}


def is_likely_uzbek(text: str) -> bool:
    t = text.lower().strip()
    words = set(re.findall(r"\w+", t))
    if words & UZBEK_WORDS:
        return True
    if any(w in t for w in ["o'", "g'", "oʻ", "gʻ", "oʼ", "gʼ"]):
        return True
    return False


async def _call_google_rpc(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Tuple[str, str]:
    params = {
        "client": "gtx",
        "sl": sl,
        "tl": tl,
        "hl": "en",
        "dt": "t",
        "dj": "1",
        "q": text,
    }
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "GoogleTranslate/6.28.0.05.357121650 (Linux; U; Android 12; Pixel 6)",
        "Accept": "*/*",
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status == 200:
            data = await resp.json(content_type=None)
            sentences = data.get("sentences", [])
            tr = "".join([s.get("trans", "") for s in sentences if s.get("trans")])
            src = data.get("src", sl)
            if tr:
                return tr, src
    raise RuntimeError("Google RPC not 200")


async def _call_mymemory(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Tuple[str, str]:
    target_tag = "uz-UZ" if tl == "uz" else ("en-GB" if tl == "en" else tl)
    source_tag = "uz-UZ" if sl == "uz" else ("autodetect" if sl == "auto" else sl)
    url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair={source_tag}|{target_tag}&de=tokhirov077@gmail.com"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status == 200:
            data = await resp.json(content_type=None)
            tr = data.get("responseData", {}).get("translatedText")
            det = data.get("responseData", {}).get("detectedLanguage", sl)
            if tr and "MYMEMORY WARNING" not in tr:
                return html.unescape(tr), det
    raise RuntimeError("MyMemory failed")


async def smart_translate(text: str) -> Dict[str, str]:
    clean_text = text.strip()
    if not clean_text:
        return {"original": "", "translated": "", "src_lang": "", "src_name": "", "flag": "", "dst_lang": "uz"}

    # Force IPv4 to avoid broken IPv6 in cloud containers
    connector = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Check if text is likely Uzbek
        if is_likely_uzbek(clean_text):
            # Uzbek -> English
            tr_text = ""
            try:
                tr_text, _ = await _call_google_rpc(session, clean_text, "uz", "en")
            except Exception:
                try:
                    tr_text, _ = await _call_mymemory(session, clean_text, "uz", "en")
                except Exception:
                    tr_text = clean_text
            return {
                "original": clean_text,
                "translated": tr_text,
                "src_lang": "uz",
                "src_name": "O'zbekcha",
                "flag": "🇺🇿",
                "dst_lang": "en",
                "dst_name": "Inglizcha",
                "dst_flag": "🇬🇧",
            }

        # Any other language -> Uzbek
        tr_text = ""
        detected_src = "auto"
        try:
            tr_text, detected_src = await _call_google_rpc(session, clean_text, "auto", "uz")
        except Exception:
            try:
                tr_text, detected_src = await _call_mymemory(session, clean_text, "auto", "uz")
            except Exception:
                tr_text = clean_text

        # If detected language happens to be Uzbek, translate it to English!
        if detected_src == "uz":
            try:
                en_tr, _ = await _call_google_rpc(session, clean_text, "uz", "en")
                return {
                    "original": clean_text,
                    "translated": en_tr,
                    "src_lang": "uz",
                    "src_name": "O'zbekcha",
                    "flag": "🇺🇿",
                    "dst_lang": "en",
                    "dst_name": "Inglizcha",
                    "dst_flag": "🇬🇧",
                }
            except Exception:
                pass

        src_name, src_flag = LANG_NAMES.get(detected_src, (detected_src.upper(), "🌐"))
        return {
            "original": clean_text,
            "translated": tr_text or clean_text,
            "src_lang": detected_src,
            "src_name": src_name,
            "flag": src_flag,
            "dst_lang": "uz",
            "dst_name": "O'zbekcha",
            "dst_flag": "🇺🇿",
        }
