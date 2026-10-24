import asyncio
import html
import json
import logging
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

COMMON_UZBEK = {"ha", "haa", "haaa", "xaa", "yoq", "yo'q", "salom", "qalesan", "nima gap", "rahmat", "xop", "xo'p", "yaxshi"}


async def _translate_api(text: str, target_lang: str = "uz") -> Tuple[str, str]:
    """Bulletproof translation: Google Android App RPC -> MyMemory (uz-UZ) -> Chrome API."""
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Layer 1: Google Android Official App RPC (Fastest, never blocked, accepted worldwide)
        try:
            params = {
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "hl": "en",
                "dt": "t",
                "dj": "1",
                "q": text,
            }
            url1 = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
            headers1 = {
                "User-Agent": "GoogleTranslate/6.28.0.05.357121650 (Linux; U; Android 12; Pixel 6)",
                "Accept": "*/*",
            }
            async with session.get(url1, headers=headers1, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    sentences = data.get("sentences", [])
                    tr_text = "".join([s.get("trans", "") for s in sentences if s.get("trans")])
                    src_lang = data.get("src", "auto")
                    if tr_text:
                        return tr_text, src_lang
        except Exception as e:
            logger.warning(f"Layer 1 Android error: {e}")

        # Layer 2: MyMemory (with correct uz-UZ language tag)
        try:
            target_tag = "uz-UZ" if target_lang == "uz" else target_lang
            url2 = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair=autodetect|{target_tag}&de=tokhirov077@gmail.com"
            headers2 = {"User-Agent": "Mozilla/5.0"}
            async with session.get(url2, headers=headers2, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data2 = await resp.json(content_type=None)
                    tr2 = data2.get("responseData", {}).get("translatedText")
                    src_detected = data2.get("responseData", {}).get("detectedLanguage", "auto")
                    if tr2 and "MYMEMORY WARNING" not in tr2:
                        return html.unescape(tr2), src_detected
        except Exception as e:
            logger.warning(f"Layer 2 MyMemory error: {e}")

        # Layer 3: Chrome Extension API
        try:
            url3 = (
                "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl="
                + target_lang
                + "&q="
                + urllib.parse.quote(text)
            )
            headers3 = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
            async with session.get(url3, headers=headers3, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data3 = await resp.json(content_type=None)
                    if isinstance(data3, list) and data3 and isinstance(data3[0], list):
                        tr3 = data3[0][0]
                        src3 = data3[0][1] if len(data3[0]) > 1 else "auto"
                        if tr3:
                            return tr3, src3
        except Exception as e:
            logger.warning(f"Layer 3 Chrome error: {e}")

    return text, "auto"


async def translate_text(text: str) -> Dict[str, str]:
    """Translate any text into Uzbek. If already in Uzbek, also provide English translation."""
    clean_text = text.strip()
    if not clean_text:
        return {"translated": "", "src_lang": "", "src_name": "", "flag": ""}

    # Check if text is known Uzbek word
    is_known_uz = clean_text.lower() in COMMON_UZBEK

    translated, src_lang = await _translate_api(clean_text, "uz")

    if is_known_uz:
        src_lang = "uz"

    extra_en = ""
    if src_lang == "uz":
        try:
            en_trans, _ = await _translate_api(clean_text, "en")
            extra_en = en_trans
        except Exception:
            pass

    name, flag = LANG_NAMES.get(src_lang, (src_lang.upper(), "🌐"))

    return {
        "original": clean_text,
        "translated": translated,
        "src_lang": src_lang,
        "src_name": name,
        "flag": flag,
        "extra_en": extra_en,
    }
