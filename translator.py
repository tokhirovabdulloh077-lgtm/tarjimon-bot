import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import Dict, Tuple

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


def _translate_sync(text: str, target_lang: str = "uz") -> Tuple[str, str]:
    """Translate text using multi-layer fallback strategy (Google Chrome API -> Google GTX -> MyMemory)."""
    # Layer 1: Google Chrome extension API (Fastest and never rate-limited in datacenters)
    try:
        url1 = (
            "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl="
            + target_lang
            + "&q="
            + urllib.parse.quote(text)
        )
        req1 = urllib.request.Request(
            url1,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req1, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], list):
            tr_text = data[0][0]
            src_lang = data[0][1] if len(data[0]) > 1 else "auto"
            if tr_text:
                return tr_text, src_lang
    except Exception as e:
        logger.warning(f"Layer 1 translate error: {e}")

    # Layer 2: Google GTX endpoint
    try:
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }
        url2 = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
        req2 = urllib.request.Request(
            url2,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req2, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translated_text = "".join(
            [segment[0] for segment in data[0] if segment and segment[0]]
        )
        src_lang = data[2] if len(data) > 2 and data[2] else "auto"
        if translated_text:
            return translated_text, src_lang
    except Exception as e:
        logger.warning(f"Layer 2 translate error: {e}")

    # Layer 3: MyMemory API fallback
    try:
        url3 = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair=autodetect|{target_lang}"
        req3 = urllib.request.Request(
            url3,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        with urllib.request.urlopen(req3, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tr3 = data.get("responseData", {}).get("translatedText")
        if tr3:
            return tr3, "auto"
    except Exception as e:
        logger.warning(f"Layer 3 translate error: {e}")

    raise RuntimeError("Barcha tarjima xizmatlari javob bermadi.")


async def translate_text(text: str) -> Dict[str, str]:
    """Translate any text into Uzbek. If already in Uzbek, also provide English translation."""
    clean_text = text.strip()
    if not clean_text:
        return {"translated": "", "src_lang": "", "src_name": "", "flag": ""}

    translated, src_lang = await asyncio.to_thread(_translate_sync, clean_text, "uz")

    # If already Uzbek, provide English translation
    extra_en = ""
    if src_lang == "uz":
        try:
            en_trans, _ = await asyncio.to_thread(_translate_sync, clean_text, "en")
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
