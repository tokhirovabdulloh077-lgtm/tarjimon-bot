import html
import json
import logging
import re
import socket
import ssl
import urllib.parse
from typing import Dict, Optional, Tuple

import aiohttp
import certifi

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
    "fi": ("Fincha", "🇫🇮"),
    "id": ("Indonezcha", "🇮🇩"),
    "ms": ("Malaycha", "🇲🇾"),
    "nl": ("Gollandcha", "🇳🇱"),
    "pl": ("Polyakcha", "🇵🇱"),
    "uk": ("Ukraincha", "🇺🇦"),
    "cs": ("Chexcha", "🇨🇿"),
    "sv": ("Shvedcha", "🇸🇪"),
    "el": ("Grekcha", "🇬🇷"),
    "he": ("Ivritcha", "🇮🇱"),
    "iw": ("Ivritcha", "🇮🇱"),
    "th": ("Taycha", "🇹🇭"),
    "vi": ("Vyetnamcha", "🇻🇳"),
    "bn": ("Bengalcha", "🇧🇩"),
    "ur": ("Urducha", "🇵🇰"),
}

UZBEK_WORDS = {
    "ha", "haa", "haaa", "xaa", "yoq", "yo'q", "salom", "assalomu", "alaykum",
    "qalesan", "qalaysan", "qalaysiz", "qandaysiz", "nima", "gap", "rahmat",
    "raxmat", "xop", "xo'p", "mayli", "yaxshi", "yomon", "qilyapsan", "ishlar",
    "qalay", "tushundim", "bilmayman", "bormi", "kerak", "nega", "chunki",
    "lekin", "ammo", "men", "sen", "biz", "siz", "ular", "bugun", "havo",
    "kecha", "ertaga", "yaxshimisiz", "charchamang", "bopman", "boldi", "bo'ldi",
    "qayerda", "qayerdasan", "qachon", "kim", "uchun", "bilan", "juda", "ham",
    "emas", "ekan", "edi", "boladi", "bo'ladi", "qilaman", "qilamiz", "kel",
    "ketdik", "uxla", "ovqat", "maktab", "ish", "pul", "vaqt", "odam", "dost",
    "do'st", "jigar", "aka", "uka", "opa", "singil", "ona", "ota",
}

UZBEK_QUICK_DICT = {
    "ha": "yes",
    "haa": "yes",
    "haaa": "yes",
    "xaa": "yes",
    "yoq": "no",
    "yo'q": "no",
    "salom": "hello",
    "assalomu alaykum": "hello (peace be upon you)",
    "qalesan": "how are you",
    "qalaysan": "how are you",
    "qalaysiz": "how are you",
    "qales": "how are you",
    "qvosan": "what are you doing?",
    "qmursan": "what are you doing?",
    "nima gap": "what's up",
    "tinchlikmi": "is everything okay",
    "rahmat": "thank you",
    "raxmat": "thank you",
    "rahmt": "thank you",
    "rxmt": "thank you",
    "xop": "okay",
    "xo'p": "okay",
    "mayli": "alright",
    "bo'ldi": "done / okay",
    "boldi": "done / okay",
    "bopti": "okay / agreed",
    "bopt": "okay / agreed",
    "chundim": "I understand",
    "tushundm": "I understand",
    "bilmiman": "I don't know",
    "blmiman": "I don't know",
    "qatta": "where?",
    "qattasiz": "where are you?",
    "yoge": "really? / no way!",
    "yo'g'e": "really? / no way!",
}

# Internet slang & abbreviations dictionary
SLANG_EXACT_MAP: Dict[str, Tuple[str, str, str, str, str]] = {
    "idk": ("bilmayman (I don't know)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "iykyk": ("tushungan tushunadi / bilsangiz bilasiz (If you know, you know)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "tbh": ("ochig'ini aytganda / rostini aytsam (To be honest)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ngl": ("rostini aytsam / yashirmayman (Not gonna lie)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "imo": ("mening fikrimcha (In my opinion)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "imho": ("mening kamtarona fikrimcha (In my humble opinion)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ofc": ("albatta (Of course)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "idc": ("menga baribir / qiziq emas (I don't care)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "idgaf": ("menga mutlaqo baribir / farqi yo'q (I don't care at all)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "nvm": ("hechqisi yo'q / e'tibor berma (Never mind)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "btw": ("aytgandek / darvoqe (By the way)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "omg": ("voy xudoyim! / yo rab (Oh my god!)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "lol": ("juda kulgili / xaxaxa (Laughing out loud)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "lmao": ("kulib qotdim (Laughing my ass off)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "asap": ("imkon qadar tezroq (As soon as possible)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "pls": ("iltimos (Please)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "plz": ("iltimos (Please)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "thx": ("rahmat (Thanks)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ty": ("rahmat (Thank you)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "np": ("arzimaydi / muammo yo'q (No problem)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "brb": ("hozir qaytaman (Be right back)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "wyd": ("nima qilyapsan? (What are you doing?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "hru": ("qalaysan? / qandaysiz? (How are you?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "wbu": ("o'zingdachi? / sizdachi? (What about you?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "hbu": ("o'zingchi? (How about you?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ikr": ("shuni ayting / haq gap (I know, right)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "gtg": ("ketishim kerak (Got to go)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "g2g": ("ketishim kerak (Got to go)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "cya": ("ko'rishguncha (See you)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ily": ("seni yaxshi ko'raman (I love you)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "pov": ("nuqtai nazar / hayotiy holat (Point of view)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "fr": ("rostdan ham / jiddiy (For real)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "frfr": ("haqiqatdan ham (For real, for real)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "rn": ("aynan hozir (Right now)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "tldr": ("qisqasi / xulosa (Too long; didn't read)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "yw": ("arzimaydi (You're welcome)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "gg": ("yaxshi o'yin (Good game)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "fyi": ("ma'lumot uchun (For your information)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "smh": ("bosh chayqash / hafsala pir bo'lish (Shaking my head)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "wth": ("bu nima degani? / bu nima jinnilik? (What the heck?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "wtf": ("bu nima degani? (What the f***?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "wtd": ("nima qilish kerak? (What to do?)", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
}

# Conversational idioms & phrases (natural living speech instead of robotic translation)
CONVERSATIONAL_IDIOMS: Dict[str, Tuple[str, str, str, str, str]] = {
    "i feel you": ("seni tushunaman / dardimiz bir", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "chill out": ("o'zingni bos / damingni ol", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "chill": ("o'zingni bos / damingni ol", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "i'm broke": ("cho'ntak bo'sh / pulim qolmagan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "i am broke": ("cho'ntak bo'sh / pulim qolmagan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "my bad": ("ayb menda / kechirasan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "piece of cake": ("juda oson / xamirdek oson ish", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "hit me up": ("menga yozvor / aloqaga chiq", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "no way": ("yo'g'e! / bo'lishi mumkin emas", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "are you kidding me": ("hazillashyapsanmi? / jiddiy aytayapsanmi?", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "never mind": ("hechqisi yo'q / e'tibor berma / qo'yaver", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "cut it out": ("bas qil / to'xtat", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "take it easy": ("o'zingni bos / xavotir olma", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "sleep tight": ("shirin tushlar / yaxshi dam ol", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "spill the tea": ("gapir, nima gap? / sirlarni och", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "you rock": ("qoyilsan / zo'rsan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "shut up": ("jim bo'l / ovozingni o'chir", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "same here": ("menda ham xuddi shunday", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "what's good": ("nima gaplar? / tinchmi?", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "count me in": ("men ham borman / meni ham qo'sh", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "count me out": ("men yo'qman / mensiz qilaveringlar", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "no big deal": ("arzimaydi / katta gap emas", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "catch you later": ("keyinroq gaplashamiz / ko'rishguncha", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "under the weather": ("mazam yo'q / biroz betob", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "it is what it is": ("bo'lar ish bo'ldi / taqdir ekan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "hands down": ("shubhasiz / bir ovozdan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "keep in touch": ("aloqada bo'laylik", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "by all means": ("bajonidil / albatta", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "give me a break": ("qo'ysang-chi / jonimga tegding", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "call it a day": ("bugunga yetadi / tugatsak ham bo'ladi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "hang in there": ("sabr qil / chida", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "so far so good": ("hozircha hammasi yaxshi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "finger crossed": ("omad tilayman / umid qilamiz", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "cross my heart": ("qasamyod qilaman / rostdan", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "break a leg": ("omad yor bo'lsin!", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "go for it": ("dadil bo'l / harakat qil!", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "long time no see": ("ancha bo'ldi ko'rishmaganimizga / bormisiz", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "take care": ("o'zingni ehtiyot qil", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "don't worry": ("xavotir olma", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "no worries": ("xavotirga o'rin yo'q / arzimaydi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "for real": ("rostdan ham / jiddiy", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "ghosted me": ("javob bermay aloqani uzdi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "he ghosted me": ("u menga javob bermay yo'q bo'lib ketdi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
    "she ghosted me": ("u qiz menga javob bermay yo'q bo'lib ketdi", "en", "Inglizcha", "🇬🇧", "O'zbekcha"),
}

ROBOTIC_FIXES = [
    (r"\bmen buzildim\b", "cho'ntak bo'sh / pulim qolmagan"),
    (r"\bmening yomonim\b", "ayb menda / kechirasan"),
    (r"\bsizni sezyapman\b", "seni tushunaman / dardimiz bir"),
    (r"\bmen sizni his qilyapman\b", "seni juda yaxshi tushunaman"),
    (r"\bpirojnoe bo'lagi\b", "juda oson ish"),
    (r"\bmeni uring\b", "menga yozvor / aloqaga chiq"),
    (r"\bmeni ur\b", "menga yozvor / aloqaga chiq"),
    (r"\boromlanish, tinchlanish\b", "o'zingni bos / damingni ol"),
    (r"\bhech qanday yo'l\b", "bo'lishi mumkin emas / yo'g'e"),
    (r"\bhech qachon aql\b", "hechqisi yo'q / qo'yaver"),
    (r"\bmen yaxshiman\b", "yaxshi, rahmat / hammasi joyida"),
    (r"\bo'likman\b", "kulib o'laman / yorildim"),
    (r"\boson oling\b", "o'zingni bos / xavotir olma"),
    (r"\bqattiq uxlang\b", "shirin tushlar / yaxshi dam ol"),
]

SLANG_EXPAND = {
    r"\bidk\b": "I don't know",
    r"\biykyk\b": "if you know you know",
    r"\btbh\b": "to be honest",
    r"\bngl\b": "not going to lie",
    r"\bimo\b": "in my opinion",
    r"\bimho\b": "in my humble opinion",
    r"\bofc\b": "of course",
    r"\bidc\b": "I don't care",
    r"\bidgaf\b": "I don't care at all",
    r"\bnvm\b": "never mind",
    r"\bbtw\b": "by the way",
    r"\bomg\b": "oh my god",
    r"\basap\b": "as soon as possible",
    r"\bpls\b": "please",
    r"\bplz\b": "please",
    r"\bthx\b": "thanks",
    r"\bty\b": "thank you",
    r"\bnp\b": "no problem",
    r"\bbrb\b": "be right back",
    r"\bwyd\b": "what are you doing",
    r"\bhru\b": "how are you",
    r"\bwbu\b": "what about you",
    r"\bhbu\b": "how about you",
    r"\bikr\b": "I know right",
    r"\bgtg\b": "got to go",
    r"\bg2g\b": "got to go",
    r"\bcya\b": "see you",
    r"\bily\b": "I love you",
    r"\bfr\b": "for real",
    r"\bfrfr\b": "for real",
    r"\brn\b": "right now",
    r"\byw\b": "you're welcome",
    r"\bfyi\b": "for your information",
    r"\bwth\b": "what the heck",
    r"\bwtf\b": "what the fuck",
    r"\bwtd\b": "what to do",
    r"\bu\b": "you",
    r"\br\b": "are",
    r"\bur\b": "your",
}


def is_likely_uzbek(text: str) -> bool:
    t = text.lower().strip()
    if t in UZBEK_QUICK_DICT:
        return True
    words = set(re.findall(r"[a-zA-Z'ʻʼ]+", t))
    if words & UZBEK_WORDS:
        return True
    if any(w in t for w in ["o'", "g'", "oʻ", "gʻ", "oʼ", "gʼ", "sh", "ch", "ng"]):
        # Check common Uzbek suffixes
        if re.search(r"(yapman|yapsan|yapti|miz|sizlar|dilar|ganman|moqchi|lik|ning|ga|da|dan)\b", t):
            return True
        if any(w in t for w in ["o'", "g'", "oʻ", "gʻ", "oʼ", "gʼ"]):
            return True
    return False


def _guess_source_lang_for_mymemory(text: str, sl: str) -> str:
    if sl and sl != "auto":
        return "uz-UZ" if sl == "uz" else sl
    # Script detection for MyMemory since MyMemory fails on 'autodetect'
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru-RU"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-IN"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar-SA"
    if re.search(r"[\uAC00-\uD7AF]", text):
        return "ko-KR"
    if re.search(r"[\u3040-\u30FF]", text):
        return "ja-JP"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "zh-CN"
    if re.search(r"[äöüßÄÖÜ]", text):
        return "de-DE"
    if re.search(r"[ğşıöçĞŞİÖÇ]", text):
        return "tr-TR"
    return "en-GB"


async def _layer_clients5(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Optional[Tuple[str, str]]:
    url = (
        f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
        f"&sl={sl}&tl={tl}&q={urllib.parse.quote(text)}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status == 200:
            data = await resp.json(content_type=None)
            if isinstance(data, list) and data:
                if isinstance(data[0], list):
                    tr = str(data[0][0]).strip()
                    src = str(data[0][1]) if len(data[0]) > 1 else sl
                    if tr:
                        return tr, src
                elif isinstance(data[0], str):
                    tr = data[0].strip()
                    if tr:
                        return tr, sl
    return None


async def _layer_googleapis(session: aiohttp.ClientSession, text: str, sl: str, tl: str, client_id: str) -> Optional[Tuple[str, str]]:
    params = {
        "client": client_id,
        "sl": sl,
        "tl": tl,
        "hl": "en",
        "dt": "t",
        "dj": "1",
        "q": text,
    }
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status == 200:
            data = await resp.json(content_type=None)
            sentences = data.get("sentences", [])
            tr = "".join([s.get("trans", "") for s in sentences if s.get("trans")]).strip()
            src = data.get("src", sl)
            if tr:
                return tr, src
    return None


async def _layer_batchexecute(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Optional[Tuple[str, str]]:
    url = "https://translate.google.com/_/TranslateWebserverUi/data/batchexecute"
    inner = json.dumps([[text, sl, tl, True], [None]])
    freq = json.dumps([[["MkEWBc", inner, None, "generic"]]])
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    body = "f.req=" + urllib.parse.quote(freq)
    async with session.post(url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as resp:
        if resp.status == 200:
            raw = await resp.text()
            for line in raw.splitlines():
                if "MkEWBc" in line:
                    outer = json.loads(line)
                    payload = json.loads(outer[0][2])
                    src_detected = payload[2] if len(payload) > 2 and payload[2] else sl
                    sents = payload[1][0][0][5]
                    tr = " ".join([s[0] for s in sents if s and s[0]]).strip()
                    if tr:
                        return tr, src_detected
    return None


async def _layer_mymemory(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Optional[Tuple[str, str]]:
    target_tag = "uz-UZ" if tl == "uz" else ("en-GB" if tl == "en" else tl)
    source_tag = _guess_source_lang_for_mymemory(text, sl)
    if source_tag == target_tag:
        source_tag = "en-GB" if target_tag == "uz-UZ" else "uz-UZ"
    url = (
        f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}"
        f"&langpair={source_tag}|{target_tag}&de=tokhirov077@gmail.com"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status == 200:
            data = await resp.json(content_type=None)
            status_code = str(data.get("responseStatus", ""))
            if status_code == "200":
                tr = data.get("responseData", {}).get("translatedText", "").strip()
                det = data.get("responseData", {}).get("detectedLanguage") or source_tag.split("-")[0]
                if tr and "MYMEMORY WARNING" not in tr and "PLEASE SELECT" not in tr:
                    return html.unescape(tr), det.split("-")[0]
    return None


async def _translate_multi_layer(session: aiohttp.ClientSession, text: str, sl: str, tl: str) -> Tuple[str, str]:
    layers = [
        ("clients5", lambda: _layer_clients5(session, text, sl, tl)),
        ("googleapis-at", lambda: _layer_googleapis(session, text, sl, tl, "at")),
        ("googleapis-chrome", lambda: _layer_googleapis(session, text, sl, tl, "dict-chrome-ex")),
        ("googleapis-it", lambda: _layer_googleapis(session, text, sl, tl, "it")),
        ("batchexecute", lambda: _layer_batchexecute(session, text, sl, tl)),
        ("mymemory", lambda: _layer_mymemory(session, text, sl, tl)),
    ]

    first_valid: Optional[Tuple[str, str]] = None
    for name, fn in layers:
        try:
            res = await fn()
            if res and res[0]:
                tr, det_src = res
                if first_valid is None:
                    first_valid = (tr, det_src)
                # If translation actually changed the text (or detected uz when translating to uz), return immediately!
                if tr.lower().strip() != text.lower().strip() or (tl == "uz" and det_src == "uz"):
                    return tr, det_src
        except Exception as e:
            logger.warning(f"Layer {name} failed: {e}")

    if first_valid is not None:
        return first_valid
    raise RuntimeError("All translation layers failed")


def _create_connector() -> aiohttp.TCPConnector:
    try:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.TCPConnector(family=socket.AF_INET, ssl=ssl_ctx)
    except Exception:
        return aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)


async def _call_gemini_ai(session: aiohttp.ClientSession, text: str, api_key: str) -> Optional[Tuple[str, str, str]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    prompt = (
        "Siz o'zbek va ingliz tillarida eng tajribali, tabiiy, zamonaviy so'zlashuvchi professional tarjimonsiz.\n"
        "Qoidalar:\n"
        "1. Agar matn o'zbek tilida bo'lsa, uni ingliz tiliga tabiiy jonli so'zlashuv uslubida tarjima qiling.\n"
        "2. Agar matn chet tilida bo'lsa, uni o'zbek tiliga o'giring. Aslo quruq, kitobiy yoki so'zma-so'z tarjima qilmang! Odamlar ko'chada, do'stlar orasida qanday tabiiy gaplashsa, xuddi shunday tirik tilda tarjima qiling. Sleng va idiomalarni o'zbekcha jonli ma'nosiga moslang.\n"
        "3. FAQAT tarjimani qaytaring, hech qanday qo'shimcha so'z, izoh yoki tushuntirish yozmang.\n\n"
        f"Matn:\n{text}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 800},
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        tr = parts[0]["text"].strip()
                        if tr:
                            is_uz = is_likely_uzbek(text)
                            src_l = "uz" if is_uz else "en"
                            dst_l = "en" if is_uz else "uz"
                            return tr, src_l, dst_l
    except Exception as e:
        logger.warning(f"Gemini AI error: {e}")
    return None


async def _call_groq_ai(session: aiohttp.ClientSession, text: str, api_key: str) -> Optional[Tuple[str, str, str]]:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    is_uz = is_likely_uzbek(text)
    system_prompt = (
        "Siz o'zbek va ingliz tillarida eng tajribali, tabiiy, zamonaviy so'zlashuvchi professional tarjimonsiz. "
        "Agar matn o'zbek tilida bo'lsa, ingliz tiliga tabiiy so'zlashuv uslubida tarjima qiling. "
        "Agar matn chet tilida bo'lsa, o'zbek tiliga o'giring. Aslo quruq, kitobiy yoki so'zma-so'z tarjima qilmang! "
        "Odamlar ko'chada va hayotda qanday gaplashsa, xuddi shunday jonli, ravon o'zbek tiliga o'giring. "
        "FAQAT tarjimani qaytaring, hech qanday ortiqcha izoh yoki tushuntirish yozmang."
    )
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    try:
        async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                choices = data.get("choices", [])
                if choices:
                    tr = choices[0].get("message", {}).get("content", "").strip()
                    if tr:
                        src_l = "uz" if is_uz else "en"
                        dst_l = "en" if is_uz else "uz"
                        return tr, src_l, dst_l
    except Exception as e:
        logger.warning(f"Groq AI error: {e}")
    return None


async def smart_translate(text: str, gemini_api_key: str = "") -> Dict[str, str]:
    clean_text = text.strip()
    if not clean_text:
        return {"original": "", "translated": "", "src_lang": "", "src_name": "", "flag": "", "dst_lang": "uz"}

    # Normalize 3+ stretched vowels/consonants (e.g. "heeello" -> "hello", "saaalom" -> "salom")
    normalized_text = re.sub(r"([aeiouAEIOU])\1{2,}", r"\1", clean_text)
    normalized_text = re.sub(r"([a-zA-Z])\1{2,}", r"\1\1", normalized_text)

    lower_clean = normalized_text.lower().strip("?!.,;:")

    # 1. Exact match for common conversational idioms & phrases
    if lower_clean in CONVERSATIONAL_IDIOMS:
        tr, sl, sn, sf, dn = CONVERSATIONAL_IDIOMS[lower_clean]
        return {
            "original": clean_text,
            "translated": tr,
            "src_lang": sl,
            "src_name": sn,
            "flag": sf,
            "dst_lang": "uz",
            "dst_name": dn,
            "dst_flag": "🇺🇿",
        }

    # 2. Exact match for common Internet slang & acronyms (e.g. idk, iykyk, tbh, ngl)
    if lower_clean in SLANG_EXACT_MAP:
        tr, sl, sn, sf, dn = SLANG_EXACT_MAP[lower_clean]
        return {
            "original": clean_text,
            "translated": tr,
            "src_lang": sl,
            "src_name": sn,
            "flag": sf,
            "dst_lang": "uz",
            "dst_name": dn,
            "dst_flag": "🇺🇿",
        }

    # 3. Quick exact dictionary for common short Uzbek conversational words
    if lower_clean in UZBEK_QUICK_DICT:
        return {
            "original": clean_text,
            "translated": UZBEK_QUICK_DICT[lower_clean],
            "src_lang": "uz",
            "src_name": "O'zbekcha",
            "flag": "🇺🇿",
            "dst_lang": "en",
            "dst_name": "Inglizcha",
            "dst_flag": "🇬🇧",
        }

    connector = _create_connector()
    async with aiohttp.ClientSession(connector=connector) as session:
        # 4. If AI API key (Gemini AIza... or Groq gsk_...) is available, use AI for superior conversational translation!
        if gemini_api_key:
            if gemini_api_key.startswith("gsk_"):
                ai_res = await _call_groq_ai(session, clean_text, gemini_api_key)
            else:
                ai_res = await _call_gemini_ai(session, clean_text, gemini_api_key)
            if ai_res:
                ai_tr, ai_src, ai_dst = ai_res
                src_n, src_f = LANG_NAMES.get(ai_src, ("Inglizcha", "🇬🇧"))
                dst_n, dst_f = LANG_NAMES.get(ai_dst, ("O'zbekcha", "🇺🇿"))
                return {
                    "original": clean_text,
                    "translated": ai_tr,
                    "src_lang": ai_src,
                    "src_name": src_n,
                    "flag": src_f,
                    "dst_lang": ai_dst,
                    "dst_name": dst_n,
                    "dst_flag": dst_f,
                }

        # Expand acronyms inside sentences (e.g. "idk what you mean tbh" -> "I don't know what you mean to be honest")
        expanded_text = normalized_text
        for pat, repl in SLANG_EXPAND.items():
            expanded_text = re.sub(pat, repl, expanded_text, flags=re.IGNORECASE)

        # 5. If heuristics detect Uzbek, translate uz -> en directly
        if is_likely_uzbek(expanded_text):
            tr_text, _ = await _translate_multi_layer(session, expanded_text, "uz", "en")
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

        # 6. Otherwise translate auto -> uz
        tr_text, detected_src = await _translate_multi_layer(session, expanded_text, "auto", "uz")

        # 7. If Google detected the input as Uzbek ('uz'), OR if the Uzbek translation is identical
        # to the input, try translating from Uzbek to English ('uz' -> 'en')!
        if detected_src == "uz" or tr_text.lower().strip() == lower_clean:
            en_tr, _ = await _translate_multi_layer(session, expanded_text, "uz", "en")
            if detected_src == "uz" or en_tr.lower().strip() != lower_clean:
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

        # Fix robotic machine translations into natural Uzbek
        for pat, repl in ROBOTIC_FIXES:
            tr_text = re.sub(pat, repl, tr_text, flags=re.IGNORECASE)

        src_name, src_flag = LANG_NAMES.get(detected_src, (detected_src.upper(), "🌐"))
        return {
            "original": clean_text,
            "translated": tr_text,
            "src_lang": detected_src,
            "src_name": src_name,
            "flag": src_flag,
            "dst_lang": "uz",
            "dst_name": "O'zbekcha",
            "dst_flag": "🇺🇿",
        }
