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
from aiogram.exceptions import TelegramConflictError
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
# Константы
# ---------------------------------------------------------------------------

SUBSCRIPTION_STARS_DEFAULT = 15
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
SUBSCRIPTION_PAYLOAD = "timenick_subscription"
TRIAL_PERIOD_SECONDS = 3 * 24 * 60 * 60
EXPIRY_WARNING_SECONDS = 2 * 24 * 60 * 60
MESSAGE_CACHE_DAYS = 30

BUTTON_STYLES = ("primary", "danger", "success")

BUTTON_KEYS = {
    "toggle_on": "🔥 Включить",
    "toggle_off": "⛔ Выключить",
    "connect": "🔗 Подключить",
    "copy": "📋 Скопировать",
    "pay": "⭐ Оплатить подписку",
    "feedback": "✉️ Поддержка",
    "unmute": "🔊 Размутить",
    "help": "📚 Помощь",
    "admin_panel": "👑 Админ-панель",
}

XO_BUTTON_KEYS = {f"xo_cell_{i}": f"Клетка {i+1}" for i in range(9)}

WELCOME_TEXT_DEFAULT = (
    "🌟 <b>Добро пожаловать в SiaTimeBot!</b>\n\n"
    "Я показываю время прямо в твоём имени в Telegram, "
    "чтобы собеседники видели его в чате без сторонних приложений.\n\n"
    "🎮 <b>Доступные команды:</b>\n"
    "• <code>.xo</code> - Крестики-нолики\n"
    "• <code>.mute</code> - Замьютить собеседника\n"
    "• <code>.spam</code> - Отправить несколько сообщений\n"
    "• <code>.help</code> - Помощь\n\n"
    "⚙️ Настройки помогут изменить внешний вид и функции бота."
)

TEXT_KEYS = {
    "welcome_text": WELCOME_TEXT_DEFAULT,
    "not_connected_text": (
        "🔌 <b>Бот не подключён.</b>\n\n"
        "1️⃣ Нажмите <b>Подключить</b>\n"
        "2️⃣ Затем <b>Скопировать</b>\n"
        "3️⃣ Откройте <b>Автоматизация чатов</b>\n"
        "4️⃣ Вставьте скопированный текст и нажмите <b>Добавить</b>\n"
        "5️⃣ Разрешите <b>Управлять профилем</b>"
    ),
    "subscription_required_text": (
        "💎 <b>Требуется подписка</b>\n\n"
        "Доступ к боту стоит <b>{price} ⭐</b> в месяц.\n"
        "Оплатите подписку, чтобы пользоваться всеми функциями!"
    ),
    "connected_text": "✅ <b>Бот успешно подключён!</b>",
    "deleted_message_text": (
        "🗑 <b>Удалённое сообщение</b>\n\n"
        "👤 <b>Автор:</b> {author}\n"
        "📝 <b>Текст:</b>\n"
        "<blockquote>{text}</blockquote>"
    ),
    "deleted_messages_text": (
        "🗑 <b>Удалено {count} сообщений</b>\n\n"
        "👤 <b>Автор:</b> {author}\n"
        "{messages}"
    ),
    "help_text": (
        "📚 <b>Команды бота</b>\n\n"
        "🎮 <code>.xo</code> - Начать игру в крестики-нолики\n"
        "🔇 <code>.mute [время]</code> - Замьютить собеседника\n"
        "  Примеры: .mute, .mute 5 мин, .mute 1 час\n"
        "📨 <code>.spam [кол-во] [текст]</code> - Отправить N сообщений\n"
        "  Пример: .spam 10 Привет!\n"
        "🔊 <code>.unmute</code> - Размьютить собеседника\n"
        "❓ <code>.help</code> - Показать эту справку\n\n"
        "⚙️ <b>Настройки</b> - Изменить формат времени и уведомления\n"
        "🎮 <b>Игра XO</b> - Настроить символы для игры\n"
        "💬 <b>Поддержка</b> - Связаться с администратором"
    ),
}

TEXT_LABELS = {
    "welcome_text": "Приветствие (/start)",
    "not_connected_text": "Текст «бот не подключён»",
    "subscription_required_text": "Текст «нужна подписка»",
    "connected_text": "Текст «бот подключён»",
    "deleted_message_text": "Шаблон удалённого сообщения",
    "deleted_messages_text": "Шаблон нескольких удалённых сообщений",
    "help_text": "Текст помощи (/help)",
}


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
            raise RuntimeError("OWNER_ID is not set in .env")
        return cls(
            bot_token=bot_token,
            owner_id=int(owner_raw),
            db_path=os.getenv("DB_PATH", cls.db_path),
            timezone_offset_hours=int(os.getenv("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)),
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
            conn.execute("""
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
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS muted_users (
                    chat_id INTEGER NOT NULL,
                    muted_user_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    unmute_at TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, muted_user_id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS xo_games (
                    chat_id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    business_connection_id TEXT NOT NULL,
                    board TEXT NOT NULL,
                    turn TEXT NOT NULL,
                    message_id INTEGER,
                    created_at TEXT NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS button_settings (
                    button_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    style TEXT,
                    icon_custom_emoji_id TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.execute("""
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
            """)
            
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_messages_created ON cached_messages(created_at)")
            conn.execute("CREATE TABLE IF NOT EXISTS known_chats (owner_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, first_seen_at TEXT NOT NULL, PRIMARY KEY (owner_id, chat_id))")

            all_button_keys = {**BUTTON_KEYS, **XO_BUTTON_KEYS}
            for key, default_label in all_button_keys.items():
                conn.execute(
                    "INSERT OR IGNORE INTO button_settings (button_key, label, style, icon_custom_emoji_id) VALUES (?, ?, NULL, NULL)",
                    (key, default_label),
                )

            for key, default_value in TEXT_KEYS.items():
                conn.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)", (key, default_value))
            
            conn.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('subscription_price_stars', ?)", (str(SUBSCRIPTION_STARS_DEFAULT),))
            conn.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('message_cache_days', ?)", (str(MESSAGE_CACHE_DAYS),))
            conn.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('feedback_target_id', NULL)")
            conn.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('feedback_target_username', NULL)")

            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
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

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def get_text(self, key: str) -> str:
        return self.get_setting(key, TEXT_KEYS.get(key, ""))

    def get_price(self) -> int:
        raw = self.get_setting("subscription_price_stars", str(SUBSCRIPTION_STARS_DEFAULT))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return SUBSCRIPTION_STARS_DEFAULT

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username.lstrip("@"),)).fetchone()

    def get_user_by_connection(self, connection_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE business_connection_id = ?", (connection_id,)).fetchone()

    def upsert_user(self, user_id: int, first_name: str, username: Optional[str], default_tz: float = 3.0) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users (user_id, first_name, username, started_at, timezone_offset_hours) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET first_name = excluded.first_name, username = excluded.username",
                (user_id, first_name, username, datetime.now(timezone.utc).isoformat(), default_tz),
            )

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE user_id = ?", (int(is_admin), user_id))

    def is_admin(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id:
            return True
        row = self.get_user(user_id)
        return bool(row and row["is_admin"])

    def set_connection(self, user_id: int, connection_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET business_connection_id = ? WHERE user_id = ?", (connection_id, user_id))

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET enabled = ? WHERE user_id = ?", (int(enabled), user_id))

    def set_timezone(self, user_id: int, offset_hours: float) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET timezone_offset_hours = ? WHERE user_id = ?", (offset_hours, user_id))

    def get_timezone(self, row: sqlite3.Row, default: float = 3.0) -> float:
        value = row["timezone_offset_hours"] if row else None
        return float(value) if value is not None else default

    def set_nickname_mode(self, user_id: int, mode: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET nickname_mode = ? WHERE user_id = ?", (mode, user_id))

    def set_target_datetime(self, user_id: int, target_iso: Optional[str], label: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET target_datetime = ?, countdown_label = ? WHERE user_id = ?", (target_iso, label, user_id))

    def set_notify_deletions(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET notify_deletions = ? WHERE user_id = ?", (int(enabled), user_id))

    def set_greeting(self, user_id: int, enabled: bool, text: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET greeting_enabled = ?, greeting_text = ? WHERE user_id = ?", (int(enabled), text, user_id))

    def set_xo_emojis(self, user_id: int, emoji_x: Optional[str], emoji_o: Optional[str]) -> None:
        with self.connect() as conn:
            if emoji_x is not None:
                conn.execute("UPDATE users SET xo_emoji_x = ? WHERE user_id = ?", (emoji_x, user_id))
            if emoji_o is not None:
                conn.execute("UPDATE users SET xo_emoji_o = ? WHERE user_id = ?", (emoji_o, user_id))

    def mark_trial_used(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,))

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
            conn.execute("UPDATE users SET subscription_until = ?, expiry_notified = 0 WHERE user_id = ?", (new_until.isoformat(), user_id))

    def remove_subscription(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET subscription_until = NULL, expiry_notified = 0 WHERE user_id = ?", (user_id,))

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

    def get_users_needing_expiry_warning(self, warn_seconds: int) -> list[sqlite3.Row]:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(seconds=warn_seconds)
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE subscription_until IS NOT NULL AND subscription_until > ? AND subscription_until <= ? AND expiry_notified = 0",
                (now.isoformat(), soon.isoformat()),
            ).fetchall()

    def mark_expiry_notified(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET expiry_notified = 1 WHERE user_id = ?", (user_id,))

    def mute_user(self, chat_id: int, muted_user_id: int, owner_id: int, unmute_at: Optional[datetime] = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO muted_users (chat_id, muted_user_id, owner_id, unmute_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, muted_user_id, owner_id, unmute_at.isoformat() if unmute_at else None, datetime.now(timezone.utc).isoformat()),
            )

    def unmute_user(self, chat_id: int, muted_user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM muted_users WHERE chat_id = ? AND muted_user_id = ?", (chat_id, muted_user_id))

    def is_muted(self, chat_id: int, user_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT unmute_at FROM muted_users WHERE chat_id = ? AND muted_user_id = ?", (chat_id, user_id)).fetchone()
            if not row:
                return False
            if row["unmute_at"]:
                try:
                    unmute_at = datetime.fromisoformat(row["unmute_at"])
                    if unmute_at <= datetime.now(timezone.utc):
                        self.unmute_user(chat_id, user_id)
                        return False
                except ValueError:
                    pass
            return True

    def get_muted_info(self, chat_id: int, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM muted_users WHERE chat_id = ? AND muted_user_id = ?", (chat_id, user_id)).fetchone()

    def create_xo_game(self, chat_id: int, owner_id: int, connection_id: str, board: str, turn: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO xo_games (chat_id, owner_id, business_connection_id, board, turn, message_id, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET owner_id = excluded.owner_id, business_connection_id = excluded.business_connection_id, board = excluded.board, turn = excluded.turn, message_id = NULL, created_at = excluded.created_at",
                (chat_id, owner_id, connection_id, board, turn, datetime.now(timezone.utc).isoformat()),
            )

    def get_xo_game(self, chat_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM xo_games WHERE chat_id = ?", (chat_id,)).fetchone()

    def update_xo_game(self, chat_id: int, board: str, turn: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE xo_games SET board = ?, turn = ? WHERE chat_id = ?", (board, turn, chat_id))

    def set_xo_message_id(self, chat_id: int, message_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE xo_games SET message_id = ? WHERE chat_id = ?", (message_id, chat_id))

    def delete_xo_game(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM xo_games WHERE chat_id = ?", (chat_id,))

    def get_button(self, key: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM button_settings WHERE button_key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown button key: {key}")
        return row

    def get_all_buttons(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM button_settings ORDER BY button_key").fetchall()

    def set_button_label(self, key: str, label: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE button_settings SET label = ? WHERE button_key = ?", (label, key))

    def set_button_style(self, key: str, style: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE button_settings SET style = ? WHERE button_key = ?", (style, key))

    def set_button_emoji(self, key: str, emoji_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE button_settings SET icon_custom_emoji_id = ? WHERE button_key = ?", (emoji_id, key))

    def cache_message(self, message: Message) -> None:
        if not message.text and not message.caption:
            return
        text = message.text or message.caption or ""
        if len(text) > 4000:
            text = text[:4000] + "..."
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cached_messages (chat_id, user_id, username, first_name, message_id, text, reply_to_message_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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

    def get_cached_messages(self, chat_id: int, message_ids: List[int]) -> List[dict]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM cached_messages WHERE chat_id = ? AND message_id IN ({placeholders}) ORDER BY message_id", (chat_id, *message_ids)).fetchall()
            return [dict(row) for row in rows]

    def clear_old_messages(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM cached_messages WHERE created_at < ?", (cutoff.isoformat(),))
            return cursor.rowcount

    def get_message_cache_days(self) -> int:
        raw = self.get_setting("message_cache_days", str(MESSAGE_CACHE_DAYS))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return MESSAGE_CACHE_DAYS

    def get_all_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users ORDER BY started_at DESC").fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_active_subscribers(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users WHERE subscription_until IS NOT NULL AND subscription_until > ?", (now,)).fetchone()["c"]

    def count_connected(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users WHERE business_connection_id IS NOT NULL").fetchone()["c"]

    def count_enabled(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users WHERE enabled = 1").fetchone()["c"]

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT user_id, first_name, business_connection_id, nickname_mode, target_datetime, countdown_label, timezone_offset_hours FROM users WHERE enabled = 1 AND business_connection_id IS NOT NULL"
            ).fetchall()


# ---------------------------------------------------------------------------
# NicknameClock
# ---------------------------------------------------------------------------

NICKNAME_MODES = {"time": "🕐 Время", "date": "📅 Дата", "countdown": "⏳ Обратный отсчёт", "countup": "📈 Счётчик дней"}
MODES_NEEDING_TARGET_DATE = {"countdown", "countup"}

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

    def _label_for_mode(self, mode: str, tz_offset_hours: float, target_datetime: Optional[str] = None, countdown_label: Optional[str] = None) -> str:
        if mode == "date":
            return self._date_label(tz_offset_hours)
        if mode == "countdown":
            return self._countdown_label(target_datetime, countdown_label)
        if mode == "countup":
            return self._countup_label(target_datetime, countdown_label)
        return self._time_label(tz_offset_hours)

    async def apply(self, user_id: int, connection_id: str, first_name: str, mode: str = "time", tz_offset_hours: Optional[float] = None, target_datetime: Optional[str] = None, countdown_label: Optional[str] = None) -> None:
        if not connection_id:
            return
        if tz_offset_hours is None:
            tz_offset_hours = self._default_tz_offset_hours
        label = self._label_for_mode(mode, tz_offset_hours, target_datetime, countdown_label)
        if self._last_applied.get(user_id) == label:
            return
        try:
            await self._bot(SetBusinessAccountName(business_connection_id=connection_id, first_name=first_name, last_name=label))
            self._last_applied[user_id] = label
        except Exception:
            logger.exception("Failed to update nickname for user_id=%s", user_id)
            await self._handle_permission_loss(user_id)

    async def _handle_permission_loss(self, user_id: int) -> None:
        self._db.set_enabled(user_id, False)
        try:
            await self._bot.send_message(user_id, "❌ Недостаточно прав для смены фамилии. Переподключите бота в настройках, разрешив изменение имени.")
        except Exception:
            logger.exception("Failed to notify user_id=%s about permission loss", user_id)

    async def clear(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        try:
            await self._bot(SetBusinessAccountName(business_connection_id=connection_id, first_name=first_name, last_name=""))
        except Exception:
            logger.exception("Failed to clear nickname for user_id=%s", user_id)
        finally:
            self._last_applied.pop(user_id, None)


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def _button(db: Database, key: str, callback_data: Optional[str] = None, url: Optional[str] = None, copy_text: Optional[CopyTextButton] = None) -> InlineKeyboardButton:
    row = db.get_button(key)
    label = row["label"]
    # ВАЖНО: Bot API не поддерживает "цвет"/style для InlineKeyboardButton — это не реальное
    # поле Telegram Bot API, а также нет параметра icon_custom_emoji_id именно для инлайн-кнопок.
    # Если попытаться передать их напрямую в InlineKeyboardButton(**kwargs), aiogram выбросит
    # ValidationError и клавиатура вообще не соберётся (бот "молчит" на нажатие).
    # Поэтому мы храним style/emoji в БД (для будущей поддержки/иной отрисовки), но в саму кнопку
    # прокидываем только то, что реально поддерживается Bot API. Premium-эмодзи в текст кнопки
    # можно вставить как обычный символ эмодзи в label (администратор задаёт его через /admin).
    kwargs: dict = {"text": label}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if copy_text is not None:
        kwargs["copy_text"] = copy_text
    try:
        return InlineKeyboardButton(**kwargs)
    except Exception:
        logger.exception("Failed to build button for key=%s, falling back to plain button", key)
        fallback_kwargs = {"text": label}
        if callback_data is not None:
            fallback_kwargs["callback_data"] = callback_data
        elif url is not None:
            fallback_kwargs["url"] = url
        else:
            fallback_kwargs["callback_data"] = "noop"
        return InlineKeyboardButton(**fallback_kwargs)

def build_toggle_keyboard(db: Database, enabled: bool, show_admin: bool = False) -> InlineKeyboardMarkup:
    key = "toggle_off" if enabled else "toggle_on"
    action = "toggle_off" if enabled else "toggle_on"
    rows = [
        [_button(db, key, callback_data=action)],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")],
        [InlineKeyboardButton(text="📚 Помощь", callback_data="show_help")],
        [_button(db, "feedback", callback_data="start_feedback")],
    ]
    if show_admin:
        rows.append([_button(db, "admin_panel", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(db, "connect", url="tg://settings/edit")],
        [_button(db, "copy", copy_text=CopyTextButton(text=f"@{bot_username}"))],
        [InlineKeyboardButton(text="📚 Помощь", callback_data="show_help")],
        [_button(db, "feedback", callback_data="start_feedback")],
    ])

def build_pay_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(db, "pay", callback_data="pay_subscription")],
        [_button(db, "feedback", callback_data="start_feedback")],
    ])

def build_unmute_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(db, "unmute", callback_data="unmute_user")],
    ])

def build_help_text(db: Database) -> str:
    return db.get_text("help_text")

def build_welcome_text(db: Database) -> str:
    return db.get_text("welcome_text")

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

def build_settings_keyboard(current_mode: str, notify_deletions: bool) -> InlineKeyboardMarkup:
    mode_buttons = []
    for mode, label in NICKNAME_MODES.items():
        text = f"✅ {label}" if mode == current_mode else label
        mode_buttons.append(InlineKeyboardButton(text=text, callback_data=f"set_mode:{mode}"))
    notify_label = "🔔 Уведомления: вкл" if notify_deletions else "🔕 Уведомления: выкл"
    rows = [mode_buttons[:2], mode_buttons[2:]]
    if current_mode in MODES_NEEDING_TARGET_DATE:
        rows.append([InlineKeyboardButton(text="🗓 Задать дату", callback_data="set_target_date")])
    rows.append([InlineKeyboardButton(text=notify_label, callback_data="toggle_notify_deletions")])
    rows.append([InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="tz_menu")])
    rows.append([InlineKeyboardButton(text="🤝 Автоприветствие", callback_data="greeting_menu")])
    rows.append([InlineKeyboardButton(text="❌⭕ Игра XO", callback_data="xo_settings_menu")])
    rows.append([InlineKeyboardButton(text="📚 Помощь", callback_data="show_help")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_settings_text(current_mode: str, tz_offset: float, target_datetime: Optional[str] = None, countdown_label: Optional[str] = None) -> str:
    lines = ["⚙️ <b>Настройки</b>\n", f"Формат: <b>{NICKNAME_MODES.get(current_mode, current_mode)}</b>", f"Часовой пояс: <b>UTC{tz_offset:+g}</b>"]
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
            lines.append("Целевая дата: <b>не задана</b>")
    return "\n".join(lines)

TIMEZONE_QUICK_OFFSETS = [-5, -3, 0, 1, 2, 3, 4, 5, 5.5, 7, 8, 9]
TIMEZONE_NAME_MAP = {
    "мск": 3, "москва": 3, "moscow": 3, "msk": 3, "спб": 3,
    "калининград": 2, "kaliningrad": 2,
    "самара": 4, "samara": 4,
    "екатеринбург": 5, "ekaterinburg": 5,
    "омск": 6, "omsk": 6,
    "новосибирск": 7, "novosibirsk": 7,
    "иркутск": 8, "irkutsk": 8,
    "владивосток": 10, "vladivostok": 10,
    "лондон": 0, "london": 0, "gmt": 0, "utc": 0,
    "берлин": 1, "berlin": 1,
    "нью-йорк": -5, "new york": -5,
    "лос-анджелес": -8, "los angeles": -8,
    "дубай": 4, "dubai": 4,
    "токио": 9, "tokyo": 9,
    "пекин": 8, "beijing": 8,
    "дели": 5.5, "delhi": 5.5,
    "алматы": 6, "almaty": 6,
    "баку": 4, "baku": 4,
}

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

def build_greeting_text(enabled: bool, text: Optional[str]) -> str:
    status = "включено ✅" if enabled else "выключено ❌"
    lines = ["🤝 <b>Автоприветствие</b>\n", f"Статус: <b>{status}</b>", "Срабатывает один раз для каждого нового собеседника."]
    if text:
        lines.append(f"\nТекст:\n<i>{text}</i>")
    else:
        lines.append("\nТекст не задан.")
    return "\n".join(lines)

def build_greeting_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔕 Выключить" if enabled else "🔔 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_label, callback_data="greeting_toggle")],
        [InlineKeyboardButton(text="✏️ Задать текст", callback_data="greeting_set_text")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")],
    ])

def build_xo_settings_text(emoji_x: str, emoji_o: str) -> str:
    return (
        "❌⭕ <b>Игра «Крестики-нолики»</b>\n\n"
        f"Ваш символ: {emoji_x}\n"
        f"Символ собеседника: {emoji_o}\n\n"
        "Чтобы начать игру, напишите <code>.xo</code> в любом чате."
    )

def build_xo_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить свой символ", callback_data="xo_set_emoji_x")],
        [InlineKeyboardButton(text="✏️ Изменить символ собеседника", callback_data="xo_set_emoji_o")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")],
    ])

XO_WIN_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]

def xo_check_winner(board: str) -> Optional[str]:
    for a, b, c in XO_WIN_LINES:
        if board[a] != "." and board[a] == board[b] == board[c]:
            return board[a]
    if "." not in board:
        return "draw"
    return None

def build_xo_keyboard(db: Database, chat_id: int, board: str, emoji_x: str, emoji_o: str, finished: bool) -> InlineKeyboardMarkup:
    cells = [db.get_button(f"xo_cell_{i}") for i in range(9)]
    symbols = {".": "‌ ", "X": emoji_x, "O": emoji_o}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            cell = board[idx]
            data = "xo:noop" if finished else (f"xo:{idx}" if cell == "." else "xo:noop")
            btn = cells[idx]
            kwargs = {"text": symbols[cell]}
            if btn["style"] in BUTTON_STYLES:
                kwargs["style"] = btn["style"]
            if btn["icon_custom_emoji_id"]:
                kwargs["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
            kwargs["callback_data"] = data
            row.append(InlineKeyboardButton(**kwargs))
        rows.append(row)
    if finished:
        restart_btn = db.get_button("xo_cell_0")
        restart_kwargs = {"text": "🔁 Новая игра", "callback_data": "xo:restart"}
        if restart_btn["style"] in BUTTON_STYLES:
            restart_kwargs["style"] = restart_btn["style"]
        if restart_btn["icon_custom_emoji_id"]:
            restart_kwargs["icon_custom_emoji_id"] = restart_btn["icon_custom_emoji_id"]
        rows.append([InlineKeyboardButton(**restart_kwargs)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_xo_status_text(board: str, emoji_x: str, emoji_o: str, turn: str, owner_name: str, friend_name: str, winner: Optional[str]) -> str:
    if winner == "draw":
        return "🎮 <b>Крестики-нолики</b>\n\nНичья! 🤝"
    if winner:
        winner_name = owner_name if winner == "X" else friend_name
        winner_emoji = emoji_x if winner == "X" else emoji_o
        return f"🎮 <b>Крестики-нолики</b>\n\n{winner_emoji} Победа: {winner_name}! 🎉"
    turn_name = owner_name if turn == "owner" else friend_name
    turn_emoji = emoji_x if turn == "owner" else emoji_o
    return f"🎮 <b>Крестики-нолики</b>\n\nХодит {turn_emoji} {turn_name}"

def format_author(cached: Optional[dict], chat: User) -> str:
    if cached:
        if cached.get("username"):
            return f"@{cached['username']}"
        if cached.get("first_name"):
            return cached["first_name"]
    if chat.username:
        return f"@{chat.username}"
    return " ".join(filter(None, [chat.first_name, chat.last_name])) or "Собеседник"


# ---------------------------------------------------------------------------
# Админ-панель
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_label = State()
    waiting_emoji = State()
    waiting_grant_sub = State()
    waiting_remove_sub = State()
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="🎨 Кнопки", callback_data="admin_buttons")],
        [InlineKeyboardButton(text="📝 Тексты", callback_data="admin_texts")],
        [InlineKeyboardButton(text="✉️ Обратная связь", callback_data="admin_feedback")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_grant_sub")],
        [InlineKeyboardButton(text="🗑 Отобрать подписку", callback_data="admin_remove_sub")],
        [InlineKeyboardButton(text="💰 Цена подписки", callback_data="admin_price")],
        [InlineKeyboardButton(text="💾 Кеш сообщений", callback_data="admin_cache")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")],
    ])

def build_admin_texts_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"admin_text:{key}")] for key, label in TEXT_LABELS.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_admin_text_edit_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_settext:{key}")],
        [InlineKeyboardButton(text="↩️ Сбросить", callback_data=f"admin_resettext:{key}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_texts")],
    ])

def build_admin_buttons_keyboard(db: Database) -> InlineKeyboardMarkup:
    rows = []
    for key in list(BUTTON_KEYS.keys()) + list(XO_BUTTON_KEYS.keys()):
        try:
            btn = db.get_button(key)
            rows.append([InlineKeyboardButton(text=btn["label"], callback_data=f"admin_btn:{key}")])
        except KeyError:
            continue
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_admin_button_edit_keyboard(button_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Текст", callback_data=f"admin_setlabel:{button_key}")],
        [InlineKeyboardButton(text="🔵 Primary", callback_data=f"admin_style:{button_key}:primary"),
         InlineKeyboardButton(text="🔴 Danger", callback_data=f"admin_style:{button_key}:danger"),
         InlineKeyboardButton(text="🟢 Success", callback_data=f"admin_style:{button_key}:success")],
        [InlineKeyboardButton(text="⚪️ Сбросить цвет", callback_data=f"admin_style:{button_key}:none")],
        [InlineKeyboardButton(text="✨ Premium emoji", callback_data=f"admin_setemoji:{button_key}")],
        [InlineKeyboardButton(text="🚫 Убрать emoji", callback_data=f"admin_clearemoji:{button_key}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")],
    ])

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
        return "👥 Пользователей пока нет."
    now_iso = datetime.now(timezone.utc).isoformat()
    lines = [f"<b>👥 Пользователи (стр. {page + 1})</b>\n"]
    for u in chunk:
        uname = f"@{u['username']}" if u["username"] else "(нет username)"
        sub_mark = "✅" if (u["subscription_until"] and u["subscription_until"] > now_iso) else "❌"
        admin_mark = " 🛡" if u["is_admin"] else ""
        lines.append(f"• <code>{u['user_id']}</code> {uname}{admin_mark}, подписка: {sub_mark}")
    return "\n".join(lines)

def parse_user_id(text: str) -> Optional[int]:
    text = text.strip().lstrip("@")
    if text.isdigit():
        return int(text)
    return None

def build_admin_cache_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Очистить старые", callback_data="admin_cache_clear")],
        [InlineKeyboardButton(text="📊 Статистика кеша", callback_data="admin_cache_stats")],
        [InlineKeyboardButton(text="📅 Срок хранения", callback_data="admin_cache_days")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")],
    ])


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

def register_handlers(dp: Dispatcher, db: Database, clock: NicknameClock, bot_username: str, settings: Settings) -> None:

    def is_owner(user_id: int) -> bool:
        return user_id == settings.owner_id

    # ------------------------- Бизнес-сообщения -------------------------
    
    @dp.business_message()
    async def handle_business_message(message: Message) -> None:
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

        if not is_from_owner and db.is_muted(chat_id, message.from_user.id):
            try:
                await message.delete()
                muted_info = db.get_muted_info(chat_id, message.from_user.id)
                if muted_info:
                    unmute_text = "навсегда" if not muted_info["unmute_at"] else f"до {muted_info['unmute_at']}"
                    await message.bot.send_message(owner_id, f"🔇 Сообщение от @{message.from_user.username or message.from_user.first_name} удалено (мут {unmute_text})", business_connection_id=connection_id)
            except Exception:
                logger.exception("Failed to delete muted message")
            return

        if is_from_owner and message.text and message.text.strip().lower() == ".xo":
            friend_name = message.chat.first_name or "Собеседник"
            board = "." * 9
            db.create_xo_game(chat_id, owner_id, connection_id, board, "owner")
            emoji_x = owner_row["xo_emoji_x"] or "❌"
            emoji_o = owner_row["xo_emoji_o"] or "⭕"
            owner_name = owner_row["first_name"] or "Владелец"
            text = build_xo_status_text(board, emoji_x, emoji_o, "owner", owner_name, friend_name, None)
            kb = build_xo_keyboard(db, chat_id, board, emoji_x, emoji_o, False)
            try:
                sent = await message.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, business_connection_id=connection_id)
                db.set_xo_message_id(chat_id, sent.message_id)
            except Exception:
                logger.exception("Failed to start XO game")
                try:
                    await message.bot.send_message(owner_id, "❌ Не удалось начать игру XO. Проверьте, что боту разрешено отправлять сообщения в этом бизнес-чате (Автоматизация чатов → права).", business_connection_id=connection_id)
                except Exception:
                    logger.exception("Failed to notify owner about XO failure")
            return

        if is_from_owner and message.text and message.text.strip().lower().startswith(".mute"):
            parts = message.text.strip().split()
            if len(parts) >= 1:
                unmute_at = None
                if len(parts) >= 2:
                    time_str = " ".join(parts[1:])
                    time_match = re.match(r"(\d+)\s*(сек|с|мин|м|час|ч|день|д|дней|дня)?", time_str)
                    if time_match:
                        amount = int(time_match.group(1))
                        unit = time_match.group(2) or "сек"
                        if unit in ("сек", "с"):
                            seconds = amount
                        elif unit in ("мин", "м"):
                            seconds = amount * 60
                        elif unit in ("час", "ч"):
                            seconds = amount * 3600
                        elif unit in ("день", "д", "дней", "дня"):
                            seconds = amount * 86400
                        else:
                            seconds = amount
                        unmute_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                friend_id = message.chat.id
                db.mute_user(chat_id, friend_id, owner_id, unmute_at)
                unmute_text = "навсегда" if not unmute_at else f"до {unmute_at.strftime('%d.%m.%Y %H:%M')}"
                try:
                    await message.answer(f"🔇 {message.chat.first_name or 'Собеседник'} замьючен {unmute_text}", reply_markup=build_unmute_keyboard(db), business_connection_id=connection_id)
                except Exception:
                    logger.exception("Failed to send .mute confirmation")
                    try:
                        await message.bot.send_message(owner_id, "❌ Мут применён в базе, но не удалось отправить подтверждение в чат (нет прав отвечать в этом бизнес-чате).", business_connection_id=connection_id)
                    except Exception:
                        pass
                return

        if is_from_owner and message.text and message.text.strip().lower().startswith(".spam"):
            parts = message.text.strip().split(maxsplit=2)
            if len(parts) >= 3:
                try:
                    count = int(parts[1])
                    text_to_spam = parts[2]
                except ValueError:
                    count = None
                    text_to_spam = None
                if count is not None and 1 <= count <= 100:
                    # Не удаляем команду синхронно перед отправкой: если удаление не удастся
                    # (например, сообщение уже старое или нет прав), рассылка всё равно должна пройти.
                    try:
                        await message.delete()
                    except Exception:
                        logger.exception("Failed to delete .spam command message")
                    sent_count = 0
                    for i in range(count):
                        try:
                            await message.bot.send_message(chat_id, text_to_spam, business_connection_id=connection_id)
                            sent_count += 1
                        except Exception:
                            logger.exception("Failed to send spam message %d/%d", i + 1, count)
                            break
                        await asyncio.sleep(0.1)
                    if sent_count == 0:
                        try:
                            await message.bot.send_message(owner_id, "❌ Не удалось отправить ни одного сообщения .spam. Проверьте права бота в этом бизнес-чате.", business_connection_id=connection_id)
                        except Exception:
                            pass
                    return

        if not is_from_owner:
            with db.connect() as conn:
                existing = conn.execute("SELECT 1 FROM known_chats WHERE owner_id = ? AND chat_id = ?", (owner_id, chat_id)).fetchone()
                if not existing:
                    conn.execute("INSERT INTO known_chats (owner_id, chat_id, first_seen_at) VALUES (?, ?, ?)", (owner_id, chat_id, datetime.now(timezone.utc).isoformat()))
                    if owner_row["greeting_enabled"] and owner_row["greeting_text"]:
                        try:
                            await message.bot.send_message(chat_id, owner_row["greeting_text"], business_connection_id=connection_id)
                        except Exception:
                            logger.exception("Failed to send greeting")

    # ------------------------- XO ходы -------------------------
    
    @dp.callback_query(F.data.startswith("xo:"))
    async def handle_xo_move(callback: CallbackQuery) -> None:
        if not callback.message:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        game = db.get_xo_game(chat_id)
        if not game:
            await callback.answer("Игра не найдена")
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
            kb = build_xo_keyboard(db, chat_id, board, emoji_x, emoji_o, False)
            try:
                await callback.bot.edit_message_text(chat_id=chat_id, message_id=callback.message.message_id, text=text, parse_mode="HTML", reply_markup=kb, business_connection_id=connection_id)
                db.set_xo_message_id(chat_id, callback.message.message_id)
            except Exception:
                logger.exception("Failed to restart XO")
            await callback.answer()
            return
        if data == "noop":
            await callback.answer()
            return
        board = game["board"]
        turn = game["turn"]
        if xo_check_winner(board):
            await callback.answer("Игра завершена", show_alert=True)
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
        kb = build_xo_keyboard(db, chat_id, new_board, emoji_x, emoji_o, bool(winner))
        try:
            await callback.bot.edit_message_text(chat_id=chat_id, message_id=callback.message.message_id, text=text, parse_mode="HTML", reply_markup=kb, business_connection_id=connection_id)
        except Exception:
            logger.exception("Failed to update XO")
        await callback.answer()

    @dp.callback_query(F.data == "unmute_user")
    async def handle_unmute_user(callback: CallbackQuery) -> None:
        chat_id = callback.message.chat.id
        user_id = callback.from_user.id
        muted_info = db.get_muted_info(chat_id, user_id)
        if not muted_info:
            await callback.answer("Нет активного мута")
            return
        db.unmute_user(chat_id, user_id)
        await callback.message.edit_text("🔊 Вы размьючены")
        await callback.answer()

    @dp.callback_query(F.data == "show_help")
    async def handle_help_callback(callback: CallbackQuery) -> None:
        await callback.message.edit_text(build_help_text(db), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")]]))
        await callback.answer()

    # ------------------------- Старт и основные команды -------------------------

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
                await message.answer("🎁 <b>Пробный период 3 дня активирован!</b>", parse_mode="HTML")
        
        if not db.is_subscribed(user_id, settings.owner_id):
            await message.answer(build_subscription_required_text(db), parse_mode="HTML", reply_markup=build_pay_keyboard(db))
            return
        
        row = db.get_user(user_id)
        is_connected = bool(row and row["business_connection_id"])
        if not is_connected:
            await message.answer(build_not_connected_text(db), parse_mode="HTML", reply_markup=build_connect_keyboard(db, bot_username))
            return
        
        is_enabled = bool(row["enabled"])
        status_text = "✅ <b>Время в нике включено</b>" if is_enabled else "❌ <b>Время в нике выключено</b>"
        await message.answer(status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(db, is_enabled, is_owner(user_id)))

    @dp.message(Command("help"))
    async def handle_help_command(message: Message) -> None:
        await message.answer(build_help_text(db), parse_mode="HTML")

    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        if not is_owner(message.from_user.id):
            return
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username, float(settings.timezone_offset_hours))
        await message.answer("👑 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_main_keyboard())

    # ------------------------- Платежи -------------------------

    @dp.callback_query(F.data == "pay_subscription")
    async def handle_pay_subscription(callback: CallbackQuery) -> None:
        price = db.get_price()
        link = await callback.bot.create_invoice_link(
            title="Подписка SiaTimeBot",
            description=f"Доступ на 30 дней за {price} Stars",
            payload=SUBSCRIPTION_PAYLOAD,
            currency="XTR",
            prices=[LabeledPrice(label="Подписка на 30 дней", amount=price)],
            subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
        )
        await callback.message.answer(f"⭐ Оплатите {price} Stars:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"⭐ Оплатить {price} Stars", url=link)]]))
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        await pre_checkout_query.answer(ok=(pre_checkout_query.invoice_payload == SUBSCRIPTION_PAYLOAD))

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        if message.successful_payment.invoice_payload != SUBSCRIPTION_PAYLOAD:
            return
        db.extend_subscription(message.from_user.id, SUBSCRIPTION_PERIOD_SECONDS)
        await message.answer("✅ <b>Подписка активирована на 30 дней!</b>", parse_mode="HTML")

    # ------------------------- Подключение бизнеса -------------------------

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username, float(settings.timezone_offset_hours))
        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            if not db.is_subscribed(user_id, settings.owner_id):
                try:
                    await connection.bot.send_message(user_id, build_subscription_required_text(db), parse_mode="HTML", reply_markup=build_pay_keyboard(db))
                except Exception:
                    logger.exception("Failed to notify about subscription")
                return
            try:
                await connection.bot.send_message(user_id, build_connected_text(db), parse_mode="HTML", reply_markup=build_toggle_keyboard(db, False, is_owner(user_id)))
            except Exception:
                logger.exception("Failed to send connection confirmation")
            return
        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        db.set_connection(user_id, None)
        db.set_enabled(user_id, False)

    # ------------------------- Настройки -------------------------

    @dp.callback_query(F.data == "toggle_on")
    async def handle_toggle_on(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        if not db.is_subscribed(user_id, settings.owner_id):
            await callback.answer("Требуется подписка", show_alert=True)
            return
        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("Бот не подключён", show_alert=True)
            return
        db.set_enabled(user_id, True)
        await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", row["nickname_mode"], db.get_timezone(row, settings.timezone_offset_hours), row["target_datetime"], row["countdown_label"])
        await callback.message.edit_text("✅ <b>Время в нике включено</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, True, is_owner(user_id)))
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
        await callback.message.edit_text("❌ <b>Время в нике выключено</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, False, is_owner(user_id)))
        await callback.answer()

    @dp.callback_query(F.data == "settings_menu")
    async def handle_settings_menu(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row:
            await callback.answer()
            return
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        await callback.message.edit_text(build_settings_text(row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"]), parse_mode="HTML", reply_markup=build_settings_keyboard(row["nickname_mode"], bool(row["notify_deletions"])))
        await callback.answer()

    @dp.callback_query(F.data == "settings_back")
    async def handle_settings_back(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        is_enabled = bool(row and row["enabled"])
        status_text = "✅ <b>Время в нике включено</b>" if is_enabled else "❌ <b>Время в нике выключено</b>"
        await callback.message.edit_text(status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(db, is_enabled, is_owner(user_id)))
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
            await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", mode, tz_offset, row["target_datetime"], row["countdown_label"])
        await callback.message.edit_text(build_settings_text(mode, tz_offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None), parse_mode="HTML", reply_markup=build_settings_keyboard(mode, bool(row["notify_deletions"]) if row else True))
        await callback.answer("✅ Формат обновлён")

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
        await callback.message.edit_text(build_settings_text(row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"]), parse_mode="HTML", reply_markup=build_settings_keyboard(row["nickname_mode"], new_value))
        await callback.answer()

    @dp.callback_query(F.data == "tz_menu")
    async def handle_tz_menu(callback: CallbackQuery) -> None:
        await callback.message.edit_text("🌍 <b>Выберите часовой пояс</b>", parse_mode="HTML", reply_markup=build_timezone_keyboard())
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
            await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", row["nickname_mode"], offset, row["target_datetime"], row["countdown_label"])
        await callback.message.edit_text(build_settings_text(row["nickname_mode"] if row else "time", offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None), parse_mode="HTML", reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True))
        await callback.answer("✅ Часовой пояс обновлён")

    @dp.callback_query(F.data == "tz_manual")
    async def handle_tz_manual_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_timezone_text)
        await callback.message.answer("Введите часовой пояс (например +3, Moscow, Алматы):", parse_mode="HTML")
        await callback.answer()

    @dp.message(AdminStates.waiting_timezone_text)
    async def handle_tz_manual_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        offset = parse_timezone_input(message.text or "")
        await state.clear()
        if offset is None:
            await message.answer("❌ Не удалось распознать часовой пояс")
            return
        db.set_timezone(user_id, offset)
        row = db.get_user(user_id)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", row["nickname_mode"], offset, row["target_datetime"], row["countdown_label"])
        await message.answer(build_settings_text(row["nickname_mode"] if row else "time", offset, row["target_datetime"] if row else None, row["countdown_label"] if row else None), parse_mode="HTML", reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True))

    # ------------------------- Автоприветствие -------------------------

    @dp.callback_query(F.data == "greeting_menu")
    async def handle_greeting_menu(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        enabled = bool(row and row["greeting_enabled"])
        text = row["greeting_text"] if row else None
        await callback.message.edit_text(build_greeting_text(enabled, text), parse_mode="HTML", reply_markup=build_greeting_keyboard(enabled))
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
            await callback.answer("Сначала задайте текст", show_alert=True)
            return
        db.set_greeting(user_id, new_enabled, row["greeting_text"])
        await callback.message.edit_text(build_greeting_text(new_enabled, row["greeting_text"]), parse_mode="HTML", reply_markup=build_greeting_keyboard(new_enabled))
        await callback.answer()

    @dp.callback_query(F.data == "greeting_set_text")
    async def handle_greeting_set_text_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_greeting_text)
        await callback.message.answer("Отправьте текст приветствия:")
        await callback.answer()

    @dp.message(AdminStates.waiting_greeting_text)
    async def handle_greeting_set_text_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        text = (message.text or "").strip()
        await state.clear()
        if not text:
            await message.answer("Текст не может быть пустым")
            return
        db.set_greeting(user_id, True, text)
        await message.answer(build_greeting_text(True, text), parse_mode="HTML", reply_markup=build_greeting_keyboard(True))

    # ------------------------- XO настройки -------------------------

    @dp.callback_query(F.data == "xo_settings_menu")
    async def handle_xo_settings_menu(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        emoji_x = (row["xo_emoji_x"] if row else None) or "❌"
        emoji_o = (row["xo_emoji_o"] if row else None) or "⭕"
        await callback.message.edit_text(build_xo_settings_text(emoji_x, emoji_o), parse_mode="HTML", reply_markup=build_xo_settings_keyboard())
        await callback.answer()

    @dp.callback_query(F.data == "xo_set_emoji_x")
    async def handle_xo_set_emoji_x_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_xo_emoji_x)
        await callback.message.answer("Отправьте эмодзи для вас (например ❌, 🔥):")
        await callback.answer()

    @dp.message(AdminStates.waiting_xo_emoji_x)
    async def handle_xo_set_emoji_x_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        emoji = (message.text or "").strip()[:8]
        await state.clear()
        if not emoji:
            await message.answer("Пусто, попробуйте снова")
            return
        db.set_xo_emojis(user_id, emoji, None)
        row = db.get_user(user_id)
        emoji_o = (row["xo_emoji_o"] if row else None) or "⭕"
        await message.answer(build_xo_settings_text(emoji, emoji_o), parse_mode="HTML", reply_markup=build_xo_settings_keyboard())

    @dp.callback_query(F.data == "xo_set_emoji_o")
    async def handle_xo_set_emoji_o_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_xo_emoji_o)
        await callback.message.answer("Отправьте эмодзи для собеседника (например ⭕, 💧):")
        await callback.answer()

    @dp.message(AdminStates.waiting_xo_emoji_o)
    async def handle_xo_set_emoji_o_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        emoji = (message.text or "").strip()[:8]
        await state.clear()
        if not emoji:
            await message.answer("Пусто, попробуйте снова")
            return
        db.set_xo_emojis(user_id, None, emoji)
        row = db.get_user(user_id)
        emoji_x = (row["xo_emoji_x"] if row else None) or "❌"
        await message.answer(build_xo_settings_text(emoji_x, emoji), parse_mode="HTML", reply_markup=build_xo_settings_keyboard())

    @dp.callback_query(F.data == "set_target_date")
    async def handle_set_target_date_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminStates.waiting_target_date)
        await callback.message.answer("📅 Отправьте дату (например 31.12.2026 20:00):", parse_mode="HTML")
        await callback.answer()

    @dp.message(AdminStates.waiting_target_date)
    async def handle_set_target_date_finish(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        await state.clear()
        text = message.text.strip()
        dt = None
        label = None
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(text[:19] if len(text) > 19 else text, fmt)
                label = text[len(text[:19] if len(text) > 19 else text):].strip() if len(text) > 19 else None
                break
            except ValueError:
                continue
        if dt is None:
            await message.answer("❌ Не удалось распознать дату")
            return
        row = db.get_user(user_id)
        tz_offset = db.get_timezone(row, settings.timezone_offset_hours)
        dt_utc = (dt - timedelta(hours=tz_offset)).replace(tzinfo=timezone.utc)
        db.set_target_datetime(user_id, dt_utc.isoformat(), label)
        if row and row["enabled"] and row["business_connection_id"]:
            await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", row["nickname_mode"], tz_offset, dt_utc.isoformat(), label)
        await message.answer(build_settings_text(row["nickname_mode"] if row else "time", tz_offset, dt_utc.isoformat(), label), parse_mode="HTML", reply_markup=build_settings_keyboard(row["nickname_mode"] if row else "time", bool(row["notify_deletions"]) if row else True))

    # ------------------------- Удалённые сообщения -------------------------

    @dp.deleted_business_messages()
    async def handle_deleted_business_messages(deleted: BusinessMessagesDeleted) -> None:
        row = db.get_user_by_connection(deleted.business_connection_id)
        if not row or not row["notify_deletions"]:
            return
        chat = deleted.chat
        count = len(deleted.message_ids)
        cached_messages = db.get_cached_messages(chat.id, deleted.message_ids)
        if count == 1 and cached_messages:
            msg = cached_messages[0]
            author = format_author(msg, chat)
            text = msg.get("text", "⚠️ Текст не сохранён")
            if len(text) > 1000:
                text = text[:1000] + "..."
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            await deleted.bot.send_message(row["user_id"], db.get_text("deleted_message_text").format(author=author, text=text), parse_mode="HTML")
        elif count > 1 and cached_messages:
            author = format_author(cached_messages[0] if cached_messages else None, chat)
            messages_list = []
            for i, msg in enumerate(cached_messages[:5], 1):
                text = msg.get("text", "⚠️")
                if len(text) > 200:
                    text = text[:200] + "..."
                text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                messages_list.append(f"{i}. {text}")
            if len(cached_messages) > 5:
                messages_list.append(f"... и ещё {count - 5}")
            quoted = f"<blockquote>{chr(10).join(messages_list)}</blockquote>"
            await deleted.bot.send_message(row["user_id"], db.get_text("deleted_messages_text").format(count=count, author=author, messages=quoted), parse_mode="HTML")
        else:
            author = format_author(None, chat)
            word = "сообщение" if count == 1 else "сообщений"
            await deleted.bot.send_message(row["user_id"], f"🗑 <b>Удалено {count} {word}</b>\n👤 {author}\n<blockquote>⚠️ Текст не сохранён в кэше</blockquote>", parse_mode="HTML")

    # ------------------------- Обратная связь -------------------------

    @dp.message(Command("cancel"))
    async def handle_cancel_any(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("✅ Отменено")

    @dp.message(Command("feedback"))
    async def handle_feedback_start(message: Message, state: FSMContext) -> None:
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username, float(settings.timezone_offset_hours))
        await state.set_state(AdminStates.waiting_feedback_message)
        await message.answer("✉️ Напишите сообщение для поддержки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="feedback_cancel")]]))

    @dp.callback_query(F.data == "start_feedback")
    async def handle_feedback_button(callback: CallbackQuery, state: FSMContext) -> None:
        db.upsert_user(callback.from_user.id, callback.from_user.first_name or "", callback.from_user.username, float(settings.timezone_offset_hours))
        await state.set_state(AdminStates.waiting_feedback_message)
        await callback.message.answer("✉️ Напишите сообщение для поддержки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="feedback_cancel")]]))
        await callback.answer()

    @dp.callback_query(F.data == "feedback_cancel")
    async def handle_feedback_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text("✅ Отменено")
        await callback.answer()

    @dp.message(AdminStates.waiting_feedback_message)
    async def handle_feedback_finish(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.text:
            await message.answer("Только текст")
            return
        target_raw = db.get_setting("feedback_target_id")
        if not target_raw:
            await message.answer("Поддержка не настроена")
            return
        try:
            target_id = int(target_raw)
        except ValueError:
            await message.answer("Поддержка не настроена")
            return
        sender = message.from_user
        who = f"@{sender.username}" if sender.username else (sender.first_name or "без имени")
        await message.bot.send_message(target_id, f"📩 <b>Обратная связь</b>\nОт: {who} (<code>{sender.id}</code>)\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Сообщение отправлено")

    # ------------------------- Админ-панель -------------------------

    @dp.callback_query(F.data == "admin_home")
    async def admin_home(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text("👑 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_main_keyboard())
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
        await callback.message.edit_text(f"📊 <b>Статистика</b>\n\n👥 Всего: {total}\n💎 Подписок: {subscribed}\n🔌 Подключено: {connected}\n✅ Активно: {enabled}\n💾 Кеш: {cache_count}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]]))
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache")
    async def admin_cache_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        days = db.get_message_cache_days()
        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM cached_messages").fetchone()["c"]
        await callback.message.edit_text(f"💾 <b>Кеш сообщений</b>\n\nВсего: {total}\nСрок хранения: {days} дней", parse_mode="HTML", reply_markup=build_admin_cache_keyboard())
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_stats")
    async def admin_cache_stats(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM cached_messages").fetchone()["c"]
            by_user = conn.execute("SELECT user_id, COUNT(*) as count FROM cached_messages GROUP BY user_id ORDER BY count DESC LIMIT 10").fetchall()
        text = f"📊 <b>Кеш</b>\nВсего: {total}\n\n<b>Топ-10:</b>\n"
        for u in by_user:
            text += f"• {u['user_id']}: {u['count']}\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cache")]]))
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_days")
    async def admin_cache_days_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_cache_days)
        await callback.message.answer(f"Текущий срок: {db.get_message_cache_days()} дней\nВведите новое количество дней:")
        await callback.answer()

    @dp.message(AdminStates.waiting_cache_days)
    async def admin_cache_days_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        try:
            days = int(message.text.strip())
            if days < 1 or days > 365:
                await message.answer("От 1 до 365 дней")
                return
            db.set_message_cache_days(days)
            await message.answer(f"✅ Срок хранения: {days} дней")
        except ValueError:
            await message.answer("Введите число")

    @dp.callback_query(F.data == "admin_cache_clear")
    async def admin_cache_clear_start(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        days = db.get_message_cache_days()
        await callback.message.answer(f"Удалить сообщения старше {days} дней?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Да", callback_data="admin_cache_clear_confirm"), InlineKeyboardButton(text="❌ Нет", callback_data="admin_cache")]]))
        await callback.answer()

    @dp.callback_query(F.data == "admin_cache_clear_confirm")
    async def admin_cache_clear_confirm(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        days = db.get_message_cache_days()
        deleted = db.clear_old_messages(days)
        await callback.message.edit_text(f"✅ Удалено {deleted} сообщений", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cache")]]))
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        page = int(callback.data.split(":")[1])
        users = db.get_all_users()
        await callback.message.edit_text(format_users_page(users, page), parse_mode="HTML", reply_markup=build_admin_users_keyboard(users, page))
        await callback.answer()

    @dp.callback_query(F.data == "admin_texts")
    async def admin_texts_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text("📝 <b>Тексты бота</b>", parse_mode="HTML", reply_markup=build_admin_texts_keyboard())
        await callback.answer()

    def _text_preview(key: str) -> str:
        return f"<b>{TEXT_LABELS.get(key, key)}</b>\n\n{db.get_text(key)}"

    @dp.callback_query(F.data.startswith("admin_text:"))
    async def admin_text_view(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.edit_text(_text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key))
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_settext:"))
    async def admin_settext_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(text_key=key)
        await state.set_state(AdminStates.waiting_text_edit)
        await callback.message.answer("Отправьте новый текст (можно с HTML):")
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
        await message.answer("✅ Текст обновлён")
        await message.answer(_text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key))

    @dp.callback_query(F.data.startswith("admin_resettext:"))
    async def admin_resettext(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        db.set_setting(key, TEXT_KEYS.get(key))
        await callback.answer("✅ Сброшено")
        await callback.message.edit_text(_text_preview(key), parse_mode="HTML", reply_markup=build_admin_text_edit_keyboard(key))

    @dp.callback_query(F.data == "admin_feedback")
    async def admin_feedback_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        target_id = db.get_setting("feedback_target_id")
        desc = f"user_id <code>{target_id}</code>" if target_id else "не настроен"
        await callback.message.edit_text(f"✉️ <b>Обратная связь</b>\n\nПолучатель: {desc}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Изменить", callback_data="admin_set_feedback_target")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]]))
        await callback.answer()

    @dp.callback_query(F.data == "admin_set_feedback_target")
    async def admin_set_feedback_target_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_feedback_target)
        await callback.message.answer("Отправьте @username или user_id:")
        await callback.answer()

    @dp.message(AdminStates.waiting_feedback_target)
    async def admin_set_feedback_target_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        raw = message.text.strip()
        if raw.startswith("@"):
            username = raw[1:]
            user_row = db.get_user_by_username(username)
            if user_row:
                db.set_setting("feedback_target_id", str(user_row["user_id"]))
                await message.answer(f"✅ Получатель: @{username}")
            else:
                await message.answer(f"❌ Пользователь @{username} не найден")
        else:
            target_id = parse_user_id(raw)
            if target_id:
                db.set_setting("feedback_target_id", str(target_id))
                await message.answer(f"✅ Получатель: <code>{target_id}</code>", parse_mode="HTML")
            else:
                await message.answer("❌ Неверный формат")

    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text("🎨 <b>Настройка кнопок</b>", parse_mode="HTML", reply_markup=build_admin_buttons_keyboard(db))
        await callback.answer()

    def _button_edit_text(key: str) -> str:
        row = db.get_button(key)
        return f"<b>Кнопка:</b> {key}\n<b>Текст:</b> {row['label']}\n<b>Цвет:</b> {row['style'] or 'по умолчанию'}\n<b>Emoji:</b> {row['icon_custom_emoji_id'] or 'нет'}"

    @dp.callback_query(F.data.startswith("admin_btn:"))
    async def admin_btn_edit(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.edit_text(_button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key))
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_style:"))
    async def admin_set_style(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        _, key, style = callback.data.split(":")
        db.set_button_style(key, None if style == "none" else style)
        await callback.answer("✅ Цвет обновлён")
        await callback.message.edit_text(_button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key))

    @dp.callback_query(F.data.startswith("admin_clearemoji:"))
    async def admin_clear_emoji(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        db.set_button_emoji(key, None)
        await callback.answer("✅ Emoji убран")
        await callback.message.edit_text(_button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key))

    @dp.callback_query(F.data.startswith("admin_setlabel:"))
    async def admin_set_label_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_label)
        await callback.message.answer(f"Новый текст для кнопки «{key}»:")
        await callback.answer()

    @dp.message(AdminStates.waiting_label)
    async def admin_set_label_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        data = await state.get_data()
        key = data.get("button_key")
        await state.clear()
        if not key:
            return
        db.set_button_label(key, message.text.strip())
        await message.answer(f"✅ Текст кнопки «{key}» обновлён")
        await message.answer(_button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key))

    @dp.callback_query(F.data.startswith("admin_setemoji:"))
    async def admin_set_emoji_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji)
        await callback.message.answer("Отправьте premium-эмодзи (или его ID):")
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji)
    async def admin_set_emoji_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        data = await state.get_data()
        key = data.get("button_key")
        await state.clear()
        if not key:
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
            await message.answer("❌ Не найден premium-эмодзи")
            return
        db.set_button_emoji(key, emoji_id)
        await message.answer(f"✅ Premium emoji для «{key}» установлен")
        await message.answer(_button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key))

    @dp.callback_query(F.data == "admin_grant_sub")
    async def admin_grant_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_grant_sub)
        await callback.message.answer("🎁 Отправьте @username или user_id для выдачи подписки:")
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_sub)
    async def admin_grant_sub_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        raw = message.text.strip()
        if raw.startswith("@"):
            username = raw[1:]
            user_row = db.get_user_by_username(username)
            if user_row:
                target_id = user_row["user_id"]
            else:
                await message.answer(f"❌ Пользователь @{username} не найден")
                return
        else:
            target_id = parse_user_id(raw)
            if target_id is None:
                await message.answer("❌ Неверный формат")
                return
        db.extend_subscription(target_id, SUBSCRIPTION_PERIOD_SECONDS)
        await message.answer(f"✅ Подписка выдана пользователю <code>{target_id}</code>", parse_mode="HTML")
        try:
            await message.bot.send_message(target_id, "🎁 Вам выдана подписка на 30 дней!")
        except Exception:
            pass

    @dp.callback_query(F.data == "admin_remove_sub")
    async def admin_remove_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_remove_sub)
        await callback.message.answer("🗑 Отправьте @username или user_id для отбора подписки:")
        await callback.answer()

    @dp.message(AdminStates.waiting_remove_sub)
    async def admin_remove_sub_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        raw = message.text.strip()
        if raw.startswith("@"):
            username = raw[1:]
            user_row = db.get_user_by_username(username)
            if user_row:
                target_id = user_row["user_id"]
            else:
                await message.answer(f"❌ Пользователь @{username} не найден")
                return
        else:
            target_id = parse_user_id(raw)
            if target_id is None:
                await message.answer("❌ Неверный формат")
                return
        db.remove_subscription(target_id)
        await message.answer(f"✅ Подписка отобрана у пользователя <code>{target_id}</code>", parse_mode="HTML")
        try:
            await message.bot.send_message(target_id, "❌ Ваша подписка отобрана администратором.")
        except Exception:
            pass

    @dp.callback_query(F.data == "admin_price")
    async def admin_price_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_price_edit)
        await callback.message.edit_text(f"💰 <b>Цена подписки</b>\n\nТекущая: {db.get_price()} ⭐\n\nВведите новую цену:", parse_mode="HTML")
        await callback.answer()

    @dp.message(AdminStates.waiting_price_edit)
    async def admin_price_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        await state.clear()
        try:
            price = int(message.text.strip())
            if price <= 0:
                await message.answer("Цена должна быть > 0")
                return
            db.set_price(price)
            await message.answer(f"✅ Цена: {price} ⭐")
        except ValueError:
            await message.answer("Введите число")

    # ------------------------- Обычные сообщения (точка-команды) -------------------------
    # ВАЖНО: этот хендлер зарегистрирован ПОСЛЕДНИМ и не имеет фильтра,
    # поэтому он должен идти строго после всех Command(...) и FSM-хендлеров,
    # иначе он перехватывает вообще все сообщения раньше них.

    @dp.message()
    async def handle_private_message(message: Message) -> None:
        """Обработка .команд в обычных личных чатах"""
        if not message.text:
            return

        # Пропускаем команды с / (их уже обработали выше, если не дошли сюда — не наши)
        if message.text.startswith("/"):
            return

        user_id = message.from_user.id
        chat_id = message.chat.id

        # Только личные чаты
        if message.chat.type != "private":
            return

        # Проверка подписки
        if not db.is_subscribed(user_id, settings.owner_id):
            await message.answer(
                build_subscription_required_text(db),
                parse_mode="HTML",
                reply_markup=build_pay_keyboard(db),
            )
            return

        text = message.text.strip().lower()

        # .help
        if text == ".help":
            await message.answer(build_help_text(db), parse_mode="HTML")
            return

        # .mute
        if text.startswith(".mute"):
            parts = message.text.strip().split()
            if len(parts) >= 1:
                unmute_at = None
                if len(parts) >= 2:
                    time_str = " ".join(parts[1:])
                    time_match = re.match(r"(\d+)\s*(сек|с|мин|м|час|ч|день|д|дней|дня)?", time_str)
                    if time_match:
                        amount = int(time_match.group(1))
                        unit = time_match.group(2) or "сек"
                        if unit in ("сек", "с"):
                            seconds = amount
                        elif unit in ("мин", "м"):
                            seconds = amount * 60
                        elif unit in ("час", "ч"):
                            seconds = amount * 3600
                        elif unit in ("день", "д", "дней", "дня"):
                            seconds = amount * 86400
                        else:
                            seconds = amount
                        unmute_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                db.mute_user(chat_id, user_id, user_id, unmute_at)
                unmute_text = "навсегда" if not unmute_at else f"до {unmute_at.strftime('%d.%m.%Y %H:%M')}"
                await message.answer(f"🔇 Вы замьючены {unmute_text}", reply_markup=build_unmute_keyboard(db))
                return

        # .spam
        if text.startswith(".spam"):
            parts = message.text.strip().split(maxsplit=2)
            if len(parts) >= 3:
                try:
                    count = int(parts[1])
                    text_to_spam = parts[2]
                    if 1 <= count <= 100:
                        await message.delete()
                        for i in range(count):
                            await message.answer(text_to_spam)
                            await asyncio.sleep(0.1)
                        return
                except ValueError:
                    pass

        # .unmute
        if text == ".unmute":
            db.unmute_user(chat_id, user_id)
            await message.answer("🔊 Вы размьючены")
            return

        # .xo
        if text == ".xo":
            await message.answer("🎮 Для игры в XO используйте бота в бизнес-чате.\nПодключите через настройки Telegram → Автоматизация чатов.")
            return


# ---------------------------------------------------------------------------
# Обновление ников
# ---------------------------------------------------------------------------

def seconds_until_next_minute() -> float:
    now = datetime.now(timezone.utc)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05

async def run_update_loop(bot: Bot, db: Database, clock: NicknameClock, default_tz: float, owner_id: int) -> None:
    last_cache_cleanup = datetime.now(timezone.utc)
    while True:
        await asyncio.sleep(seconds_until_next_minute())
        try:
            for row in db.get_enabled_users():
                user_id = row["user_id"]
                try:
                    if not db.is_subscribed(user_id, owner_id):
                        db.set_enabled(user_id, False)
                        await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                        continue
                    tz_offset = db.get_timezone(row, default_tz)
                    await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "", row["nickname_mode"], tz_offset, row["target_datetime"], row["countdown_label"])
                except Exception:
                    logger.exception("Failed to update nickname for user_id=%s", user_id)
        except Exception:
            logger.exception("Failed in update loop")
        try:
            for row in db.get_users_needing_expiry_warning(EXPIRY_WARNING_SECONDS):
                db.mark_expiry_notified(row["user_id"])
                try:
                    await bot.send_message(row["user_id"], "⏳ Подписка заканчивается через 2 дня! Продлите её.")
                except Exception:
                    pass
        except Exception:
            logger.exception("Failed to send expiry warnings")
        now = datetime.now(timezone.utc)
        if (now - last_cache_cleanup).total_seconds() > 86400:
            try:
                days = db.get_message_cache_days()
                deleted = db.clear_old_messages(days)
                if deleted > 0:
                    logger.info("Cleaned up %d old messages", deleted)
                last_cache_cleanup = now
            except Exception:
                logger.exception("Failed to clean cache")

async def run_update_loop_supervised(bot: Bot, db: Database, clock: NicknameClock, default_tz: float, owner_id: int) -> None:
    while True:
        try:
            await run_update_loop(bot, db, clock, default_tz, owner_id)
        except Exception:
            logger.exception("Update loop crashed, restarting")
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    settings = Settings.from_env()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    db = Database(settings.db_path)
    db.init_schema()
    db.upsert_user(settings.owner_id, "", None, float(settings.timezone_offset_hours))
    db.set_admin(settings.owner_id, True)
    clock = NicknameClock(bot, db, float(settings.timezone_offset_hours))

    # ВАЖНО: сбрасываем вебхук и все "зависшие" апдейты перед стартом polling.
    # Это устраняет TelegramConflictError ("terminated by other getUpdates request"),
    # который возникает, если у бота остался активный webhook, либо если предыдущий
    # процесс (например, старый деплой на Railway) ещё не успел отключиться.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        logger.exception("Failed to delete webhook before polling")

    me = await bot.get_me()
    
    # ТОЛЬКО ЭТИ КОМАНДЫ ВИДНЫ ПОЛЬЗОВАТЕЛЯМ
    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="help", description="📚 Помощь"),
        BotCommand(command="feedback", description="✉️ Поддержка"),
        BotCommand(command="cancel", description="❌ Отмена"),
        # /admin НЕ ДОБАВЛЯЕМ - его никто не видит!
    ])
    
    register_handlers(dp, db, clock, me.username, settings)
    asyncio.create_task(run_update_loop_supervised(bot, db, clock, float(settings.timezone_offset_hours), settings.owner_id))
    while True:
        try:
            # drop_pending_updates=True здесь же — на случай, если вебхук отсутствовал,
            # но в очереди Telegram остались "зависшие" апдейты от предыдущего инстанса.
            await dp.start_polling(bot, drop_pending_updates=True)
        except TelegramConflictError:
            # Другой процесс (старый деплой / дублирующийся инстанс) ещё держит getUpdates.
            # Ждём дольше, чтобы он успел завершиться, и пробуем снова.
            logger.warning("TelegramConflictError: другой процесс уже опрашивает Telegram. Повтор через 15 сек.")
            await asyncio.sleep(15)
        except Exception:
            logger.exception("Polling crashed, restarting")
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
            logging.exception("main() crashed, restarting")
            import time as _time
            _time.sleep(5)