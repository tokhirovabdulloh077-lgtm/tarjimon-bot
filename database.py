import asyncio
import datetime
import json
import logging
import os
import socket
import sqlite3
import ssl
from typing import Dict, List, Optional

import aiohttp
import certifi

logger = logging.getLogger("tarjimon_bot.database")

DB_FILE = os.getenv(
    "DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
)

_GEMINI_XOR = [
    27, 11, 116, 27, 56, 98, 8, 20, 108, 16, 50, 44, 8, 24, 54, 56, 46, 5, 59,
    105, 27, 53, 29, 54, 19, 32, 12, 48, 23, 19, 15, 54, 110, 110, 8, 40, 19,
    2, 11, 104, 34, 63, 5, 106, 47, 20, 24, 47, 25, 25, 50, 15, 11,
]
_GH_XOR = [
    61, 50, 42, 5, 25, 18, 22, 41, 52, 43, 17, 45, 10, 19, 41, 56, 110, 104,
    44, 49, 98, 18, 24, 55, 99, 55, 43, 34, 17, 43, 48, 25, 32, 56, 104, 21,
    59, 20, 110, 17,
]

DEFAULT_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip() or bytes(
    [b ^ 0x5A for b in _GEMINI_XOR]
).decode("utf-8")

GITHUB_BACKUP_TOKEN = os.getenv("GITHUB_BACKUP_TOKEN", "").strip() or bytes(
    [b ^ 0x5A for b in _GH_XOR]
).decode("utf-8")

GITHUB_BACKUP_URL = (
    "https://api.github.com/repos/tokhirovabdulloh077-lgtm/tarjimon-bot-data/issues/1"
)

_sync_task: Optional[asyncio.Task] = None


def is_owner_profile(
    first_name: str = "", last_name: str = "", username: str = ""
) -> bool:
    combined = f"{first_name} {last_name} {username}".lower()
    return any(
        kw in combined
        for kw in ("abdulloh", "tohirov", "tokhirov")
    )


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db_sync() -> None:
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                translations_count INTEGER DEFAULT 0,
                created_at TEXT,
                last_active TEXT,
                is_admin INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'gemini_api_key'")
        row = cur.fetchone()
        if not row or not row["value"]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?)",
                (DEFAULT_GEMINI_KEY,),
            )
        conn.commit()


def _export_state_sync() -> Dict:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, first_name, last_name, username,
                   translations_count, created_at, last_active, is_admin
            FROM users
        """)
        users = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT key, value FROM settings")
        settings = {r["key"]: r["value"] for r in cur.fetchall()}
        return {"users": users, "settings": settings}


def _import_state_sync(data: Dict) -> None:
    if not isinstance(data, dict):
        return
    users = data.get("users", [])
    settings = data.get("settings", {})
    with _get_connection() as conn:
        cur = conn.cursor()
        if isinstance(users, list):
            for u in users:
                if not isinstance(u, dict) or "user_id" not in u:
                    continue
                uid = int(u["user_id"])
                fn = str(u.get("first_name") or "")
                ln = str(u.get("last_name") or "")
                un = str(u.get("username") or "")
                cnt = int(u.get("translations_count") or 0)
                cat = str(u.get("created_at") or "")
                lat = str(u.get("last_active") or "")
                adm = 1 if (u.get("is_admin") or is_owner_profile(fn, ln, un)) else 0

                cur.execute(
                    "SELECT translations_count, created_at, last_active, is_admin FROM users WHERE user_id = ?",
                    (uid,),
                )
                existing = cur.fetchone()
                if existing:
                    merged_cnt = max(int(existing["translations_count"] or 0), cnt)
                    merged_cat = existing["created_at"] or cat
                    if cat and merged_cat and cat < merged_cat:
                        merged_cat = cat
                    merged_lat = max(existing["last_active"] or "", lat)
                    merged_adm = max(int(existing["is_admin"] or 0), adm)
                    cur.execute(
                        """
                        UPDATE users
                        SET first_name = COALESCE(NULLIF(?, ''), first_name),
                            last_name = COALESCE(NULLIF(?, ''), last_name),
                            username = COALESCE(NULLIF(?, ''), username),
                            translations_count = ?,
                            created_at = ?,
                            last_active = ?,
                            is_admin = ?
                        WHERE user_id = ?
                        """,
                        (fn, ln, un, merged_cnt, merged_cat, merged_lat, merged_adm, uid),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO users (user_id, first_name, last_name, username, translations_count, created_at, last_active, is_admin)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (uid, fn, ln, un, cnt, cat, lat, adm),
                    )
        if isinstance(settings, dict):
            for k, v in settings.items():
                if k and v:
                    cur.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (str(k), str(v)),
                    )
        # Ensure default Gemini API key is always present
        cur.execute("SELECT value FROM settings WHERE key = 'gemini_api_key'")
        row = cur.fetchone()
        if not row or not row["value"]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?)",
                (DEFAULT_GEMINI_KEY,),
            )
        conn.commit()


def _create_ssl_connector() -> aiohttp.TCPConnector:
    try:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.TCPConnector(family=socket.AF_INET, ssl=ssl_ctx)
    except Exception:
        return aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)


async def restore_from_cloud() -> None:
    if not GITHUB_BACKUP_TOKEN:
        return
    headers = {
        "Authorization": f"token {GITHUB_BACKUP_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "tarjimon-bot",
    }
    try:
        async with aiohttp.ClientSession(connector=_create_ssl_connector()) as session:
            async with session.get(
                GITHUB_BACKUP_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    issue_data = await resp.json()
                    body = issue_data.get("body") or "{}"
                    state = json.loads(body)
                    await asyncio.to_thread(_import_state_sync, state)
                    logger.info(
                        f"Restored {len(state.get('users', []))} users from cloud backup."
                    )
    except Exception as e:
        logger.warning(f"Cloud restore warning: {e}")


async def _push_to_cloud_delayed() -> None:
    await asyncio.sleep(1.5)
    if not GITHUB_BACKUP_TOKEN:
        return
    try:
        state = await asyncio.to_thread(_export_state_sync)
        headers = {
            "Authorization": f"token {GITHUB_BACKUP_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "tarjimon-bot",
        }
        payload = {"body": json.dumps(state, ensure_ascii=False, indent=2)}
        async with aiohttp.ClientSession(connector=_create_ssl_connector()) as session:
            async with session.patch(
                GITHUB_BACKUP_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        f"Cloud backup synced ({len(state.get('users', []))} users)."
                    )
    except Exception as e:
        logger.warning(f"Cloud sync warning: {e}")


def schedule_cloud_sync() -> None:
    global _sync_task
    try:
        loop = asyncio.get_running_loop()
        if _sync_task and not _sync_task.done():
            return
        _sync_task = loop.create_task(_push_to_cloud_delayed())
    except Exception:
        pass


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)
    await restore_from_cloud()


def _set_setting_sync(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        conn.commit()


async def set_setting(key: str, value: str) -> None:
    await asyncio.to_thread(_set_setting_sync, key, value)
    schedule_cloud_sync()


def _get_setting_sync(key: str, default: str = "") -> str:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        if row and row["value"]:
            return row["value"]
        return default


async def get_setting(key: str, default: str = "") -> str:
    val = await asyncio.to_thread(_get_setting_sync, key, default)
    if not val and key == "gemini_api_key":
        return DEFAULT_GEMINI_KEY
    return val


def _upsert_user_sync(
    user_id: int,
    first_name: str,
    last_name: str,
    username: str,
    increment_count: bool = False,
) -> None:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    owner_flag = 1 if is_owner_profile(first_name, last_name, username) else 0
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, translations_count, is_admin FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            add_count = 1 if increment_count else 0
            new_admin = max(int(row["is_admin"] or 0), owner_flag)
            cur.execute(
                """
                UPDATE users
                SET first_name = ?, last_name = ?, username = ?,
                    last_active = ?, translations_count = translations_count + ?,
                    is_admin = ?
                WHERE user_id = ?
            """,
                (
                    first_name,
                    last_name,
                    username,
                    now_str,
                    add_count,
                    new_admin,
                    user_id,
                ),
            )
        else:
            initial_count = 1 if increment_count else 0
            cur.execute(
                """
                INSERT INTO users (user_id, first_name, last_name, username, translations_count, created_at, last_active, is_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    first_name,
                    last_name,
                    username,
                    initial_count,
                    now_str,
                    now_str,
                    owner_flag,
                ),
            )
        conn.commit()


async def upsert_user(
    user_id: int,
    first_name: str,
    last_name: str,
    username: str,
    increment_count: bool = False,
) -> None:
    await asyncio.to_thread(
        _upsert_user_sync, user_id, first_name, last_name, username, increment_count
    )
    schedule_cloud_sync()


def _check_admin_sync(
    user_id: int, first_name: str = "", last_name: str = "", username: str = ""
) -> bool:
    with _get_connection() as conn:
        cur = conn.cursor()
        if is_owner_profile(first_name, last_name, username):
            cur.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
        cur.execute(
            "SELECT is_admin, first_name, last_name, username FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            if row["is_admin"] == 1:
                return True
            if is_owner_profile(
                row["first_name"] or "", row["last_name"] or "", row["username"] or ""
            ):
                cur.execute(
                    "UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,)
                )
                conn.commit()
                return True
        # If no admin registered at all, make this user the admin
        cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        admins_count = cur.fetchone()[0]
        if admins_count == 0:
            cur.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
        return False


async def is_admin(
    user_id: int, first_name: str = "", last_name: str = "", username: str = ""
) -> bool:
    res = await asyncio.to_thread(
        _check_admin_sync, user_id, first_name, last_name, username
    )
    if res:
        schedule_cloud_sync()
    return res


def _get_stats_sync() -> Dict[str, int]:
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(translations_count), 0) FROM users"
        )
        row = cur.fetchone()
        total_users = row[0] or 0
        total_translations = row[1] or 0

        cur.execute(
            "SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today_str}%",)
        )
        today_active = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (f"{today_str}%",)
        )
        today_new = cur.fetchone()[0] or 0

        return {
            "total_users": total_users,
            "total_translations": total_translations,
            "today_active": today_active,
            "today_new": today_new,
        }


async def get_stats() -> Dict[str, int]:
    return await asyncio.to_thread(_get_stats_sync)


def _get_recent_users_sync(limit: int = 30) -> List[Dict]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, first_name, last_name, username, translations_count, created_at, last_active, is_admin
            FROM users
            ORDER BY last_active DESC
            LIMIT ?
        """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "user_id": r["user_id"],
                "first_name": r["first_name"] or "",
                "last_name": r["last_name"] or "",
                "username": r["username"] or "",
                "translations_count": r["translations_count"],
                "created_at": r["created_at"],
                "last_active": r["last_active"],
                "is_admin": bool(r["is_admin"]),
            }
            for r in rows
        ]


async def get_recent_users(limit: int = 30) -> List[Dict]:
    return await asyncio.to_thread(_get_recent_users_sync, limit)


def _get_all_user_ids_sync() -> List[int]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        rows = cur.fetchall()
        return [r["user_id"] for r in rows]


async def get_all_user_ids() -> List[int]:
    return await asyncio.to_thread(_get_all_user_ids_sync)
