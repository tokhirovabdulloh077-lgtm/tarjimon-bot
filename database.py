import asyncio
import datetime
import os
import sqlite3
from typing import Dict, List, Optional

DB_FILE = os.getenv("DB_PATH", "users.db")


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
        conn.commit()


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


def _set_setting_sync(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


async def set_setting(key: str, value: str) -> None:
    await asyncio.to_thread(_set_setting_sync, key, value)


def _get_setting_sync(key: str, default: str = "") -> str:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


async def get_setting(key: str, default: str = "") -> str:
    return await asyncio.to_thread(_get_setting_sync, key, default)


def _upsert_user_sync(user_id: int, first_name: str, last_name: str, username: str, increment_count: bool = False) -> None:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, translations_count FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            add_count = 1 if increment_count else 0
            cur.execute("""
                UPDATE users
                SET first_name = ?, last_name = ?, username = ?,
                    last_active = ?, translations_count = translations_count + ?
                WHERE user_id = ?
            """, (first_name, last_name, username, now_str, add_count, user_id))
        else:
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            # First user to ever interact can be claimed as initial admin
            is_admin_flag = 1 if total == 0 else 0
            initial_count = 1 if increment_count else 0
            cur.execute("""
                INSERT INTO users (user_id, first_name, last_name, username, translations_count, created_at, last_active, is_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, first_name, last_name, username, initial_count, now_str, now_str, is_admin_flag))
        conn.commit()


async def upsert_user(user_id: int, first_name: str, last_name: str, username: str, increment_count: bool = False) -> None:
    await asyncio.to_thread(_upsert_user_sync, user_id, first_name, last_name, username, increment_count)


def _check_admin_sync(user_id: int) -> bool:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row["is_admin"] == 1:
            return True
        # If no admin registered at all, make this user the admin
        cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        admins_count = cur.fetchone()[0]
        if admins_count == 0:
            cur.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
        return False


async def is_admin(user_id: int) -> bool:
    return await asyncio.to_thread(_check_admin_sync, user_id)


def _get_stats_sync() -> Dict[str, int]:
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(translations_count), 0) FROM users")
        row = cur.fetchone()
        total_users = row[0] or 0
        total_translations = row[1] or 0

        cur.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today_str}%",))
        today_active = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (f"{today_str}%",))
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
        cur.execute("""
            SELECT user_id, first_name, last_name, username, translations_count, created_at, last_active, is_admin
            FROM users
            ORDER BY last_active DESC
            LIMIT ?
        """, (limit,))
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
