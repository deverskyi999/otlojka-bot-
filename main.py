from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Dict, List, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SetBusinessAccountName
from aiogram.types import (
    BotCommand,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    User,
    MessageEntity,
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")

# ---------------------------------------------------------------------------
# Константы подписки
# ---------------------------------------------------------------------------

SUBSCRIPTION_STARS_DEFAULT = 15
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
SUBSCRIPTION_PAYLOAD = "timenick_subscription"

BUTTON_STYLES = ("primary", "danger", "success")

BUTTON_KEYS = {
    "toggle_on": "🔥 Включить",
    "toggle_off": "⛔ Выключить",
    "connect": "🔗 Подключить",
    "copy": "📋 Скопировать",
    "pay": "⭐ Оплатить подписку",
    "feedback": "✉️ Поддержка",
}

TRIAL_PERIOD_SECONDS = 3 * 24 * 60 * 60
EXPIRY_WARNING_SECONDS = 2 * 24 * 60 * 60
MESSAGE_CACHE_DAYS = 30

WELCOME_TEXT_DEFAULT = (
    "Привет! Я SiaTimeBot. Показываю время прямо в твоём имени в Telegram, "
    "чтобы собеседники видели его в чате без сторонних приложений. "
    "И это не всё: внутри ещё несколько классных фишек, загляни в настройки и попробуй все."
)

TEXT_KEYS = {
    "welcome_text": WELCOME_TEXT_DEFAULT,
    "not_connected_text": (
        "<b>Бот не подключён.</b>\n\n"
        "Нажмите <b>Подключить</b>, затем <b>Скопировать</b>, "
        "далее откройте <b>Автоматизация чатов</b>, вставьте скопированный текст "
        "и нажмите <b>Добавить</b>. Разрешите <b>Управлять профилем</b>."
    ),
    "subscription_required_text": (
        "<b>Требуется подписка.</b>\n\n"
        "Доступ к боту стоит {price} ⭐ в месяц."
    ),
    "connected_text": "<b>Бот подключён.</b>",
    "deleted_message_text": (
        "🗑 <b>Новое удалённое сообщение</b>\n\n"
        "👤 <b>Автор:</b> {author}\n"
        "📝 <b>Текст:</b>\n"
        "<blockquote>{text}</blockquote>"
    ),
    "deleted_messages_text": (
        "🗑 <b>Удалено {count} сообщений</b>\n\n"
        "👤 <b>Автор:</b> {author}\n"
        "{messages}"
    ),
}

TEXT_LABELS = {
    "welcome_text": "Приветствие (/start)",
    "not_connected_text": "Текст «бот не подключён»",
    "subscription_required_text": "Текст «нужна подписка»",
    "connected_text": "Текст «бот подключён»",
    "deleted_message_text": "Шаблон удалённого сообщения",
    "deleted_messages_text": "Шаблон нескольких удалённых сообщений",
}


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set in environment (.env)")

        owner_raw = os.getenv("OWNER_ID")
        if not owner_raw:
            raise RuntimeError(
                "OWNER_ID is not set in .env. Укажите свой Telegram user_id, "
                "чтобы получить доступ к админ-панели"
            )

        return cls(
            bot_token=bot_token,
            owner_id=int(owner_raw),
            db_path=os.getenv("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.getenv("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
        )


# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, path: str) -> None:
        self._path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    business_connection_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    subscription_until TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    nickname_mode TEXT NOT NULL DEFAULT 'time',
                    notify_deletions INTEGER NOT NULL DEFAULT 1,
                    timezone_offset_hours REAL,
                    trial_used INTEGER NOT NULL DEFAULT 0,
                    expiry_notified INTEGER NOT NULL DEFAULT 0,
                    target_datetime TEXT,
                    countdown_label TEXT,
                    greeting_enabled INTEGER NOT NULL DEFAULT 0,
                    greeting_text TEXT,
                    xo_emoji_x TEXT NOT NULL DEFAULT '❌',
                    xo_emoji_o TEXT NOT NULL DEFAULT '⭕'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_chats (
                    owner_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (owner_id, chat_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS xo_games (
                    chat_id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    business_connection_id TEXT NOT NULL,
                    board TEXT NOT NULL,
                    turn TEXT NOT NULL,
                    message_id INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS button_settings (
                    button_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    style TEXT,
                    icon_custom_emoji_id TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

            # Таблица для кеша сообщений
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cached_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    message_id INTEGER NOT NULL,
                    text TEXT,
                    reply_to_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(chat_id, message_id)
                )
                """
            )
            
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cached_messages_chat_msg ON cached_messages(chat_id, message_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cached_messages_created ON cached_messages(created_at)"
            )

            existing_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            for col, ddl in (
                ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
                ("started_at", "ALTER TABLE users ADD COLUMN started_at TEXT"),
                ("subscription_until", "ALTER TABLE users ADD COLUMN subscription_until TEXT"),
                ("is_admin", "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
                ("nickname_mode", "ALTER TABLE users ADD COLUMN nickname_mode TEXT NOT NULL DEFAULT 'time'"),
                ("notify_deletions", "ALTER TABLE users ADD COLUMN notify_deletions INTEGER NOT NULL DEFAULT 1"),
                ("timezone_offset_hours", "ALTER TABLE users ADD COLUMN timezone_offset_hours REAL"),
                ("trial_used", "ALTER TABLE users ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0"),
                ("expiry_notified", "ALTER TABLE users ADD COLUMN expiry_notified INTEGER NOT NULL DEFAULT 0"),
                ("target_datetime", "ALTER TABLE users ADD COLUMN target_datetime TEXT"),
                ("countdown_label", "ALTER TABLE users ADD COLUMN countdown_label TEXT"),
                ("greeting_enabled", "ALTER TABLE users ADD COLUMN greeting_enabled INTEGER NOT NULL DEFAULT 0"),
                ("greeting_text", "ALTER TABLE users ADD COLUMN greeting_text TEXT"),
                ("xo_emoji_x", "ALTER TABLE users ADD COLUMN xo_emoji_x TEXT NOT NULL DEFAULT '❌'"),
                ("xo_emoji_o", "ALTER TABLE users ADD COLUMN xo_emoji_o TEXT NOT NULL DEFAULT '⭕'"),
            ):
                if col not in existing_cols:
                    conn.execute(ddl)

            for key, default_label in BUTTON_KEYS.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO button_settings (button_key, label, style, icon_custom_emoji_id)
                    VALUES (?, ?, NULL, NULL)
                    """,
                    (key, default_label),
                )

            for key, default_value in TEXT_KEYS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (key, default_value),
                )
            for key in ("feedback_target_id", "feedback_target_username"):
                conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, NULL)",
                    (key,),
                )
            conn.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('subscription_price_stars', ?)",
                (str(SUBSCRIPTION_STARS_DEFAULT),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('message_cache_days', ?)",
                (str(MESSAGE_CACHE_DAYS),),
            )

            migrated = conn.execute(
                "SELECT value FROM bot_settings WHERE key = 'welcome_text_v3_applied'"
            ).fetchone()
            if migrated is None:
                conn.execute(
                    "UPDATE bot_settings SET value = ? WHERE key = 'welcome_text'",
                    (WELCOME_TEXT_DEFAULT,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('welcome_text_v3_applied', '1')"
                )

    # --- глобальные настройки/тексты --------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None or row["value"] is None:
            return default
        return row["value"]

    def set_setting(self, key: str, value: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_text(self, key: str) -> str:
        return self.get_setting(key, TEXT_KEYS.get(key, ""))

    def get_price(self) -> int:
        raw = self.get_setting("subscription_price_stars", str(SUBSCRIPTION_STARS_DEFAULT))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return SUBSCRIPTION_STARS_DEFAULT

    def set_price(self, stars: int) -> None:
        self.set_setting("subscription_price_stars", str(int(stars)))

    # --- пользователи -----------------------------------------------------

    def upsert_user(
        self, user_id: int, first_name: str, username: Optional[str], default_tz: float = 3.0
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, first_name, username, started_at, timezone_offset_hours)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
                """,
                (user_id, first_name, username, datetime.now(timezone.utc).isoformat(), default_tz),
            )

    def set_timezone(self, user_id: int, offset_hours: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET timezone_offset_hours = ? WHERE user_id = ?",
                (offset_hours, user_id),
            )

    def get_timezone(self, row: sqlite3.Row, default: float = 3.0) -> float:
        value = row["timezone_offset_hours"] if row else None
        return float(value) if value is not None else default

    def mark_trial_used(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,)
            )

    def set_connection(self, user_id: int, connection_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET business_connection_id = ? WHERE user_id = ?",
                (connection_id, user_id),
            )

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET enabled = ? WHERE user_id = ?",
                (int(enabled), user_id),
            )

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def get_user_by_connection(self, connection_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE business_connection_id = ?", (connection_id,)
            ).fetchone()

    def set_nickname_mode(self, user_id: int, mode: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET nickname_mode = ? WHERE user_id = ?",
                (mode, user_id),
            )

    def set_target_datetime(self, user_id: int, target_iso: Optional[str], label: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET target_datetime = ?, countdown_label = ? WHERE user_id = ?",
                (target_iso, label, user_id),
            )

    def set_notify_deletions(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET notify_deletions = ? WHERE user_id = ?",
                (int(enabled), user_id),
            )

    def set_greeting(self, user_id: int, enabled: bool, text: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET greeting_enabled = ?, greeting_text = ? WHERE user_id = ?",
                (int(enabled), text, user_id),
            )

    def set_xo_emojis(self, user_id: int, emoji_x: Optional[str], emoji_o: Optional[str]) -> None:
        with self.connect() as conn:
            if emoji_x is not None:
                conn.execute("UPDATE users SET xo_emoji_x = ? WHERE user_id = ?", (emoji_x, user_id))
            if emoji_o is not None:
                conn.execute("UPDATE users SET xo_emoji_o = ? WHERE user_id = ?", (emoji_o, user_id))

    # --- автоприветствие / первый контакт ----------------------------------

    def register_first_contact(self, owner_id: int, chat_id: int) -> bool:
        """Возвращает True, если это первое сообщение от chat_id владельцу owner_id."""
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM known_chats WHERE owner_id = ? AND chat_id = ?",
                (owner_id, chat_id),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO known_chats (owner_id, chat_id, first_seen_at) VALUES (?, ?, ?)",
                (owner_id, chat_id, datetime.now(timezone.utc).isoformat()),
            )
            return True

    # --- игра XO -------------------------------------------------------

    def create_xo_game(self, chat_id: int, owner_id: int, connection_id: str, board: str, turn: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO xo_games (chat_id, owner_id, business_connection_id, board, turn, message_id, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    business_connection_id = excluded.business_connection_id,
                    board = excluded.board,
                    turn = excluded.turn,
                    message_id = NULL,
                    created_at = excluded.created_at
                """,
                (chat_id, owner_id, connection_id, board, turn, datetime.now(timezone.utc).isoformat()),
            )

    def set_xo_message_id(self, chat_id: int, message_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE xo_games SET message_id = ? WHERE chat_id = ?", (message_id, chat_id))

    def get_xo_game(self, chat_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM xo_games WHERE chat_id = ?", (chat_id,)).fetchone()

    def update_xo_game(self, chat_id: int, board: str, turn: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE xo_games SET board = ?, turn = ? WHERE chat_id = ?", (board, turn, chat_id)
            )

    def delete_xo_game(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM xo_games WHERE chat_id = ?", (chat_id,))

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, first_name, business_connection_id, nickname_mode,
                       target_datetime, countdown_label, timezone_offset_hours
                FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
                """
            ).fetchall()

    def get_all_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY started_at DESC"
            ).fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_active_subscribers(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE subscription_until IS NOT NULL AND subscription_until > ?",
                (now,),
            ).fetchone()["c"]

    def count_connected(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE business_connection_id IS NOT NULL"
            ).fetchone()["c"]

    def count_enabled(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE enabled = 1"
            ).fetchone()["c"]

    # --- подписка -----------------------------------------------------

    def extend_subscription(self, user_id: int, seconds: int) -> None:
        now = datetime.now(timezone.utc)
        row = self.get_user(user_id)
        current_until = None
        if row and row["subscription_until"]:
            try:
                current_until = datetime.fromisoformat(row["subscription_until"])
            except ValueError:
                current_until = None

        base = current_until if current_until and current_until > now else now
        new_until = base + timedelta(seconds=seconds)

        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET subscription_until = ?, expiry_notified = 0 WHERE user_id = ?",
                (new_until.isoformat(), user_id),
            )

    def is_subscribed(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id:
            return True

        row = self.get_user(user_id)
        if not row or not row["subscription_until"]:
            return False
        try:
            until = datetime.fromisoformat(row["subscription_until"])
        except ValueError:
            return False
        return until > datetime.now(timezone.utc)

    # --- роли -----------------------------------------------------

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET is_admin = ? WHERE user_id = ?",
                (int(is_admin), user_id),
            )

    def is_admin(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id:
            return True
        row = self.get_user(user_id)
        return bool(row and row["is_admin"])

    def get_users_needing_expiry_warning(self, warn_seconds: int) -> list[sqlite3.Row]:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(seconds=warn_seconds)
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM users
                WHERE subscription_until IS NOT NULL
                  AND subscription_until > ?
                  AND subscription_until <= ?
                  AND expiry_notified = 0
                """,
                (now.isoformat(), soon.isoformat()),
            ).fetchall()

    def mark_expiry_notified(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET expiry_notified = 1 WHERE user_id = ?", (user_id,)
            )

    def get_admins(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE is_admin = 1 ORDER BY started_at DESC"
            ).fetchall()

    # --- настройки кнопок ---------------------------------------------

    def get_button(self, key: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM button_settings WHERE button_key = ?", (key,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown button key: {key}")
        return row

    def get_all_buttons(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM button_settings ORDER BY button_key"
            ).fetchall()

    def set_button_label(self, key: str, label: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE button_settings SET label = ? WHERE button_key = ?",
                (label, key),
            )

    def set_button_style(self, key: str, style: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE button_settings SET style = ? WHERE button_key = ?",
                (style, key),
            )

    def set_button_emoji(self, key: str, emoji_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE button_settings SET icon_custom_emoji_id = ? WHERE button_key = ?",
                (emoji_id, key),
            )

    # --- кеш сообщений ---------------------------------------------

    def cache_message(self, message: Message) -> None:
        if not message.text and not message.caption:
            return
            
        text = message.text or message.caption or ""
        
        if len(text) > 4000:
            text = text[:4000] + "..."
            
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cached_messages 
                (chat_id, user_id, username, first_name, message_id, text, reply_to_message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat.id,
                    message.from_user.id if message.from_user else 0,
                    message.from_user.username if message.from_user else None,
                    message.from_user.first_name if message.from_user else None,
                    message.message_id,
                    text,
                    message.reply_to_message.message_id if message.reply_to_message else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_cached_message(self, chat_id: int, message_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM cached_messages 
                WHERE chat_id = ? AND message_id = ?
                """,
                (chat_id, message_id)
            ).fetchone()
            if row:
                return dict(row)
            return None

    def get_cached_messages(self, chat_id: int, message_ids: List[int]) -> List[dict]:
        if not message_ids:
            return []
            
        placeholders = ",".join("?" for _ in message_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM cached_messages 
                WHERE chat_id = ? AND message_id IN ({placeholders})
                ORDER BY message_id
                """,
                (chat_id, *message_ids)
            ).fetchall()
            return [dict(row) for row in rows]

    def clear_old_messages(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM cached_messages WHERE created_at < ?",
                (cutoff.isoformat(),)
            )
            return cursor.rowcount

    def get_message_cache_days(self) -> int:
        raw = self.get_setting("message_cache_days", str(MESSAGE_CACHE_DAYS))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return MESSAGE_CACHE_DAYS

    def set_message_cache_days(self, days: int) -> None:
        self.set_setting("message_cache_days", str(max(1, int(days))))


# ---------------------------------------------------------------------------
# Режимы отображения в нике
# ---------------------------------------------------------------------------

NICKNAME_MODES = {
    "time": "🕐 Время",
    "date": "📅 Дата",
    "countdown": "⏳ Обратный отсчёт",
    "countup": "📈 Счётчик дней",
}

MODES_NEEDING_TARGET_DATE = {"countdown", "countup"}


def parse_target_datetime(text: str) -> Optional[datetime]:
    text = text.strip()
    formats = (
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


_DATE_PREFIX_RE = re.compile(
    r"^(\d{1,2}[./]\d{1,2}[./]\d{4}(?:\s+\d{1,2}:\d{2})?|\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?)\s*(.*)$"
)


def parse_target_input(text: str) -> tuple[Optional[datetime], Optional[str]]:
    text = text.strip()
    match = _DATE_PREFIX_RE.match(text)
    if not match:
        return None, None
    date_part, label_part = match.group(1), match.group(2).strip()
    dt = parse_target_datetime(date_part)
    return dt, (label_part or None)


def local_naive_to_utc(naive_dt: datetime, tz_offset_hours: float) -> datetime:
    return (naive_dt - timedelta(hours=tz_offset_hours)).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Часы/дата/отсчёты в нике
# ---------------------------------------------------------------------------

class NicknameClock:
    def __init__(self, bot: Bot, db: Database, default_tz_offset_hours: float) -> None:
        self._bot = bot
        self._db = db
        self._default_tz_offset_hours = default_tz_offset_hours
        self._last_applied: dict[int, str] = {}

    def _time_label(self, tz_offset_hours: float) -> str:
        tz = timezone(timedelta(hours=tz_offset_hours))
        return datetime.now(tz).strftime("• [%H:%M]")

    def _date_label(self, tz_offset_hours: float) -> str:
        tz = timezone(timedelta(hours=tz_offset_hours))
        return datetime.now(tz).strftime("• %d.%m.%Y")

    def _countdown_label(self, target_datetime: Optional[str], countdown_label: Optional[str]) -> str:
        if not target_datetime:
            return "• Дата не задана"
        try:
            target = datetime.fromisoformat(target_datetime)
        except ValueError:
            return "• Дата не задана"
        now = datetime.now(timezone.utc)
        delta = target - now
        prefix = f"{countdown_label} " if countdown_label else ""
        if delta.total_seconds() <= 0:
            return f"• {prefix}Наступило!"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"• {prefix}{days}д {hours}ч"
        minutes = (delta.seconds % 3600) // 60
        return f"• {prefix}{hours}ч {minutes}м"

    def _countup_label(self, target_datetime: Optional[str], countdown_label: Optional[str]) -> str:
        if not target_datetime:
            return "• Дата не задана"
        try:
            target = datetime.fromisoformat(target_datetime)
        except ValueError:
            return "• Дата не задана"
        now = datetime.now(timezone.utc)
        delta = now - target
        prefix = f"{countdown_label} " if countdown_label else ""
        if delta.total_seconds() < 0:
            return f"• {prefix}ещё не началось"
        days = delta.days
        return f"• {prefix}день {days}"

    def _label_for_mode(
        self, mode: str, tz_offset_hours: float,
        target_datetime: Optional[str] = None, countdown_label: Optional[str] = None,
    ) -> str:
        if mode == "date":
            return self._date_label(tz_offset_hours)
        if mode == "countdown":
            return self._countdown_label(target_datetime, countdown_label)
        if mode == "countup":
            return self._countup_label(target_datetime, countdown_label)
        return self._time_label(tz_offset_hours)

    async def apply(
        self, user_id: int, connection_id: str, first_name: str,
        mode: str = "time", tz_offset_hours: Optional[float] = None,
        target_datetime: Optional[str] = None, countdown_label: Optional[str] = None,
    ) -> None:
        if not connection_id:
            return

        if tz_offset_hours is None:
            tz_offset_hours = self._default_tz_offset_hours

        label = self._label_for_mode(mode, tz_offset_hours, target_datetime, countdown_label)
        if self._last_applied.get(user_id) == label:
            return

        try:
            await self._bot(
                SetBusinessAccountName(
                    business_connection_id=connection_id,
                    first_name=first_name,
                    last_name=label,
                )
            )
            self._last_applied[user_id] = label
        except Exception:
            logger.exception("Failed to update nickname for user_id=%s", user_id)
            await self._handle_permission_loss(user_id)

    async def _handle_permission_loss(self, user_id: int) -> None:
        self._db.set_enabled(user_id, False)
        try:
            await self._bot.send_message(
                user_id,
                "Недостаточно прав для смены фамилии. "
                "Переподключите бота в настройках, разрешив изменение имени.",
            )
        except Exception:
            logger.exception("Failed to notify user_id=%s about permission loss", user_id)

    async def clear(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        try:
            await self._bot(
                SetBusinessAccountName(
                    business_connection_id=connection_id,
                    first_name=first_name,
                    last_name="",
                )
            )
        except Exception:
            logger.exception("Failed to clear nickname for user_id=%s", user_id)
        finally:
            self._last_applied.pop(user_id, None)


# ---------------------------------------------------------------------------
# Построение клавиатур
# ---------------------------------------------------------------------------

def _button(db: Database, key: str, callback_data: Optional[str] = None,
            url: Optional[str] = None, copy_text: Optional[CopyTextButton] = None) -> InlineKeyboardButton:
    row = db.get_button(key)
    kwargs: dict = {"text": row["label"]}
    if row["style"] in BUTTON_STYLES:
        kwargs["style"] = row["style"]
    if row["icon_custom_emoji_id"]:
        kwargs["icon_custom_emoji_id"] = row["icon_custom_emoji_id"]
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if copy_text is not None:
        kwargs["copy_text"] = copy_text
    return InlineKeyboardButton(**kwargs)


def build_pay_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(db, "pay", callback_data="pay_subscription")],
            [_button(db, "feedback", callback_data="start_feedback")],
        ]
    )


def build_toggle_keyboard(db: Database, enabled: bool) -> InlineKeyboardMarkup:
    key = "toggle_off" if enabled else "toggle_on"
    action = "toggle_off" if enabled else "toggle_on"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(db, key, callback_data=action)],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")],
            [_button(db, "feedback", callback_data="start_feedback")],
        ]
    )


def with_admin_button(kb: InlineKeyboardMarkup, show: bool) -> InlineKeyboardMarkup:
    """Добавляет отдельную кнопку входа в админ-панель, видимую только владельцу бота."""
    if not show:
        return kb
    rows = list(kb.inline_keyboard) + [[InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_home")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_settings_keyboard(current_mode: str, notify_deletions: bool) -> InlineKeyboardMarkup:
    mode_buttons = []
    for mode, label in NICKNAME_MODES.items():
        text = f"✅ {label}" if mode == current_mode else label
        mode_buttons.append(InlineKeyboardButton(text=text, callback_data=f"set_mode:{mode}"))

    notify_label = "🔔 Уведомл. об удалении: вкл" if notify_deletions else "🔕 Уведомл. об удалении: выкл"

    rows = [mode_buttons[:2], mode_buttons[2:]]
    if current_mode in MODES_NEEDING_TARGET_DATE:
        rows.append([InlineKeyboardButton(text="🗓 Задать дату", callback_data="set_target_date")])
    rows.append([InlineKeyboardButton(text=notify_label, callback_data="toggle_notify_deletions")])
    rows.append([InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="tz_menu")])
    rows.append([InlineKeyboardButton(text="🤝 Автоприветствие", callback_data="greeting_menu")])
    rows.append([InlineKeyboardButton(text="❌⭕ Игра XO", callback_data="xo_settings_menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_greeting_text(enabled: bool, text: Optional[str]) -> str:
    status = "включено ✅" if enabled else "выключено ❌"
    lines = [
        "<b>🤝 Автоприветствие</b>\n",
        f"Статус: <b>{status}</b>",
        "Срабатывает один раз для каждого нового собеседника, который впервые "
        "пишет вам в чат, подключённый через бизнес-режим.",
    ]
    if text:
        lines.append(f"\nТекущий текст:\n<i>{text}</i>")
    else:
        lines.append("\nТекст ещё не задан.")
    return "\n".join(lines)


def build_greeting_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔕 Выключить" if enabled else "🔔 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_label, callback_data="greeting_toggle")],
            [InlineKeyboardButton(text="✏️ Задать текст", callback_data="greeting_set_text")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")],
        ]
    )


def build_xo_settings_text(emoji_x: str, emoji_o: str) -> str:
    return (
        "<b>❌⭕ Игра «Крестики-нолики»</b>\n\n"
        f"Ваш символ: {emoji_x}\n"
        f"Символ собеседника: {emoji_o}\n\n"
        "Чтобы начать игру, напишите <code>.xo</code> в любом чате, подключённом "
        "через бизнес-режим — вам придёт игровое поле 3×3 с кнопками."
    )


def build_xo_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить свой символ", callback_data="xo_set_emoji_x")],
            [InlineKeyboardButton(text="✏️ Изменить символ собеседника", callback_data="xo_set_emoji_o")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")],
        ]
    )


def build_settings_text(current_mode: str, tz_offset: float, target_datetime: Optional[str] = None, countdown_label: Optional[str] = None) -> str:
    lines = [
        "<b>⚙️ Настройки</b>\n",
        f"Формат ника: <b>{NICKNAME_MODES.get(current_mode, current_mode)}</b>",
        f"Часовой пояс: <b>UTC{tz_offset:+g}</b>",
    ]
    if current_mode in MODES_NEEDING_TARGET_DATE:
        if target_datetime:
            try:
                dt_utc = datetime.fromisoformat(target_datetime)
                local_tz = timezone(timedelta(hours=tz_offset))
                date_str = dt_utc.astimezone(local_tz).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                date_str = "не задана"
            lines.append(f"Целевая дата: <b>{date_str}</b>" + (f" ({countdown_label})" if countdown_label else ""))
        else:
            lines.append("Целевая дата: <b>не задана</b>, нажмите «🗓 Задать дату»")
    lines.append(
        "\n🔔 Уведомления об удалении: бот пришлёт вам содержимое удалённых сообщений."
    )
    return "\n".join(lines)


TIMEZONE_QUICK_OFFSETS = [-5, -3, 0, 1, 2, 3, 4, 5, 5.5, 7, 8, 9]

TIMEZONE_NAME_MAP = {
    "мск": 3, "москва": 3, "moscow": 3, "msk": 3, "спб": 3,
    "калининград": 2, "kaliningrad": 2,
    "самара": 4, "samara": 4,
    "екатеринбург": 5, "ekaterinburg": 5,
    "омск": 6, "omsk": 6,
    "новосибирск": 7, "novosibirsk": 7, "красноярск": 7, "krasnoyarsk": 7,
    "иркутск": 8, "irkutsk": 8,
    "владивосток": 10, "vladivostok": 10,
    "киев": 2, "kyiv": 2, "kiev": 2, "минск": 3, "minsk": 3,
    "лондон": 0, "london": 0, "gmt": 0, "utc": 0,
    "берлин": 1, "berlin": 1, "париж": 1, "paris": 1, "рим": 1, "rome": 1,
    "нью-йорк": -5, "new york": -5, "ny": -5,
    "лос-анджелес": -8, "los angeles": -8, "la": -8,
    "дубай": 4, "dubai": 4,
    "стамбул": 3, "istanbul": 3,
    "токио": 9, "tokyo": 9,
    "пекин": 8, "beijing": 8, "shanghai": 8,
    "дели": 5.5, "delhi": 5.5, "india": 5.5,
    "алматы": 6, "almaty": 6, "ташкент": 5, "tashkent": 5,
    "баку": 4, "baku": 4, "ереван": 4, "yerevan": 4, "тбилиси": 4, "tbilisi": 4,
}


def parse_timezone_input(text: str) -> Optional[float]:
    text = text.strip().lower()
    if not text:
        return None

    if text in TIMEZONE_NAME_MAP:
        return float(TIMEZONE_NAME_MAP[text])

    match = re.search(r"[+-]?\d+(?:[.,]\d+)?", text)
    if match:
        try:
            value = float(match.group(0).replace(",", "."))
        except ValueError:
            return None
        if -12 <= value <= 14:
            return value
        return None

    return None


def build_timezone_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for offset in TIMEZONE_QUICK_OFFSETS:
        label = f"UTC{offset:+g}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"set_tz:{offset}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="tz_manual")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(db, "connect", url="tg://settings/edit")],
            [_button(db, "copy", copy_text=CopyTextButton(text=f"@{bot_username}"))],
            [_button(db, "feedback", callback_data="start_feedback")],
        ]
    )


def build_not_connected_text(db: Database) -> str:
    return db.get_text("not_connected_text")


def build_subscription_required_text(db: Database) -> str:
    text = db.get_text("subscription_required_text")
    try:
        return text.format(price=db.get_price())
    except (KeyError, IndexError):
        return text


def build_connected_text(db: Database) -> str:
    return db.get_text("connected_text")


def build_welcome_text(db: Database) -> str:
    return db.get_text("welcome_text")


# ---------------------------------------------------------------------------
# Игра "Крестики-нолики" (.xo)
# ---------------------------------------------------------------------------

XO_WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def xo_check_winner(board: str) -> Optional[str]:
    for a, b, c in XO_WIN_LINES:
        if board[a] != "." and board[a] == board[b] == board[c]:
            return board[a]
    if "." not in board:
        return "draw"
    return None


def build_xo_keyboard(chat_id: int, board: str, emoji_x: str, emoji_o: str, finished: bool) -> InlineKeyboardMarkup:
    symbols = {".": "‌ ", "X": emoji_x, "O": emoji_o}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            cell = board[idx]
            data = "xo:noop" if finished else (f"xo:{idx}" if cell == "." else "xo:noop")
            row.append(InlineKeyboardButton(text=symbols[cell], callback_data=data))
        rows.append(row)
    if finished:
        rows.append([InlineKeyboardButton(text="🔁 Новая игра", callback_data="xo:restart")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_xo_status_text(board: str, emoji_x: str, emoji_o: str, turn: str, owner_name: str,
                          friend_name: str, winner: Optional[str]) -> str:
    if winner == "draw":
        return "🎮 <b>Крестики-нолики</b>\n\nНичья! 🤝"
    if winner:
        winner_name = owner_name if winner == "X" else friend_name
        winner_emoji = emoji_x if winner == "X" else emoji_o
        return f"🎮 <b>Крестики-нолики</b>\n\n{winner_emoji} Победа: {winner_name}! 🎉"
    turn_name = owner_name if turn == "owner" else friend_name
    turn_emoji = emoji_x if turn == "owner" else emoji_o
    return f"🎮 <b>Крестики-нолики</b>\n\nХодит {turn_emoji} {turn_name}"


async def start_xo_game(bot: Bot, db: "Database", owner_id: int, connection_id: str,
                         chat_id: int, owner_row: sqlite3.Row, friend_name: str) -> None:
    board = "." * 9
    db.create_xo_game(chat_id, owner_id, connection_id, board, "owner")
    emoji_x = owner_row["xo_emoji_x"] or "❌"
    emoji_o = owner_row["xo_emoji_o"] or "⭕"
    owner_name = owner_row["first_name"] or "Владелец"
    text = build_xo_status_text(board, emoji_x, emoji_o, "owner", owner_name, friend_name, None)
    kb = build_xo_keyboard(chat_id, board, emoji_x, emoji_o, False)
    try:
        sent = await bot.send_message(
            chat_id, text, parse_mode="HTML", reply_markup=kb,
            business_connection_id=connection_id,
        )
        db.set_xo_message_id(chat_id, sent.message_id)
    except Exception:
        logger.exception("Failed to start XO game for owner_id=%s chat_id=%s", owner_id, chat_id)


# ---------------------------------------------------------------------------
# Админ-панель (только для владельца)
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_label = State()
    waiting_emoji = State()
    waiting_grant_sub_id = State()
    waiting_timezone_text = State()
    waiting_feedback_message = State()
    waiting_feedback_target = State()
    waiting_text_edit = State()
    waiting_target_date = State()
    waiting_price_edit = State()
    waiting_cache_days = State()
    waiting_greeting_text = State()
    waiting_xo_emoji_x = State()
    waiting_xo_emoji_o = State()


def build_admin_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="🎨 Кнопки", callback_data="admin_buttons")],
        [InlineKeyboardButton(text="📝 Тексты", callback_data="admin_texts")],
        [InlineKeyboardButton(text="✉️ Обратная связь", callback_data="admin_feedback")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_grant_sub")],
        [InlineKeyboardButton(text="💰 Цена подписки", callback_data="admin_price")],
        [InlineKeyboardButton(text="💾 Кеш сообщений", callback_data="admin_cache")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_texts_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"admin_text:{key}")]
        for key, label in TEXT_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_text_edit_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_settext:{key}")],
            [InlineKeyboardButton(text="↩️ Сбросить по умолчанию", callback_data=f"admin_resettext:{key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_texts")],
        ]
    )


def build_admin_feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить получателя", callback_data="admin_set_feedback_target")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")],
        ]
    )


def build_admin_buttons_keyboard(db: Database) -> InlineKeyboardMarkup:
    rows = []
    for row in db.get_all_buttons():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{row['label']}",
                    callback_data=f"admin_btn:{row['button_key']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_button_edit_keyboard(button_key: str) -> InlineKeyboardMarkup:
    style_row = [
        InlineKeyboardButton(text="🔵 Primary", callback_data=f"admin_style:{button_key}:primary"),
        InlineKeyboardButton(text="🔴 Danger", callback_data=f"admin_style:{button_key}:danger"),
        InlineKeyboardButton(text="🟢 Success", callback_data=f"admin_style:{button_key}:success"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"admin_setlabel:{button_key}")],
            style_row,
            [InlineKeyboardButton(text="⚪️ Сбросить цвет", callback_data=f"admin_style:{button_key}:none")],
            [InlineKeyboardButton(text="✨ Задать premium emoji", callback_data=f"admin_setemoji:{button_key}")],
            [InlineKeyboardButton(text="🚫 Убрать emoji", callback_data=f"admin_clearemoji:{button_key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")],
        ]
    )


def build_admin_users_keyboard(users: list[sqlite3.Row], page: int, page_size: int = 10) -> InlineKeyboardMarkup:
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users:{page - 1}"))
    if (page + 1) * page_size < len(users):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users:{page + 1}"))
    rows = []
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_users_page(users: list[sqlite3.Row], page: int, page_size: int = 10) -> str:
    start = page * page_size
    chunk = users[start:start + page_size]
    if not chunk:
        return "Пользователей пока нет."

    now_iso = datetime.now(timezone.utc).isoformat()
    lines = [f"<b>Пользователи (стр. {page + 1})</b>\n"]
    for u in chunk:
        uname = f"@{u['username']}" if u["username"] else "(нет username)"
        sub_mark = "✅" if (u["subscription_until"] and u["subscription_until"] > now_iso) else "нет"
        admin_mark = " 🛡" if u["is_admin"] else ""
        lines.append(f"• <code>{u['user_id']}</code> {uname}{admin_mark}, подписка: {sub_mark}")
    return "\n".join(lines)


def parse_user_id(text: str) -> Optional[int]:
    text = text.strip().lstrip("@")
    if text.isdigit():
        return int(text)
    return None


def build_admin_cache_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить старые", callback_data="admin_cache_clear")],
            [InlineKeyboardButton(text="📊 Статистика кеша", callback_data="admin_cache_stats")],
            [InlineKeyboardButton(text="📅 Срок хранения", callback_data="admin_cache_days")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")],
        ]
    )


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

def register_handlers(
    dp: Dispatcher,
    db: Database,
    clock: NicknameClock,
    bot_username: str,
    settings: Settings,
) -> None:

    def is_owner(user_id: int) -> bool:
        return user_id == settings.owner_id

    # ------------------------- Кеширование сообщений -------------------------
    
    @dp.business_message()
    async def handle_business_message(message: Message) -> None:
        """Сохраняет все сообщения в кеш для последующего отображения при удалении"""
        if message.text or message.caption:
            db.cache_message(message)
            
        if message.reply_to_message:
            db.cache_message(message.reply_to_message)

        connection_id = message.business_connection_id
        if not connection_id:
            return
        owner_row = db.get_user_by_connection(connection_id)
        if not owner_row:
            return
        owner_id = owner_row["user_id"]
        chat_id = message.chat.id
        is_from_owner = bool(message.from_user and message.from_user.id == owner_id)

        # Запуск игры в крестики-нолики командой .xo от владельца аккаунта
        if is_from_owner and message.text and message.text.strip().lower() == ".xo":
            friend_name = message.chat.first_name or "Собеседник"
            await start_xo_game(message.bot, db, owner_id, connection_id, chat_id, owner_row, friend_name)
            return

        # Автоприветствие для тех, кто пишет впервые
        if not is_from_owner:
            is_first = db.register_first_contact(owner_id, chat_id)
            if is_first and owner_row["greeting_enabled"] and owner_row["greeting_text"]:
                try:
                    await message.bot.send_message(
                        chat_id,
                        owner_row["greeting_text"],
                        business_connection_id=connection_id,
                    )
                except Exception:
                    logger.exception("Failed to send greeting for owner_id=%s", owner_id)

    @dp.callback_query(F.data.startswith("xo:"))
    async def handle_xo_move(callback: CallbackQuery) -> None:
        if not callback.message:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        game = db.get_xo_game(chat_id)
        if not game:
            await callback.answer("Игра не найдена или уже завершена")
            return

        data = callback.data.split(":", 1)[1]
        connection_id = game["business_connection_id"]
        owner_id = game["owner_id"]
        owner_row = db.get_user(owner_id)
        emoji_x = (owner_row["xo_emoji_x"] if owner_row else None) or "❌"
        emoji_o = (owner_row["xo_emoji_o"] if owner_row else None) or "⭕"
        owner_name = (owner_row["first_name"] if owner_row else None) or "Владелец"
        friend_name = callback.message.chat.first_name or "Собеседник"

        if data == "restart":
            board = "." * 9
            db.create_xo_game(chat_id, owner_id, connection_id, board, "owner")
            text = build_xo_status_text(board, emoji_x, emoji_o, "owner", owner_name, friend_name, None)
            kb = build_xo_keyboard(chat_id, board, emoji_x, emoji_o, False)
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id, message_id=callback.message.message_id,
                    text=text, parse_mode="HTML", reply_markup=kb,
                    business_connection_id=connection_id,
                )
                db.set_xo_message_id(chat_id, callback.message.message_id)
            except Exception:
                logger.exception("Failed to restart XO game chat_id=%s", chat_id)
            await callback.answer()
            return

        if data == "noop":
            await callback.answer()
            return

        board = game["board"]
        turn = game["turn"]

        if xo_check_winner(board):
            await callback.answer("Игра завершена, нажмите «Новая игра»", show_alert=True)
            return

        try:
            idx = int(data)
        except ValueError:
            await callback.answer()
            return
        if idx < 0 or idx > 8 or board[idx] != ".":
            await callback.answer()
            return

        is_owner_tap = callback.from_user.id == owner_id
        expected_owner_turn = turn == "owner"
        if is_owner_tap != expected_owner_turn:
            await callback.answer("Сейчас не ваш ход", show_alert=True)
            return

        symbol = "X" if turn == "owner" else "O"
        new_board = board[:idx] + symbol + board[idx + 1:]
        winner = xo_check_winner(new_board)
        next_turn = "friend" if turn == "owner" else "owner"
        db.update_xo_game(chat_id, new_board, next_turn)

        text = build_xo_status_text(new_board, emoji_x, emoji_o, next_turn, owner_name, friend_name, winner)
        kb = build_xo_keyboard(chat_id, new_board, emoji_x, emoji_o, bool(winner))
        try:
            await callback.bot.edit_message_text(
                chat_id=chat_id, message_id=callback.message.message_id,
                text=text, parse_mode="HTML", reply_markup=kb,
                business_connection_id=connection_id,
            )
        except Exception:
            logger.exception("Failed to update XO board chat_id=%s", chat_id)
        await callback.answer()

    # ------------------------- пользовательская часть -------------------------

    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        user_id = message.from_user.id
        is_new = db.get_user(user_id) is None
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username, float(settings.timezone_offset_hours))

        if is_new:
            await message.answer(build_welcome_text(db), parse_mode="HTML")
            row = db.get_user(user_id)
            if row and not row["trial_used"]:
                db.extend_subscription(user_id, TRIAL_PERIOD_SECONDS)
                db.mark_trial_used(user_id)
                await message.answer(
                    "🎁 Вам начислен бесплатный пробный период на 3 дня.",
                    parse_mode="HTML",
                )

        if not db.is_subscribed(user_id, settings.owner_id):
            await message.answer(
                build_subscription_required_text(db),
                parse_mode="HTML",
                reply_markup=build_pay_keyboard(db),
            )
            return

        row = db.get_user(user_id)
        is_connected = bool(row and row["business_connection_id"])

        if not is_connected:
            await message.answer(
                build_not_connected_text(db),
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(db, bot_username),
            )
            return

        is_enabled = bool(row["enabled"])
        status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
        await message.answer(
            status_text, parse_mode="HTML",
            reply_markup=with_admin_button(build_toggle_keyboard(db, is_enabled), is_owner(user_id)),
        )

    @dp.callback_query(F.data == "pay_subscription")
    async def handle_pay_subscription(callback: CallbackQuery) -> None:
        price = db.get_price()
        prices = [LabeledPrice(label="Подписка на 30 дней", amount=price)]
        link = await callback.bot.create_invoice_link(
            title="Подписка SiaTimeBot",
            description=f"Доступ к боту на 30 дней за {price} Stars",
            payload=SUBSCRIPTION_PAYLOAD,
            currency="XTR",
            prices=prices,
            subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
        )
        await callback.message.answer(
            "Нажмите кнопку ниже, чтобы оплатить прямо в Telegram:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=f"⭐ Оплатить {price} Stars", url=link)]]
            ),
        )
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        if pre_checkout_query.invoice_payload == SUBSCRIPTION_PAYLOAD:
            await pre_checkout_query.answer(ok=True)
        else:
            await pre_checkout_query.answer(ok=False, error_message="Неизвестный платёж.")

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        if payment.invoice_payload != SUBSCRIPTION_PAYLOAD:
            return

        user_id = message.from_user.id
        db.extend_subscription(user_id, SUBSCRIPTION_PERIOD_SECONDS)

        await message.answer(
            "<b>Подписка активирована ✅</b>\nВам доступны все функции бота на 30 дней.",
            parse_mode="HTML",
        )

        row = db.get_user(user_id)
        is_connected = bool(row and row["business_connection_id"])
        if not is_connected:
            await message.answer(
                build_not_connected_text(db),
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(db, bot_username),
            )

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username, float(settings.timezone_offset_hours))

        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            if not db.is_subscribed(user_id, settings.owner_id):
                try:
                    await connection.bot.send_message(
                        user_id,
                        build_subscription_required_text(db),
                        parse_mode="HTML",
                        reply_markup=build_pay_keyboard(db),
                    )
                except Exception:
                    logger.exception("Failed to notify user_id=%s about subscription", user_id)
                return
            try:
                await connection.bot.send_message(
                    user_id,
                    build_connected_text(db),
                    parse_mode="HTML",
                    reply_markup=build_toggle_keyboard(db, False),
                )
            except Exception:
                logger.exception("Failed to send connection confirmation to user_id=%s", user_id)
            return

        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")

        db.set_connection(user_id, None)
        db.set_enabled(user_id, False)

    @dp.callback_query(F.data == "toggle_on")
    async def handle_toggle_on(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id

        if not db.is_subscribed(user_id, settings.owner_id):
            await callback.answer("Требуется активная подписка", show_alert=True)
            return

        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("Бот не подключён", show_alert=True)
            return

        db.set_enabled(user_id, True)
        await clock.apply(
            user_id, row["business_connection_id"], row["first_name"] or "",
            row["nickname_mode"], db.get_timezone(row, settings.timezone_offset_hours),
            row["target_datetime"], row["countdown_label"],
        )
        await callback.message.edit_text(
            "<b>Время в нике включено.</b>",
            parse_mode="HTML",
            reply_markup=build_toggle_keyboard(db, True),
        )
        await callback.answer()

    @dp.callback_query(F.data == "toggle_off")
    async def handle_toggle_off(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)

        if not row:
            await callback.answer()
            return

        db.set_enabled(user_id, False)
        if row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")

        await callback.message.edit_text(
            "<b>Время в нике выключено.</b>",
            parse_mode="HTML",
            reply_markup=build_toggle_keyboard(db, False),
        )
        await callback.answer()

    @dp.callback_query(F.data == "settings_menu")
    async def handle_settings_menu(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row:
            await callback.answer()
            return
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        await callback.message.edit_text(
            build_settings_text(row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"]),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(row["nickname_mode"], bool(row["notify_deletions"])),
        )
        await callback.answer()

    @dp.callback_query(F.data == "settings_back")
    async def handle_settings_back(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        is_enabled = bool(row and row["enabled"])
        status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
        await callback.message.edit_text(
            status_text, parse_mode="HTML",
            reply_markup=with_admin_button(build_toggle_keyboard(db, is_enabled), is_owner(user_id)),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_mode:"))
    async def handle_set_mode(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        mode = callback.data.split(":", 1)[1]
        if mode not in NICKNAME_MODES:
            await callback.answer()
            return
        db.set_nickname_mode(user_id, mode)
        row = db.get_user(user_id)
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(
                user_id, row["business_connection_id"], row["first_name"] or "",
                mode, tz_offset, row["target_datetime"], row["countdown_label"],
            )
        await callback.message.edit_text(
            build_settings_text(mode, tz_offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(mode, bool(row["notify_deletions"]) if row else True),
        )
        await callback.answer("Формат обновлён")

    @dp.callback_query(F.data == "toggle_notify_deletions")
    async def handle_toggle_notify_deletions(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row:
            await callback.answer()
            return
        new_value = not bool(row["notify_deletions"])
        db.set_notify_deletions(user_id, new_value)
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        await callback.message.edit_text(
            build_settings_text(row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"]),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(row["nickname_mode"], new_value),
        )
        await callback.answer()

    @dp.callback_query(F.data == "tz_menu")
    async def handle_tz_menu(callback: CallbackQuery) -> None:
        await callback.message.edit_text(
            "<b>🌍 Выберите часовой пояс</b>\n\n"
            "Можно выбрать кнопкой ниже или ввести вручную числом (например "
            "<code>+3</code>, <code>-5.5</code>) или названием города/страны "
            "(например «Москва», «London», «Дубай»).",
            parse_mode="HTML",
            reply_markup=build_timezone_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_tz:"))
    async def handle_set_tz(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        try:
            offset = float(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer()
            return
        db.set_timezone(user_id, offset)
        row = db.get_user(user_id)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(
                user_id, row["business_connection_id"], row["first_name"] or "",
                row["nickname_mode"], offset, row["target_datetime"], row["countdown_label"],
            )
        await callback.message.edit_text(
            build_settings_text(row["nickname_mode"] if row else "time", offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True),
        )
        await callback.answer("Часовой пояс обновлён")

    @dp.callback_query(F.data == "tz_manual")
    async def handle_tz_manual_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_timezone_text)
        await callback.message.answer(
            "Введите часовой пояс числом (например <code>+3</code>) или названием "
            "города/страны (например «Алматы»):",
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_timezone_text)
    async def handle_tz_manual_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        offset = parse_timezone_input(message.text or "")
        await state.clear()
        if offset is None:
            await message.answer(
                "Не удалось распознать часовой пояс. Попробуйте число от -12 до +14 "
                "(например -5.5) или известное название города."
            )
            return
        db.set_timezone(user_id, offset)
        row = db.get_user(user_id)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(
                user_id, row["business_connection_id"], row["first_name"] or "",
                row["nickname_mode"], offset, row["target_datetime"], row["countdown_label"],
            )
        await message.answer(
            build_settings_text(row["nickname_mode"] if row else "time", offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True),
        )

    @dp.callback_query(F.data == "greeting_menu")
    async def handle_greeting_menu(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        enabled = bool(row and row["greeting_enabled"])
        text = row["greeting_text"] if row else None
        await callback.message.edit_text(
            build_greeting_text(enabled, text),
            parse_mode="HTML",
            reply_markup=build_greeting_keyboard(enabled),
        )
        await callback.answer()

    @dp.callback_query(F.data == "greeting_toggle")
    async def handle_greeting_toggle(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row:
            await callback.answer()
            return
        new_enabled = not bool(row["greeting_enabled"])
        if new_enabled and not row["greeting_text"]:
            await callback.answer("Сначала задайте текст приветствия", show_alert=True)
            return
        db.set_greeting(user_id, new_enabled, row["greeting_text"])
        await callback.message.edit_text(
            build_greeting_text(new_enabled, row["greeting_text"]),
            parse_mode="HTML",
            reply_markup=build_greeting_keyboard(new_enabled),
        )
        await callback.answer()

    @dp.callback_query(F.data == "greeting_set_text")
    async def handle_greeting_set_text_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_greeting_text)
        await callback.message.answer(
            "Отправьте текст, который будет отправляться каждому новому собеседнику "
            "при первом сообщении вам:"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_greeting_text)
    async def handle_greeting_set_text_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        text = (message.text or "").strip()
        await state.clear()
        if not text:
            await message.answer("Текст не может быть пустым, попробуйте снова.")
            return
        row = db.get_user(user_id)
        was_enabled = bool(row and row["greeting_enabled"])
        db.set_greeting(user_id, True, text)
        await message.answer(
            build_greeting_text(True, text),
            parse_mode="HTML",
            reply_markup=build_greeting_keyboard(True),
        )

    @dp.callback_query(F.data == "xo_settings_menu")
    async def handle_xo_settings_menu(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        emoji_x = (row["xo_emoji_x"] if row else None) or "❌"
        emoji_o = (row["xo_emoji_o"] if row else None) or "⭕"
        await callback.message.edit_text(
            build_xo_settings_text(emoji_x, emoji_o),
            parse_mode="HTML",
            reply_markup=build_xo_settings_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "xo_set_emoji_x")
    async def handle_xo_set_emoji_x_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_xo_emoji_x)
        await callback.message.answer("Отправьте эмодзи, которым будете играть вы (например ❌, 🔥, 🐱):")
        await callback.answer()

    @dp.message(AdminStates.waiting_xo_emoji_x)
    async def handle_xo_set_emoji_x_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        emoji = (message.text or "").strip()
        await state.clear()
        if not emoji:
            await message.answer("Пусто, попробуйте снова.")
            return
        emoji = emoji[:8]
        db.set_xo_emojis(user_id, emoji, None)
        row = db.get_user(user_id)
        emoji_o = (row["xo_emoji_o"] if row else None) or "⭕"
        await message.answer(
            build_xo_settings_text(emoji, emoji_o),
            parse_mode="HTML",
            reply_markup=build_xo_settings_keyboard(),
        )

    @dp.callback_query(F.data == "xo_set_emoji_o")
    async def handle_xo_set_emoji_o_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_xo_emoji_o)
        await callback.message.answer("Отправьте эмодзи для собеседника (например ⭕, 💧, 🐶):")
        await callback.answer()

    @dp.message(AdminStates.waiting_xo_emoji_o)
    async def handle_xo_set_emoji_o_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        emoji = (message.text or "").strip()
        await state.clear()
        if not emoji:
            await message.answer("Пусто, попробуйте снова.")
            return
        emoji = emoji[:8]
        db.set_xo_emojis(user_id, None, emoji)
        row = db.get_user(user_id)
        emoji_x = (row["xo_emoji_x"] if row else None) or "❌"
        await message.answer(
            build_xo_settings_text(emoji_x, emoji),
            parse_mode="HTML",
            reply_markup=build_xo_settings_keyboard(),
        )

    @dp.callback_query(F.data == "set_target_date")
    async def handle_set_target_date_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_target_date)
        await callback.message.answer(
            "Отправьте дату (и, если нужно, время и подпись) одним сообщением.\n\n"
            "Форматы: <code>31.12.2026</code>, <code>31.12.2026 20:00</code>, "
            "<code>2026-12-31 20:00</code>.\n"
            "Можно добавить подпись после даты, например:\n"
            "<code>31.12.2026 20:00 До Нового года</code>\n"
            "<code>14.02.2020 Вместе с</code>",
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_target_date)
    async def handle_set_target_date_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        await state.clear()
        naive_dt, label = parse_target_input(message.text or "")
        if naive_dt is None:
            await message.answer(
                "Не удалось распознать дату. Попробуйте, например: "
                "<code>31.12.2026 20:00 До Нового года</code>",
                parse_mode="HTML",
            )
            return
        row = db.get_user(user_id)
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        dt_utc = local_naive_to_utc(naive_dt, tz_offset)
        db.set_target_datetime(user_id, dt_utc.isoformat(), label)
        row = db.get_user(user_id)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(
                user_id, row["business_connection_id"], row["first_name"] or "",
                row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"],
            )
        await message.answer(
            build_settings_text(row["nickname_mode"] if row else "time", tz_offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None),
            parse_mode="HTML",
            reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True),
        )

    # ----------------------------- Удалённые сообщения с цитированием -------------------------------------

    def format_author(cached: Optional[dict], chat: User) -> str:
        """Форматирует автора сообщения"""
        if cached:
            if cached.get("username"):
                return f"@{cached['username']}"
            if cached.get("first_name"):
                return cached["first_name"]
        if chat.username:
            return f"@{chat.username}"
        return " ".join(filter(None, [chat.first_name, chat.last_name])) or "Собеседник"

    @dp.deleted_business_messages()
    async def handle_deleted_business_messages(deleted: BusinessMessagesDeleted) -> None:
        """Обработка удалённых сообщений с цитированием"""
        row = db.get_user_by_connection(deleted.business_connection_id)
        if not row or not row["notify_deletions"]:
            return

        chat = deleted.chat
        count = len(deleted.message_ids)
        
        # Получаем закешированные сообщения
        cached_messages = db.get_cached_messages(chat.id, deleted.message_ids)
        
        if count == 1 and cached_messages:
            # Одно сообщение - показываем с цитированием
            msg = cached_messages[0]
            author = format_author(msg, chat)
            text = msg.get("text", "⚠️ Текст сообщения не сохранён")
            
            # Ограничиваем длину для безопасности
            if len(text) > 1000:
                text = text[:1000] + "..."
            
            # Экранируем HTML
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            
            deleted_text = db.get_text("deleted_message_text").format(
                author=author,
                text=text
            )
            
            await deleted.bot.send_message(row["user_id"], deleted_text, parse_mode="HTML")
            
        elif count > 1 and cached_messages:
            # Несколько сообщений - показываем список
            author = format_author(cached_messages[0] if cached_messages else None, chat)
            
            messages_list = []
            for i, msg in enumerate(cached_messages[:5], 1):
                text = msg.get("text", "⚠️ Текст не сохранён")
                if len(text) > 200:
                    text = text[:200] + "..."
                text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                messages_list.append(f"{i}. {text}")
            
            if len(cached_messages) > 5:
                messages_list.append(f"... и ещё {count - 5} сообщений")
            
            messages_text = "\n".join(messages_list)
            
            deleted_text = db.get_text("deleted_messages_text").format(
                count=count,
                author=author,
                messages=messages_text
            )
            
            await deleted.bot.send_message(row["user_id"], deleted_text, parse_mode="HTML")
            
        else:
            # Сообщения не найдены в кеше
            author = format_author(None, chat)
            if count == 1:
                word = "сообщение"
            elif 2 <= count % 10 <= 4 and not (11 <= count % 100 <= 14):
                word = "сообщения"
            else:
                word = "сообщений"
                
            await deleted.bot.send_message(
                row["user_id"],
                f"🗑 <b>Новое удалённое сообщение</b>\n\n"
                f"👤 <b>Автор:</b> {author}\n"
                f"⚠️ Текст сообщения не сохранён в кеше.",
                parse_mode="HTML"
            )

    # ------------------------------ обратная связь (пользователь) ------------------------------

    @dp.message(Command("cancel"))
    async def handle_cancel_any(message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        if current is None:
            await message.answer("Нечего отменять.")
            return
        await state.clear()
        await message.answer("Отменено.")

    async def _start_feedback(user_id: int, first_name: str, username: Optional[str], state: FSMContext, answer_func) -> None:
        db.upsert_user(user_id, first_name, username, float(settings.timezone_offset_hours))
        await state.set_state(AdminStates.waiting_feedback_message)
        await answer_func(
            "✉️ Напишите одним сообщением, что хотите передать поддержке. "
            "Чтобы отменить, нажмите «Отмена» или отправьте /cancel.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="feedback_cancel")]]
            ),
        )

    @dp.message(Command("feedback"))
    async def handle_feedback_start(message: Message, state: FSMContext) -> None:
        await _start_feedback(
            message.from_user.id, message.from_user.first_name or "", message.from_user.username,
            state, message.answer,
        )

    @dp.callback_query(F.data == "start_feedback")
    async def handle_feedback_button(callback: CallbackQuery, state: FSMContext) -> None:
        await _start_feedback(
            callback.from_user.id, callback.from_user.first_name or "", callback.from_user.username,
            state, callback.message.answer,
        )
        await callback.answer()

    @dp.callback_query(F.data == "feedback_cancel")
    async def handle_feedback_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text("Отменено.")
        await callback.answer()

    @dp.message(AdminStates.waiting_feedback_message)
    async def handle_feedback_finish(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.text:
            await message.answer("Пока поддерживается только текст. Напишите /feedback ещё раз, если нужно.")
            return

        target_raw = db.get_setting("feedback_target_id")
        if not target_raw:
            await message.answer("Обратная связь пока не настроена администратором бота. Попробуйте позже.")
            return

        try:
            target_id = int(target_raw)
        except ValueError:
            await message.answer("Обратная связь пока не настроена администратором бота. Попробуйте позже.")
            return

        sender = message.from_user
        who = f"@{sender.username}" if sender.username else (sender.first_name or "без имени")
        forward_text = (
            f"📩 <b>Обратная связь</b>\n"
            f"От: {who} (<code>{sender.id}</code>)\n\n"
            f"{message.text}"
        )
        try:
            await message.bot.send_message(target_id, forward_text, parse_mode="HTML")
            await message.answer("✅ Спасибо, ваше сообщение передано.")
        except Exception:
            logger.exception("Failed to deliver feedback from user_id=%s", sender.id)
            await message.answer("Не получилось доставить сообщение. Попробуйте позже.")

    # ------------------------------ админ-панель (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ------------------------------

    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        user_id = message.from_user.id
        # Команда /admin доступна только владельцу
        if not is_owner(user_id):
            return
        
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username, float(settings.timezone_offset_hours))
        await message.answer(
            "<b>👑 Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard(),
        )

    @dp.callback_query(F.data == "admin_home")
    async def admin_home(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text(
            "<b>👑 Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        total = db.count_users()
        subscribed = db.count_active_subscribers()
        connected = db.count_connected()
        enabled = db.count_enabled()
        
        with db.connect() as conn:
            cache_count = conn.execute("SELECT COUNT(*) AS c FROM cached_messages").fetchone()["c"]
        
        text = (
            "<b>📊 Статистика</b>\n\n"
            f"Всего пользователей (/start): <b>{total}</b>\n"
            f"Активных подписок: <b>{subscribed}</b>\n"
            f"Подключили бизнес-бота: <b>{connected}</b>\n"
            f"Сейчас включена функция: <b>{enabled}</b>\n"
            f"Сообщений в кеше: <b>{cache_count}</b>"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]]
            ),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache")
    async def admin_cache_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
            
        days = db.get_message_cache_days()
        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM cached_messages").fetchone()["c"]
            oldest = conn.execute(
                "SELECT MIN(created_at) AS min FROM cached_messages"
            ).fetchone()["min"]
            
        text = (
            "<b>💾 Управление кешем сообщений</b>\n\n"
            f"Всего сообщений в кеше: <b>{total}</b>\n"
            f"Срок хранения: <b>{days} дней</b>\n"
            f"Самое старое сообщение: <b>{oldest or 'нет'}</b>\n\n"
            "Сообщения хранятся для показа содержимого при удалении."
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=build_admin_cache_keyboard()
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_stats")
    async def admin_cache_stats(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
            
        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM cached_messages").fetchone()["c"]
            by_user = conn.execute(
                "SELECT user_id, COUNT(*) as count FROM cached_messages GROUP BY user_id ORDER BY count DESC LIMIT 10"
            ).fetchall()
            
        text = f"<b>📊 Статистика кеша</b>\n\nВсего: {total}\n\n<b>Топ-10 пользователей:</b>\n"
        for u in by_user:
            text += f"• {u['user_id']}: {u['count']} сообщений\n"
            
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cache")]]
            )
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_days")
    async def admin_cache_days_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
            
        await state.set_state(AdminStates.waiting_cache_days)
        await callback.message.answer(
            f"Текущий срок хранения: <b>{db.get_message_cache_days()} дней</b>\n\n"
            "Введите новое количество дней (например 30):",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_cache_days)
    async def admin_cache_days_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
            
        await state.clear()
        try:
            days = int(message.text.strip())
            if days < 1:
                await message.answer("Минимальное значение: 1 день.")
                return
            if days > 365:
                await message.answer("Максимальное значение: 365 дней.")
                return
                
            db.set_message_cache_days(days)
            await message.answer(f"✅ Срок хранения установлен на <b>{days} дней</b>.", parse_mode="HTML")
            
            await message.answer(
                "Хотите удалить сообщения старше этого срока?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🗑 Да, очистить", callback_data="admin_cache_clear_confirm")],
                        [InlineKeyboardButton(text="❌ Нет", callback_data="admin_cache")]
                    ]
                )
            )
        except ValueError:
            await message.answer("Пожалуйста, введите целое число.")

    @dp.callback_query(F.data == "admin_cache_clear")
    async def admin_cache_clear_start(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
            
        days = db.get_message_cache_days()
        with db.connect() as conn:
            old_count = conn.execute(
                "SELECT COUNT(*) AS c FROM cached_messages WHERE created_at < ?",
                (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            ).fetchone()["c"]
            
        await callback.message.answer(
            f"Будут удалены сообщения старше <b>{days} дней</b>.\n"
            f"Таких сообщений: <b>{old_count}</b>\n\n"
            "Продолжить?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Да, очистить", callback_data="admin_cache_clear_confirm")],
                    [InlineKeyboardButton(text="❌ Нет", callback_data="admin_cache")]
                ]
            )
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_clear_confirm")
    async def admin_cache_clear_confirm(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
            
        days = db.get_message_cache_days()
        deleted = db.clear_old_messages(days)
        
        await callback.message.edit_text(
            f"✅ Удалено <b>{deleted}</b> старых сообщений.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cache")]]
            )
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        page = int(callback.data.split(":")[1])
        users = db.get_all_users()
        await callback.message.edit_text(
            format_users_page(users, page),
            parse_mode="HTML",
            reply_markup=build_admin_users_keyboard(users, page),
        )
        await callback.answer()

    # --- редактируемые тексты бота ---

    @dp.callback_query(F.data == "admin_texts")
    async def admin_texts_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text(
            "<b>📝 Тексты бота</b>\nВыберите, что изменить:",
            parse_mode="HTML",
            reply_markup=build_admin_texts_keyboard(),
        )
        await callback.answer()

    def _text_preview(key: str) -> str:
        return f"<b>{TEXT_LABELS.get(key, key)}</b>\n\n{db.get_text(key)}"

    @dp.callback_query(F.data.startswith("admin_text:"))
    async def admin_text_view(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.edit_text(
            _text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key)
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_settext:"))
    async def admin_settext_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(text_key=key)
        await state.set_state(AdminStates.waiting_text_edit)
        await callback.message.answer(
            "Отправьте новый текст (можно с HTML-разметкой: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;):"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_text_edit)
    async def admin_settext_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        data = await state.get_data()
        key = data.get("text_key")
        await state.clear()
        if not key or not message.text:
            return
        db.set_setting(key, message.text)
        await message.answer(
            "Текст обновлён.",
            parse_mode="HTML",
        )
        await message.answer(
            _text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key)
        )

    @dp.callback_query(F.data.startswith("admin_resettext:"))
    async def admin_resettext(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        db.set_setting(key, TEXT_KEYS.get(key))
        await callback.answer("Сброшено")
        await callback.message.edit_text(
            _text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key)
        )

    # --- обратная связь: получатель ---

    @dp.callback_query(F.data == "admin_feedback")
    async def admin_feedback_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        target_id = db.get_setting("feedback_target_id")
        target_username = db.get_setting("feedback_target_username")
        if target_id:
            desc = f"user_id <code>{target_id}</code>" + (f" (@{target_username})" if target_username else "")
        else:
            desc = "не настроен"
        await callback.message.edit_text(
            f"<b>✉️ Обратная связь</b>\n\nСообщения из /feedback сейчас приходят: {desc}.",
            parse_mode="HTML",
            reply_markup=build_admin_feedback_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_set_feedback_target")
    async def admin_set_feedback_target_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_feedback_target)
        await callback.message.answer(
            "Отправьте user_id или @username получателя обратной связи.\n\n"
            "⚠️ Если укажете @username, бот сможет доставлять сообщения только если "
            "этот человек уже хотя бы раз запускал бота (/start). Telegram не позволяет "
            "писать по username в обход этого."
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_feedback_target)
    async def admin_set_feedback_target_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        raw = (message.text or "").strip()
        target_id = parse_user_id(raw)
        if target_id is not None:
            db.set_setting("feedback_target_id", str(target_id))
            target_row = db.get_user(target_id)
            db.set_setting(
                "feedback_target_username",
                target_row["username"] if target_row and target_row["username"] else None,
            )
            await message.answer(f"✅ Обратная связь теперь идёт на <code>{target_id}</code>.", parse_mode="HTML")
            return

        username = raw.lstrip("@")
        with db.connect() as conn:
            found = conn.execute(
                "SELECT user_id FROM users WHERE username = ?", (username,)
            ).fetchone()
        if found:
            db.set_setting("feedback_target_id", str(found["user_id"]))
            db.set_setting("feedback_target_username", username)
            await message.answer(f"✅ Обратная связь теперь идёт на @{username}.", parse_mode="HTML")
        else:
            db.set_setting("feedback_target_id", None)
            db.set_setting("feedback_target_username", username)
            await message.answer(
                f"Пользователь @{username} ещё не запускал этого бота. Сохранил username, "
                "но доставка заработает только после того, как он хоть раз нажмёт /start."
            )

    # --- кнопки: текст / цвет / premium emoji ---

    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text(
            "<b>🎨 Настройка кнопок</b>\nВыберите кнопку:",
            parse_mode="HTML",
            reply_markup=build_admin_buttons_keyboard(db),
        )
        await callback.answer()

    def _button_edit_text(key: str) -> str:
        row = db.get_button(key)
        return (
            f"<b>Кнопка:</b> {key}\n"
            f"Текст: {row['label']}\n"
            f"Цвет: {row['style'] or 'по умолчанию'}\n"
            f"Premium emoji ID: {row['icon_custom_emoji_id'] or 'нет'}\n\n"
            f"<i>Premium emoji на кнопке отображается только если у владельца бота "
            f"есть активная подписка Telegram Premium.</i>"
        )

    @dp.callback_query(F.data.startswith("admin_btn:"))
    async def admin_btn_edit(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.edit_text(
            _button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key)
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_style:"))
    async def admin_set_style(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        _, key, style = callback.data.split(":")
        db.set_button_style(key, None if style == "none" else style)
        await callback.answer("Цвет обновлён")
        await callback.message.edit_text(
            _button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key)
        )

    @dp.callback_query(F.data.startswith("admin_clearemoji:"))
    async def admin_clear_emoji(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        db.set_button_emoji(key, None)
        await callback.answer("Emoji убран")
        await callback.message.edit_text(
            _button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key)
        )

    @dp.callback_query(F.data.startswith("admin_setlabel:"))
    async def admin_set_label_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_label)
        await callback.message.answer(f"Отправьте новый текст для кнопки «{key}»:")
        await callback.answer()

    @dp.message(AdminStates.waiting_label)
    async def admin_set_label_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        db.set_button_label(key, message.text.strip())
        await state.clear()
        await message.answer(
            f"Текст кнопки «{key}» обновлён.",
            parse_mode="HTML",
            reply_markup=build_admin_button_edit_keyboard(key),
        )

    @dp.callback_query(F.data.startswith("admin_setemoji:"))
    async def admin_set_emoji_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji)
        await callback.message.answer(
            "Отправьте сюда сообщение с premium-эмодзи (просто отправьте стикер-эмодзи "
            "как обычное сообщение), я возьму его custom_emoji_id автоматически.\n\n"
            "Работает только если у владельца бота есть Telegram Premium."
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji)
    async def admin_set_emoji_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return

        emoji_id: Optional[str] = None
        if message.entities:
            for entity in message.entities:
                if entity.type == "custom_emoji" and entity.custom_emoji_id:
                    emoji_id = entity.custom_emoji_id
                    break

        if not emoji_id and message.text:
            candidate = message.text.strip()
            if candidate.isdigit():
                emoji_id = candidate

        if not emoji_id:
            await message.answer(
                "Не нашёл premium-эмодзи в сообщении. Отправьте сообщение, "
                "содержащее именно premium-эмодзи, либо пришлите его числовой ID."
            )
            return

        db.set_button_emoji(key, emoji_id)
        await state.clear()
        await message.answer(
            f"Premium emoji для кнопки «{key}» установлен.",
            parse_mode="HTML",
            reply_markup=build_admin_button_edit_keyboard(key),
        )

    # --- выдача подписок ---

    @dp.callback_query(F.data == "admin_grant_sub")
    async def admin_grant_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_grant_sub_id)
        await callback.message.answer(
            "Отправьте user_id пользователя, которому выдать подписку на 30 дней:"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_sub_id)
    async def admin_grant_sub_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        target_id = parse_user_id(message.text or "")
        await state.clear()
        if target_id is None:
            await message.answer("Некорректный user_id. Отправьте число.")
            return

        db.extend_subscription(target_id, SUBSCRIPTION_PERIOD_SECONDS)
        await message.answer(f"✅ Подписка на 30 дней выдана пользователю <code>{target_id}</code>.", parse_mode="HTML")

        try:
            await message.bot.send_message(
                target_id,
                "🎁 Вам выдана подписка на 30 дней администратором бота.",
            )
        except Exception:
            logger.info("Could not notify user_id=%s about granted subscription", target_id)

    # --- цена подписки ---

    @dp.callback_query(F.data == "admin_price")
    async def admin_price_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_price_edit)
        await callback.message.edit_text(
            f"<b>💰 Цена подписки</b>\n\n"
            f"Текущая цена: <b>{db.get_price()} ⭐</b> за 30 дней.\n\n"
            f"Отправьте новое число Stars (например <code>25</code>), чтобы изменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]]
            ),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_price_edit)
    async def admin_price_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        raw = (message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await message.answer("Введите положительное целое число Stars, например 25.")
            return
        db.set_price(int(raw))
        await message.answer(
            f"✅ Цена подписки обновлена: {db.get_price()} ⭐ за 30 дней.",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard(),
        )


# ---------------------------------------------------------------------------
# Периодическое обновление ников и очистка кеша
# ---------------------------------------------------------------------------

def seconds_until_next_minute() -> float:
    now = datetime.now(timezone.utc)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05


async def run_update_loop(bot: Bot, db: Database, clock: NicknameClock, default_tz: float, owner_id: int) -> None:
    last_cache_cleanup = datetime.now(timezone.utc)
    
    while True:
        delay = seconds_until_next_minute()
        await asyncio.sleep(delay)

        try:
            for row in db.get_enabled_users():
                user_id = row["user_id"]
                try:
                    if not db.is_subscribed(user_id, owner_id):
                        db.set_enabled(user_id, False)
                        await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                        continue
                    tz_offset = db.get_timezone(row, default_tz)
                    await clock.apply(
                        user_id, row["business_connection_id"], row["first_name"] or "",
                        row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"],
                    )
                except Exception:
                    logger.exception("Failed to update nickname for user_id=%s in update loop", user_id)
        except Exception:
            logger.exception("Failed to fetch enabled users in update loop")

        try:
            for row in db.get_users_needing_expiry_warning(EXPIRY_WARNING_SECONDS):
                db.mark_expiry_notified(row["user_id"])
                try:
                    await bot.send_message(
                        row["user_id"],
                        "⏳ Ваша подписка заканчивается меньше чем через 2 дня. "
                        "Продлите её командой /start, чтобы не потерять доступ.",
                    )
                except Exception:
                    logger.info("Could not send expiry warning to user_id=%s", row["user_id"])
        except Exception:
            logger.exception("Failed to process expiry warnings in update loop")

        # Очистка старого кеша раз в день
        now = datetime.now(timezone.utc)
        if (now - last_cache_cleanup).total_seconds() > 86400:
            try:
                days = db.get_message_cache_days()
                deleted = db.clear_old_messages(days)
                if deleted > 0:
                    logger.info("Cleaned up %d old cached messages", deleted)
                last_cache_cleanup = now
            except Exception:
                logger.exception("Failed to clean up old cached messages")


async def run_update_loop_supervised(bot: Bot, db: Database, clock: NicknameClock, default_tz: float, owner_id: int) -> None:
    while True:
        try:
            await run_update_loop(bot, db, clock, default_tz, owner_id)
        except Exception:
            logger.exception("run_update_loop crashed, restarting in 5 seconds")
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = Settings.from_env()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    db = Database(settings.db_path)
    db.init_schema()
    db.upsert_user(settings.owner_id, "", None, float(settings.timezone_offset_hours))
    db.set_admin(settings.owner_id, True)

    clock = NicknameClock(bot, db, float(settings.timezone_offset_hours))

    me = await bot.get_me()
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота / статус"),
        BotCommand(command="feedback", description="Написать в поддержку"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])
    register_handlers(dp, db, clock, me.username, settings)

    asyncio.create_task(run_update_loop_supervised(bot, db, clock, float(settings.timezone_offset_hours), settings.owner_id))

    # Поллинг оборачиваем в цикл с авто-рестартом, чтобы разрыв сети или
    # временная ошибка Telegram API не останавливали бота насовсем.
    while True:
        try:
            await dp.start_polling(bot)
        except Exception:
            logger.exception("Polling crashed, restarting in 5 seconds")
            await asyncio.sleep(5)
        else:
            break


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            break
        except Exception:
            logging.exception("main() crashed, restarting in 5 seconds")
            import time as _time
            _time.sleep(5)