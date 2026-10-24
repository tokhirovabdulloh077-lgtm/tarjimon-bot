import asyncio
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


async def _translate_api(text: str, target_lang: str = "uz") -> Tuple[str, str]:
    """Async translation using multiple fallbacks with disabled SSL strictness for Linux containers."""
    connector = aiohttp.TCPConnector(ssl=False)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        # Layer 1: Google Chrome extension API
        try:
            url1 = (
                "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl="
                + target_lang
                + "&q="
                + urllib.parse.quote(text)
            )
            async with session.get(url1, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if isinstance(data, list) and data and isinstance(data[0], list):
                        tr_text = data[0][0]
                        src_lang = data[0][1] if len(data[0]) > 1 else "auto"
                        if tr_text:
                            return tr_text, src_lang
        except Exception as e:
            logger.warning(f"Layer 1 error: {e}")

        # Layer 2: Google GTX single endpoint
        try:
            params = {
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": text,
            }
            url2 = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
            async with session.get(url2, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data2 = await resp.json(content_type=None)
                    tr_text2 = "".join([seg[0] for seg in data2[0] if seg and seg[0]])
                    src_lang2 = data2[2] if len(data2) > 2 and data2[2] else "auto"
                    if tr_text2:
                        return tr_text2, src_lang2
        except Exception as e:
            logger.warning(f"Layer 2 error: {e}")

        # Layer 3: MyMemory API fallback
        try:
            url3 = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair=autodetect|{target_lang}"
            async with session.get(url3, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data3 = await resp.json(content_type=None)
                    tr3 = data3.get("responseData", {}).get("translatedText")
                    if tr3 and "MYMEMORY WARNING" not in tr3:
                        return tr3, "auto"
        except Exception as e:
            logger.warning(f"Layer 3 error: {e}")

    return text, "auto"


async def translate_text(text: str) -> Dict[str, str]:
    """Translate any text into Uzbek. If already in Uzbek, also provide English translation."""
    clean_text = text.strip()
    if not clean_text:
        return {"translated": "", "src_lang": "", "src_name": "", "flag": ""}

    translated, src_lang = await _translate_api(clean_text, "uz")

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
