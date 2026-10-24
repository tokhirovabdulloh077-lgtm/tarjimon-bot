import asyncio
import json
import urllib.parse
import urllib.request
from typing import Dict, Tuple

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
    """Translate text to target_lang using Google GTX free engine."""
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_lang,
        "dt": "t",
        "q": text,
    }
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    translated_text = "".join(
        [segment[0] for segment in data[0] if segment and segment[0]]
    )
    src_lang = data[2] if len(data) > 2 and data[2] else "auto"
    return translated_text, src_lang


async def translate_text(text: str) -> Dict[str, str]:
    """Translate any text into Uzbek. If already in Uzbek, also provide English translation."""
    clean_text = text.strip()
    if not clean_text:
        return {"translated": "", "src_lang": "", "src_name": "", "flag": ""}

    translated, src_lang = await asyncio.to_thread(_translate_sync, clean_text, "uz")

    # If the user wrote in Uzbek, translate to English so they can also use it in reverse!
    extra_en = ""
    if src_lang == "uz":
        en_trans, _ = await asyncio.to_thread(_translate_sync, clean_text, "en")
        extra_en = en_trans

    name, flag = LANG_NAMES.get(src_lang, (src_lang.upper(), "🌐"))

    return {
        "original": clean_text,
        "translated": translated,
        "src_lang": src_lang,
        "src_name": name,
        "flag": flag,
        "extra_en": extra_en,
    }
