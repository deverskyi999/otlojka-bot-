from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import json
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, List, Dict, Any, Union
import re

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SetBusinessAccountName, SetBusinessAccountUsername
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ChatPermissions,
    User,
    Chat,
    ChatMember,
    ChatMemberUpdated,
    BusinessMessagesDeleted,
    BusinessMessage,
    KeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButtonPollType,
    WebAppInfo,
    LoginUrl,
    SwitchInlineQueryChosenChat,
    CallbackGame,
)
from aiogram.types.input_file import InputFile
from aiogram.types.input_media import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from aiogram.types.input_sticker import InputSticker
from aiogram.types.emoji import Emoji
from aiogram.types.reaction_type import ReactionTypeEmoji, ReactionTypeCustomEmoji
from aiogram.types.chat_boost import ChatBoost, ChatBoostSource
from aiogram.types.chat_member import ChatMemberAdministrator, ChatMemberOwner
from aiogram.types.chat_permissions import ChatPermissions
from aiogram.types.paid_media import PaidMediaInfo, PaidMedia
from aiogram.types.story import Story
from aiogram.types.giveaway import Giveaway, GiveawayWinners
from aiogram.types.giveaway_completed import GiveawayCompleted
from aiogram.types.giveaway_created import GiveawayCreated
from aiogram.types.inaccessible_message import InaccessibleMessage
from aiogram.types.message_origin import MessageOrigin
from aiogram.types.message_entity import MessageEntity
from aiogram.types.passport_data import PassportData
from aiogram.types.proximity_alert_triggered import ProximityAlertTriggered
from aiogram.types.video_chat_ended import VideoChatEnded
from aiogram.types.video_chat_participants_invited import VideoChatParticipantsInvited
from aiogram.types.video_chat_scheduled import VideoChatScheduled
from aiogram.types.web_app_data import WebAppData
from aiogram.types.write_access_allowed import WriteAccessAllowed
from aiogram.types.birthdate import Birthdate
from aiogram.types.personal_details import PersonalDetails
from aiogram.types.encrypted_credentials import EncryptedCredentials
from aiogram.types.encrypted_passport_element import EncryptedPassportElement
from aiogram.types.passport_file import PassportFile
from aiogram.types.secure_data import SecureData
from aiogram.types.secure_value import SecureValue
from aiogram.types.document import Document
from aiogram.types.animation import Animation
from aiogram.types.audio import Audio
from aiogram.types.voice import Voice
from aiogram.types.video import Video
from aiogram.types.video_note import VideoNote
from aiogram.types.contact import Contact
from aiogram.types.location import Location
from aiogram.types.venue import Venue
from aiogram.types.poll import Poll
from aiogram.types.poll_answer import PollAnswer
from aiogram.types.dice import Dice
from aiogram.types.game_high_score import GameHighScore
from aiogram.types.invoice import Invoice
from aiogram.types.order_info import OrderInfo
from aiogram.types.shipping_address import ShippingAddress
from aiogram.types.shipping_query import ShippingQuery
from aiogram.types.shipping_option import ShippingOption
from aiogram.types.chat_boost_updated import ChatBoostUpdated
from aiogram.types.chat_boost_removed import ChatBoostRemoved
from aiogram.types.chat_member_updated import ChatMemberUpdated
from aiogram.types.background_type import BackgroundType
from aiogram.types.background_fill import BackgroundFill
from aiogram.types.background_fill_freeform_gradient import BackgroundFillFreeformGradient
from aiogram.types.background_fill_gradient import BackgroundFillGradient
from aiogram.types.background_fill_solid import BackgroundFillSolid
from aiogram.types.background_type_chat_theme import BackgroundTypeChatTheme
from aiogram.types.background_type_fill import BackgroundTypeFill
from aiogram.types.background_type_pattern import BackgroundTypePattern
from aiogram.types.background_type_wallpaper import BackgroundTypeWallpaper
from aiogram.types.chat_administrator_rights import ChatAdministratorRights
from aiogram.types.chat_background import ChatBackground
from aiogram.types.chat_full_info import ChatFullInfo
from aiogram.types.chat_invite_link import ChatInviteLink
from aiogram.types.chat_location import ChatLocation
from aiogram.types.chat_photo import ChatPhoto
from aiogram.types.chat_shared import ChatShared
from aiogram.types.chat_boost_source import ChatBoostSourceGiveaway, ChatBoostSourcePremium
from aiogram.types.chat_boost_source_giveaway import ChatBoostSourceGiveaway as ChatBoostSourceGiveawayType
from aiogram.types.chat_boost_source_premium import ChatBoostSourcePremium as ChatBoostSourcePremiumType
from aiogram.types.chat_boost_source_premium import ChatBoostSourcePremium as ChatBoostSourcePremiumType2
from aiogram.types.chat_boost_source_premium import ChatBoostSourcePremium as ChatBoostSourcePremiumType3
from aiogram.types.chat_boost_source_premium import ChatBoostSourcePremium as ChatBoostSourcePremiumType4
from aiogram.types.chat_boost_source_premium import ChatBoostSourcePremium as ChatBoostSourcePremiumType5

from dotenv import load_dotenv

# ======================================================================================
# ЛОГГЕР
# ======================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("TimeNick")

# ======================================================================================
# КОНСТАНТЫ
# ======================================================================================

SUBSCRIPTION_STARS = 25
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
SUBSCRIPTION_PAYLOAD = "timenick_subscription"

BUTTON_STYLES = ("primary", "danger", "success")
BUTTON_STYLE_NAMES = {
    "primary": "🔵 Синий (Primary)",
    "danger": "🔴 Красный (Danger)", 
    "success": "🟢 Зеленый (Success)",
    None: "⚪️ По умолчанию"
}

EMOJI_PREMIUM = "⭐️"  # Можно заменить на ваш premium emoji

VERSION = "2.0.0"
BOT_NAME = "TimeNick ⏰"

# ======================================================================================
# НАСТРОЙКИ
# ======================================================================================

@dataclass
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3
    auto_subscribe_admins: bool = True
    subscription_days_admin: int = 365

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        
        bot_token = os.environ.get("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("❌ BOT_TOKEN не найден! Добавьте в переменные окружения.")
        
        admin_ids_raw = os.environ.get("ADMIN_IDS", "")
        admin_ids = tuple(
            int(x.strip()) for x in admin_ids_raw.replace(" ", "").split(",") if x.strip()
        )
        
        if not admin_ids:
            logger.warning("⚠️ ADMIN_IDS не установлен! Админ-панель недоступна.")
        
        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
            db_path=os.environ.get("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.environ.get("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
            auto_subscribe_admins=os.environ.get("AUTO_SUBSCRIBE_ADMINS", "true").lower() == "true",
            subscription_days_admin=int(
                os.environ.get("SUBSCRIPTION_DAYS_ADMIN", cls.subscription_days_admin)
            ),
        )

# ======================================================================================
# БАЗА ДАННЫХ - РАСШИРЕННАЯ
# ======================================================================================

class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA cache_size=-20000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            # ===== ПОЛЬЗОВАТЕЛИ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT DEFAULT '',
                    username TEXT,
                    phone_number TEXT,
                    language_code TEXT,
                    is_premium INTEGER DEFAULT 0,
                    
                    business_connection_id TEXT,
                    business_connection_date TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    
                    started_at TEXT,
                    subscription_until TEXT,
                    subscription_auto_renew INTEGER DEFAULT 0,
                    
                    is_muted INTEGER NOT NULL DEFAULT 0,
                    is_banned INTEGER DEFAULT 0,
                    warning_count INTEGER DEFAULT 0,
                    
                    settings JSON,
                    last_seen TEXT
                )
            """)
            
            # ===== МУТЫ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS muted_users (
                    muter_id INTEGER,
                    muted_id INTEGER,
                    muted_at TEXT,
                    reason TEXT,
                    duration INTEGER,
                    PRIMARY KEY (muter_id, muted_id)
                )
            """)
            
            # ===== КНОПКИ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS buttons (
                    button_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    style TEXT,
                    icon_custom_emoji_id TEXT,
                    emoji_prefix TEXT,
                    emoji_suffix TEXT,
                    is_visible INTEGER NOT NULL DEFAULT 1,
                    row_order INTEGER DEFAULT 0,
                    callback_data TEXT,
                    url TEXT,
                    copy_text TEXT,
                    web_app_url TEXT,
                    switch_inline_query TEXT,
                    switch_inline_query_current_chat TEXT,
                    login_url TEXT,
                    game_id TEXT,
                    pay INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # ===== СТАТИСТИКА =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    stat_key TEXT PRIMARY KEY,
                    stat_value TEXT,
                    updated_at TEXT
                )
            """)
            
            # ===== ЛОГИ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_time TEXT,
                    log_level TEXT,
                    log_message TEXT,
                    user_id INTEGER,
                    action_type TEXT,
                    details JSON
                )
            """)
            
            # ===== ПОДПИСКИ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    plan_type TEXT,
                    days INTEGER,
                    price_stars INTEGER,
                    started_at TEXT,
                    expires_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    UNIQUE(user_id, plan_type)
                )
            """)
            
            # ===== ПЛАТЕЖИ =====
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    currency TEXT,
                    status TEXT,
                    telegram_payment_id TEXT,
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            
            # Добавляем недостающие колонки
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            
            new_cols = {
                "last_name": "TEXT DEFAULT ''",
                "phone_number": "TEXT",
                "language_code": "TEXT",
                "is_premium": "INTEGER DEFAULT 0",
                "business_connection_date": "TEXT",
                "subscription_auto_renew": "INTEGER DEFAULT 0",
                "is_banned": "INTEGER DEFAULT 0",
                "warning_count": "INTEGER DEFAULT 0",
                "settings": "JSON",
                "last_seen": "TEXT"
            }
            
            for col, col_type in new_cols.items():
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            
            # ===== ИНИЦИАЛИЗАЦИЯ КНОПОК - ВСЕ ЗЕЛЕНЫЕ! =====
            default_buttons = {
                # === ПОЛЬЗОВАТЕЛЬСКИЕ ===
                "toggle_on": {
                    "label": "🟢 Включить время в нике",
                    "style": "success",
                    "callback_data": "toggle_on",
                    "row_order": 1
                },
                "toggle_off": {
                    "label": "🔴 Выключить время в нике",
                    "style": "danger",
                    "callback_data": "toggle_off",
                    "row_order": 1
                },
                "connect": {
                    "label": "🔗 Подключить бизнес-бота",
                    "style": "success",
                    "callback_data": "connect_business",
                    "row_order": 2
                },
                "copy_username": {
                    "label": "📋 Скопировать юзернейм",
                    "style": "success",
                    "callback_data": None,
                    "copy_text": f"@{BOT_NAME.lower().replace(' ', '')}",
                    "row_order": 2
                },
                "pay_subscription_btn": {
                    "label": f"⭐ Оплатить подписку ({SUBSCRIPTION_STARS} ⭐)",
                    "style": "success",
                    "callback_data": "pay_subscription",
                    "row_order": 3
                },
                "my_subscription": {
                    "label": "📊 Моя подписка",
                    "style": "success",
                    "callback_data": "my_subscription",
                    "row_order": 3
                },
                "mute_help_btn": {
                    "label": "🔇 Помощь по муту",
                    "style": "success",
                    "callback_data": "mute_help",
                    "row_order": 4
                },
                "unmute_btn": {
                    "label": "🔊 Размутить меня",
                    "style": "success",
                    "callback_data": "unmute_me",
                    "row_order": 4
                },
                "support_btn": {
                    "label": "🆘 Поддержка",
                    "style": "success",
                    "callback_data": "support",
                    "row_order": 5
                },
                "about_btn": {
                    "label": "ℹ️ О боте",
                    "style": "success",
                    "callback_data": "about",
                    "row_order": 5
                },
                
                # === АДМИНСКИЕ ===
                "admin_stats_btn": {
                    "label": "📊 Статистика",
                    "style": "success",
                    "callback_data": "admin_stats",
                    "row_order": 10
                },
                "admin_users_btn": {
                    "label": "👥 Управление пользователями",
                    "style": "success",
                    "callback_data": "admin_users",
                    "row_order": 11
                },
                "admin_buttons_btn": {
                    "label": "🎨 Управление кнопками",
                    "style": "success",
                    "callback_data": "admin_buttons",
                    "row_order": 12
                },
                "admin_give_sub_btn": {
                    "label": "⭐ Выдать подписку",
                    "style": "success",
                    "callback_data": "admin_give_sub",
                    "row_order": 13
                },
                "admin_mutes_btn": {
                    "label": "🔇 Управление мутами",
                    "style": "success",
                    "callback_data": "admin_mutes",
                    "row_order": 14
                },
                "admin_settings_btn": {
                    "label": "⚙️ Настройки бота",
                    "style": "success",
                    "callback_data": "admin_settings",
                    "row_order": 15
                },
                "admin_logs_btn": {
                    "label": "📋 Логи",
                    "style": "success",
                    "callback_data": "admin_logs",
                    "row_order": 16
                },
                "admin_back_btn": {
                    "label": "⬅️ Назад в админ-панель",
                    "style": "success",
                    "callback_data": "admin_home",
                    "row_order": 99
                },
                "admin_refresh_btn": {
                    "label": "🔄 Обновить",
                    "style": "success",
                    "callback_data": "admin_refresh",
                    "row_order": 98
                }
            }
            
            for key, config in default_buttons.items():
                conn.execute("""
                    INSERT OR IGNORE INTO buttons 
                    (button_key, label, style, callback_data, row_order, is_visible, copy_text, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    key,
                    config["label"],
                    config["style"],
                    config.get("callback_data"),
                    config.get("row_order", 0),
                    config.get("copy_text"),
                    datetime.now(timezone.utc).isoformat()
                ))
            
            # Обновляем существующие кнопки до зеленого цвета
            for key, config in default_buttons.items():
                conn.execute("""
                    UPDATE buttons SET 
                        style = ?,
                        label = ?,
                        row_order = ?,
                        copy_text = ?
                    WHERE button_key = ?
                """, (
                    config["style"],
                    config["label"],
                    config.get("row_order", 0),
                    config.get("copy_text"),
                    key
                ))
            
            # ===== ИНИЦИАЛИЗАЦИЯ СТАТИСТИКИ =====
            stats_defaults = {
                "total_mutes": "0",
                "total_unmutes": "0",
                "total_payments": "0",
                "total_stars_spent": "0",
                "bot_started_at": datetime.now(timezone.utc).isoformat()
            }
            
            for key, value in stats_defaults.items():
                conn.execute("""
                    INSERT OR IGNORE INTO stats (stat_key, stat_value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value, datetime.now(timezone.utc).isoformat()))

    # ==================================================================================
    # ПОЛЬЗОВАТЕЛИ - РАСШИРЕННЫЕ МЕТОДЫ
    # ==================================================================================

    def upsert_user(self, user_id: int, first_name: str, username: Optional[str] = None,
                   last_name: Optional[str] = None, language_code: Optional[str] = None,
                   is_premium: bool = False) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO users (
                    user_id, first_name, last_name, username, 
                    language_code, is_premium, started_at, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    username = excluded.username,
                    language_code = excluded.language_code,
                    is_premium = excluded.is_premium,
                    last_seen = excluded.last_seen
            """, (
                user_id,
                first_name,
                last_name or "",
                username,
                language_code,
                1 if is_premium else 0,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            ))

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def get_all_users(self, limit: int = 1000, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM users 
                ORDER BY started_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def get_active_users(self, days: int = 7) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM users 
                WHERE last_seen IS NOT NULL AND last_seen > ?
                ORDER BY last_seen DESC
            """, (cutoff,)).fetchall()

    def update_last_seen(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE users SET last_seen = ? WHERE user_id = ?
            """, (datetime.now(timezone.utc).isoformat(), user_id))

    # ==================================================================================
    # ПОДПИСКИ - РАСШИРЕННЫЕ
    # ==================================================================================

    def set_business_connection(self, user_id: int, connection_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE users SET 
                    business_connection_id = ?,
                    business_connection_date = ?
                WHERE user_id = ?
            """, (
                connection_id,
                datetime.now(timezone.utc).isoformat() if connection_id else None,
                user_id
            ))

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET enabled = ? WHERE user_id = ?", 
                        (int(enabled), user_id))

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT user_id, first_name, business_connection_id
                FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
            """).fetchall()

    # ==================================================================================
    # ПОДПИСКА
    # ==================================================================================

    def get_subscription(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM subscriptions 
                WHERE user_id = ? AND is_active = 1
                ORDER BY expires_at DESC LIMIT 1
            """, (user_id,)).fetchone()

    def set_subscription(self, user_id: int, days: int, plan_type: str = "premium",
                        price_stars: int = SUBSCRIPTION_STARS) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=days)
        
        with self.connect() as conn:
            # Деактивируем старые подписки
            conn.execute("""
                UPDATE subscriptions SET is_active = 0 
                WHERE user_id = ? AND plan_type = ?
            """, (user_id, plan_type))
            
            # Создаем новую
            conn.execute("""
                INSERT INTO subscriptions 
                (user_id, plan_type, days, price_stars, started_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (user_id, plan_type, days, price_stars, now.isoformat(), expires_at.isoformat()))
            
            # Обновляем пользователя
            conn.execute("""
                UPDATE users SET 
                    subscription_until = ?,
                    subscription_auto_renew = 1
                WHERE user_id = ?
            """, (expires_at.isoformat(), user_id))

    def is_subscribed(self, user_id: int) -> bool:
        sub = self.get_subscription(user_id)
        if not sub:
            return False
        try:
            expires_at = datetime.fromisoformat(sub["expires_at"])
            return expires_at > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return False

    def extend_subscription(self, user_id: int, days: int) -> None:
        sub = self.get_subscription(user_id)
        now = datetime.now(timezone.utc)
        
        if sub:
            try:
                current_expires = datetime.fromisoformat(sub["expires_at"])
                new_expires = max(current_expires, now) + timedelta(days=days)
            except (ValueError, TypeError):
                new_expires = now + timedelta(days=days)
        else:
            new_expires = now + timedelta(days=days)
        
        with self.connect() as conn:
            if sub:
                conn.execute("""
                    UPDATE subscriptions SET 
                        expires_at = ?,
                        days = days + ?
                    WHERE sub_id = ?
                """, (new_expires.isoformat(), days, sub["sub_id"]))
            else:
                conn.execute("""
                    INSERT INTO subscriptions 
                    (user_id, plan_type, days, price_stars, started_at, expires_at, is_active)
                    VALUES (?, 'premium', ?, ?, ?, ?, 1)
                """, (user_id, days, SUBSCRIPTION_STARS, now.isoformat(), new_expires.isoformat()))
            
            conn.execute("""
                UPDATE users SET subscription_until = ? WHERE user_id = ?
            """, (new_expires.isoformat(), user_id))

    def get_subscription_info(self, user_id: int) -> Dict[str, Any]:
        sub = self.get_subscription(user_id)
        if not sub:
            return {"has_subscription": False}
        
        expires_at = datetime.fromisoformat(sub["expires_at"])
        now = datetime.now(timezone.utc)
        days_left = (expires_at - now).days
        
        return {
            "has_subscription": True,
            "active": expires_at > now,
            "expires_at": expires_at,
            "days_left": days_left,
            "plan_type": sub["plan_type"],
            "days_total": sub["days"],
            "price_stars": sub["price_stars"]
        }

    def count_active_subscribers(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            return conn.execute("""
                SELECT COUNT(*) AS c FROM users 
                WHERE subscription_until IS NOT NULL AND subscription_until > ?
            """, (now,)).fetchone()["c"]

    def count_connected(self) -> int:
        with self.connect() as conn:
            return conn.execute("""
                SELECT COUNT(*) AS c FROM users 
                WHERE business_connection_id IS NOT NULL
            """).fetchone()["c"]

    def count_enabled(self) -> int:
        with self.connect() as conn:
            return conn.execute("""
                SELECT COUNT(*) AS c FROM users WHERE enabled = 1
            """).fetchone()["c"]

    # ==================================================================================
    # МУТЫ - РАСШИРЕННЫЕ
    # ==================================================================================

    def mute_user(self, muter_id: int, muted_id: int, reason: str = "", duration: int = 0) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO muted_users (muter_id, muted_id, muted_at, reason, duration)
                VALUES (?, ?, ?, ?, ?)
            """, (muter_id, muted_id, datetime.now(timezone.utc).isoformat(), reason, duration))
            conn.execute("UPDATE users SET is_muted = 1 WHERE user_id = ?", (muted_id,))
            
            # Логируем
            self._add_log(conn, "mute", muter_id, {"muted_id": muted_id, "reason": reason})

    def unmute_user(self, muter_id: int, muted_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM muted_users WHERE muter_id = ? AND muted_id = ?", 
                        (muter_id, muted_id))
            count = conn.execute("SELECT COUNT(*) AS c FROM muted_users WHERE muted_id = ?", 
                               (muted_id,)).fetchone()["c"]
            if count == 0:
                conn.execute("UPDATE users SET is_muted = 0 WHERE user_id = ?", (muted_id,))
            self._add_log(conn, "unmute", muter_id, {"muted_id": muted_id})

    def is_muted(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(row and row["is_muted"])

    def get_muters(self, muted_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT muter_id, reason, duration FROM muted_users WHERE muted_id = ?
            """, (muted_id,)).fetchall()

    def get_all_muted(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM muted_users ORDER BY muted_at DESC
            """).fetchall()

    def get_muted_by_user(self, muter_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM muted_users WHERE muter_id = ? ORDER BY muted_at DESC
            """, (muter_id,)).fetchall()

    # ==================================================================================
    # КНОПКИ - РАСШИРЕННЫЕ
    # ==================================================================================

    def get_button(self, key: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM buttons WHERE button_key = ?", (key,)).fetchone()
            if row is None:
                raise KeyError(f"Кнопка {key} не найдена")
            return row

    def get_all_buttons(self, only_visible: bool = False) -> list[sqlite3.Row]:
        with self.connect() as conn:
            query = "SELECT * FROM buttons"
            if only_visible:
                query += " WHERE is_visible = 1"
            query += " ORDER BY row_order, button_key"
            return conn.execute(query).fetchall()

    def update_button(self, key: str, **kwargs) -> None:
        with self.connect() as conn:
            fields = []
            values = []
            for field, value in kwargs.items():
                if field in ['label', 'style', 'icon_custom_emoji_id', 'emoji_prefix', 
                           'emoji_suffix', 'callback_data', 'url', 'copy_text', 
                           'web_app_url', 'switch_inline_query', 'login_url']:
                    fields.append(f"{field} = ?")
                    values.append(value)
                elif field in ['is_visible', 'row_order', 'pay']:
                    fields.append(f"{field} = ?")
                    values.append(int(value))
            
            if fields:
                values.append(key)
                fields.append("updated_at = ?")
                values.append(datetime.now(timezone.utc).isoformat())
                conn.execute(f"""
                    UPDATE buttons SET {', '.join(fields)} WHERE button_key = ?
                """, values)

    def delete_button(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM buttons WHERE button_key = ?", (key,))

    def create_button(self, key: str, label: str, style: Optional[str] = "success",
                     callback_data: Optional[str] = None, row_order: int = 0,
                     url: Optional[str] = None, copy_text: Optional[str] = None) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO buttons 
                (button_key, label, style, callback_data, row_order, is_visible, url, copy_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """, (key, label, style, callback_data, row_order, url, copy_text,
                  datetime.now(timezone.utc).isoformat(),
                  datetime.now(timezone.utc).isoformat()))

    # ==================================================================================
    # ЛОГИ
    # ==================================================================================

    def _add_log(self, conn: sqlite3.Connection, action_type: str, user_id: int, details: Dict) -> None:
        try:
            conn.execute("""
                INSERT INTO logs (log_time, log_level, action_type, user_id, details)
                VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                "INFO",
                action_type,
                user_id,
                json.dumps(details)
            ))
        except Exception:
            pass

    def get_logs(self, limit: int = 50, action_type: Optional[str] = None) -> list[sqlite3.Row]:
        with self.connect() as conn:
            query = "SELECT * FROM logs"
            params = []
            if action_type:
                query += " WHERE action_type = ?"
                params.append(action_type)
            query += " ORDER BY log_time DESC LIMIT ?"
            params.append(limit)
            return conn.execute(query, params).fetchall()

    # ==================================================================================
    # СТАТИСТИКА
    # ==================================================================================

    def increment_stats(self, stat_key: str, increment: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE stats SET stat_value = CAST(stat_value AS INTEGER) + ?,
                updated_at = ?
                WHERE stat_key = ?
            """, (increment, datetime.now(timezone.utc).isoformat(), stat_key))

    def get_stats(self, stat_key: str) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT stat_value FROM stats WHERE stat_key = ?", (stat_key,)).fetchone()
            return row["stat_value"] if row else "0"

    def get_all_stats(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM stats").fetchall()
            return {row["stat_key"]: row["stat_value"] for row in rows}

# ======================================================================================
# ЧАСЫ В НИКЕ
# ======================================================================================

class NicknameClock:
    def __init__(self, bot: Bot, db: Database, tz_offset_hours: int) -> None:
        self._bot = bot
        self._db = db
        self._tz = timezone(timedelta(hours=tz_offset_hours))
        self._last_applied: Dict[int, str] = {}
        self._last_checked: Dict[int, datetime] = {}

    def _current_label(self) -> str:
        now = datetime.now(self._tz)
        return f"• [{now.strftime('%H:%M')}]"

    def _current_time_str(self) -> str:
        now = datetime.now(self._tz)
        return now.strftime("%H:%M:%S")

    async def apply(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        
        label = self._current_label()
        if self._last_applied.get(user_id) == label:
            return
        
        try:
            # Проверяем, есть ли у бота права
            await self._bot(SetBusinessAccountName(
                business_connection_id=connection_id,
                first_name=first_name,
                last_name=label,
            ))
            self._last_applied[user_id] = label
            self._last_checked[user_id] = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Failed to update nickname for user {user_id}: {e}")
            self._db.set_enabled(user_id, False)
            
            # Уведомляем пользователя
            try:
                await self._bot.send_message(user_id,
                    "❌ Не удалось обновить никнейм. Проверьте права доступа бота."
                )
            except Exception:
                pass

    async def clear(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        try:
            await self._bot(SetBusinessAccountName(
                business_connection_id=connection_id,
                first_name=first_name,
                last_name="",
            ))
        except Exception as e:
            logger.error(f"Failed to clear nickname for user {user_id}: {e}")
        finally:
            self._last_applied.pop(user_id, None)
            self._last_checked.pop(user_id, None)

# ======================================================================================
# СОЗДАНИЕ КНОПОК
# ======================================================================================

class ButtonMaker:
    def __init__(self, db: Database):
        self.db = db

    def make_button(self, key: str, callback_data: Optional[str] = None,
                   url: Optional[str] = None, copy_text: Optional[CopyTextButton] = None,
                   web_app: Optional[WebAppInfo] = None) -> InlineKeyboardButton:
        """Создает кнопку из БД с полной поддержкой всех параметров"""
        btn = self.db.get_button(key)
        
        text = btn["label"]
        
        # Добавляем префикс/суффикс если есть
        if btn["emoji_prefix"]:
            text = f"{btn['emoji_prefix']} {text}"
        if btn["emoji_suffix"]:
            text = f"{text} {btn['emoji_suffix']}"
        
        kwargs = {"text": text}
        
        # Стиль кнопки (цвет)
        if btn["style"] in ("primary", "danger", "success"):
            kwargs["style"] = btn["style"]
        
        # Premium эмодзи на кнопке (работает с 9.4)
        if btn["icon_custom_emoji_id"]:
            kwargs["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
        
        # Определяем callback_data
        final_callback = callback_data or btn["callback_data"]
        final_url = url or btn["url"]
        
        if final_callback is not None:
            kwargs["callback_data"] = final_callback
        elif final_url is not None:
            kwargs["url"] = final_url
        elif btn["copy_text"] is not None:
            kwargs["copy_text"] = CopyTextButton(text=btn["copy_text"])
        elif copy_text is not None:
            kwargs["copy_text"] = copy_text
        elif web_app is not None:
            kwargs["web_app"] = web_app
        else:
            # Если ничего нет, ставим callback_data по умолчанию
            kwargs["callback_data"] = key
        
        return InlineKeyboardButton(**kwargs)

    def make_keyboard(self, buttons: List[Union[str, tuple]], row_width: int = 1) -> InlineKeyboardMarkup:
        """Создает клавиатуру из списка кнопок"""
        keyboard = []
        row = []
        
        for item in buttons:
            if isinstance(item, str):
                btn = self.make_button(item)
            elif isinstance(item, tuple):
                btn = self.make_button(item[0], callback_data=item[1] if len(item) > 1 else None)
            else:
                continue
            
            row.append(btn)
            if len(row) >= row_width:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ======================================================================================
# СОСТОЯНИЯ
# ======================================================================================

class AdminStates(StatesGroup):
    # Выдача подписки
    waiting_give_sub_user = State()
    waiting_give_sub_days = State()
    
    # Редактирование кнопок
    waiting_button_key = State()
    waiting_button_label = State()
    waiting_button_style = State()
    waiting_button_emoji = State()
    waiting_button_callback = State()
    waiting_button_url = State()
    waiting_button_copy = State()
    waiting_button_order = State()
    
    # Настройки бота
    waiting_bot_settings = State()
    
    # Управление пользователями
    waiting_user_action = State()
    waiting_user_id = State()
    waiting_user_warn = State()
    waiting_user_ban = State()

# ======================================================================================
# ОСНОВНАЯ ЛОГИКА БОТА
# ======================================================================================

def register_handlers(dp: Dispatcher, db: Database, clock: NicknameClock,
                      bot_username: str, settings: Settings, bot: Bot) -> None:
    
    button_maker = ButtonMaker(db)
    
    # ==================================================================================
    # ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
    # ==================================================================================
    
    def is_admin(user_id: int) -> bool:
        return user_id in settings.admin_ids
    
    def is_business_connected(user_id: int) -> bool:
        row = db.get_user(user_id)
        return bool(row and row.get("business_connection_id"))
    
    def get_user_status(user_id: int) -> Dict[str, Any]:
        row = db.get_user(user_id)
        if not row:
            return {"exists": False}
        
        return {
            "exists": True,
            "is_subscribed": db.is_subscribed(user_id),
            "is_enabled": bool(row.get("enabled", 0)),
            "is_connected": bool(row.get("business_connection_id")),
            "is_muted": bool(row.get("is_muted", 0)),
            "is_admin": is_admin(user_id),
        }
    
    # ==================================================================================
    # КОМАНДА /start - С АВТО-ПОДПИСКОЙ ДЛЯ АДМИНОВ
    # ==================================================================================
    
    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        user_id = message.from_user.id
        user = message.from_user
        
        db.upsert_user(
            user_id=user_id,
            first_name=user.first_name or "",
            username=user.username,
            last_name=user.last_name,
            language_code=user.language_code,
            is_premium=user.is_premium or False
        )
        
        db.update_last_seen(user_id)
        
        # === АВТО-ПОДПИСКА ДЛЯ АДМИНОВ ===
        if is_admin(user_id) and settings.auto_subscribe_admins:
            sub_info = db.get_subscription_info(user_id)
            if not sub_info.get("has_subscription", False) or not sub_info.get("active", False):
                db.set_subscription(user_id, settings.subscription_days_admin)
                logger.info(f"✅ Admin {user_id} auto-subscribed for {settings.subscription_days_admin} days")
        
        row = db.get_user(user_id)
        is_subscribed = db.is_subscribed(user_id)
        is_connected = bool(row and row["business_connection_id"])
        is_enabled = bool(row and row["enabled"]) if row else False
        
        # === ГЛАВНОЕ МЕНЮ ===
        main_buttons = []
        
        if not is_subscribed:
            # Нет подписки - показываем оплату
            main_buttons.append("pay_subscription_btn")
            main_buttons.append("about_btn")
            
            await message.answer(
                f"🌟 <b>Добро пожаловать в {BOT_NAME}!</b>\n\n"
                f"Бот показывает текущее время в вашем бизнес-аккаунте.\n\n"
                f"<b>💰 Стоимость подписки:</b> {SUBSCRIPTION_STARS} ⭐ в месяц\n"
                f"<b>👑 Админы получают подписку автоматически!</b>\n\n"
                f"Нажмите кнопку ниже для оплаты:",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard(main_buttons, row_width=1)
            )
            return
        
        # === ЕСТЬ ПОДПИСКА ===
        if not is_connected:
            # Не подключен бизнес-бот
            main_buttons.append("connect")
            main_buttons.append("copy_username")
            main_buttons.append("about_btn")
            
            await message.answer(
                "🔗 <b>Подключите бизнес-бота</b>\n\n"
                "1️⃣ Нажмите <b>Подключить</b>\n"
                "2️⃣ Нажмите <b>Скопировать</b>\n"
                "3️⃣ Перейдите в <b>Настройки → Автоматизация чатов</b>\n"
                "4️⃣ Вставьте скопированный юзернейм\n"
                "5️⃣ Разрешите <b>Управление профилем</b>",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard(main_buttons, row_width=2)
            )
            return
        
        # === ВСЁ ПОДКЛЮЧЕНО ===
        status = "🟢 <b>Включено</b>" if is_enabled else "🔴 <b>Выключено</b>"
        
        main_buttons.append("toggle_off" if is_enabled else "toggle_on")
        main_buttons.append("my_subscription")
        main_buttons.append("mute_help_btn")
        main_buttons.append("about_btn")
        
        # Если админ - добавляем кнопку админ-панели
        if is_admin(user_id):
            main_buttons.append("admin_stats_btn")
        
        await message.answer(
            f"⏰ <b>TimeNick</b>\n\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"Управляйте ботом с помощью кнопок:",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(main_buttons, row_width=2)
        )

    # ==================================================================================
    # КОМАНДА /admin
    # ==================================================================================
    
    @dp.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        user_id = message.from_user.id
        
        if not is_admin(user_id):
            await message.answer(
                f"❌ <b>Доступ запрещен!</b>\n\n"
                f"Ваш ID: <code>{user_id}</code>\n"
                f"Добавьте этот ID в переменную ADMIN_IDS на хостинге.",
                parse_mode="HTML"
            )
            return
        
        # Админ-панель
        admin_buttons = [
            "admin_stats_btn",
            "admin_users_btn",
            "admin_buttons_btn",
            "admin_give_sub_btn",
            "admin_mutes_btn",
            "admin_settings_btn",
            "admin_logs_btn",
        ]
        
        await message.answer(
            f"👑 <b>Админ-панель</b>\n\n"
            f"Добро пожаловать, администратор!\n"
            f"Ваш ID: <code>{user_id}</code>\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(admin_buttons, row_width=2)
        )

    # ==================================================================================
    # КОМАНДА /check
    # ==================================================================================
    
    @dp.message(Command("check"))
    async def cmd_check(message: Message) -> None:
        user_id = message.from_user.id
        status = get_user_status(user_id)
        sub_info = db.get_subscription_info(user_id)
        
        text = (
            f"🔍 <b>Проверка статуса</b>\n\n"
            f"👤 Ваш ID: <code>{user_id}</code>\n"
            f"👑 Админ: {'✅ Да' if is_admin(user_id) else '❌ Нет'}\n"
            f"⭐ Подписка: {'✅ Активна' if status['is_subscribed'] else '❌ Нет'}\n"
            f"🔗 Бот подключен: {'✅ Да' if status['is_connected'] else '❌ Нет'}\n"
            f"🟢 Функция включена: {'✅ Да' if status['is_enabled'] else '❌ Нет'}\n"
            f"🔇 Замучен: {'✅ Да' if status['is_muted'] else '❌ Нет'}\n"
        )
        
        if sub_info.get("has_subscription", False):
            text += f"\n📅 Подписка до: {sub_info.get('expires_at', '—')}\n"
            text += f"⏳ Осталось дней: {sub_info.get('days_left', 0)}"
        
        # Показываем список админов
        text += f"\n\n👑 Список админов: <code>{settings.admin_ids}</code>"
        
        await message.answer(text, parse_mode="HTML")

    # ==================================================================================
    # КОМАНДЫ МУТА
    # ==================================================================================
    
    @dp.message(Command("mute"))
    async def cmd_mute(message: Message, command: CommandObject) -> None:
        user_id = message.from_user.id
        
        if not db.is_subscribed(user_id):
            await message.answer("❌ Требуется активная подписка!")
            return
        
        if not command.args:
            await message.answer(
                "🔇 <b>Как замутить</b>\n\n"
                "<code>.mute @username</code> - замутить по юзернейму\n"
                "<code>.mute 123456789</code> - замутить по ID\n"
                "<code>.mute @username причина</code> - с указанием причины\n\n"
                "Пример: <code>.mute @john спам</code>",
                parse_mode="HTML"
            )
            return
        
        args = command.args.split()
        target = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "Без причины"
        
        if target.startswith("@"):
            target = target[1:]
        
        target_id = None
        try:
            target_id = int(target)
        except ValueError:
            for u in db.get_all_users():
                if u["username"] and u["username"].lower() == target.lower():
                    target_id = u["user_id"]
                    break
        
        if not target_id:
            await message.answer("❌ Пользователь не найден в базе данных.")
            return
        
        if target_id == user_id:
            await message.answer("❌ Нельзя замутить самого себя!")
            return
        
        db.mute_user(user_id, target_id, reason)
        db.increment_stats("total_mutes")
        
        await message.answer(
            f"🔇 <b>Пользователь замучен!</b>\n\n"
            f"ID: <code>{target_id}</code>\n"
            f"Причина: {reason}\n\n"
            f"Для размута: <code>.unmute {target_id}</code>",
            parse_mode="HTML"
        )
        
        # Уведомляем замученного
        try:
            await bot.send_message(target_id,
                f"🔇 <b>Вы были замучены!</b>\n\n"
                f"Администратор: <code>{user_id}</code>\n"
                f"Причина: {reason}\n\n"
                f"Ваши сообщения будут удаляться.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.message(Command("unmute"))
    async def cmd_unmute(message: Message, command: CommandObject) -> None:
        user_id = message.from_user.id
        
        if not db.is_subscribed(user_id):
            await message.answer("❌ Требуется активная подписка!")
            return
        
        if not command.args:
            await message.answer(
                "🔊 <b>Как размутить</b>\n\n"
                "<code>.unmute @username</code> - размутить по юзернейму\n"
                "<code>.unmute 123456789</code> - размутить по ID",
                parse_mode="HTML"
            )
            return
        
        target = command.args.strip()
        if target.startswith("@"):
            target = target[1:]
        
        target_id = None
        try:
            target_id = int(target)
        except ValueError:
            for u in db.get_all_users():
                if u["username"] and u["username"].lower() == target.lower():
                    target_id = u["user_id"]
                    break
        
        if not target_id:
            await message.answer("❌ Пользователь не найден.")
            return
        
        db.unmute_user(user_id, target_id)
        db.increment_stats("total_unmutes")
        
        await message.answer(
            f"🔊 <b>Пользователь размучен!</b>\n\n"
            f"ID: <code>{target_id}</code>",
            parse_mode="HTML"
        )

    # ==================================================================================
    # CALLBACK - ПОЛЬЗОВАТЕЛЬСКИЕ
    # ==================================================================================
    
    @dp.callback_query(F.data == "toggle_on")
    async def callback_toggle_on(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        
        if not db.is_subscribed(user_id):
            await callback.answer("❌ Нет активной подписки!", show_alert=True)
            return
        
        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("❌ Бизнес-бот не подключен!", show_alert=True)
            return
        
        db.set_enabled(user_id, True)
        await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
        
        await callback.message.edit_text(
            "✅ <b>Время в нике включено!</b>\n\n"
            f"🕐 Текущее время: {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["toggle_off", "my_subscription", "mute_help_btn"], row_width=2)
        )
        await callback.answer("✅ Включено!")

    @dp.callback_query(F.data == "toggle_off")
    async def callback_toggle_off(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        
        db.set_enabled(user_id, False)
        
        await callback.message.edit_text(
            "❌ <b>Время в нике выключено</b>",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["toggle_on", "my_subscription", "mute_help_btn"], row_width=2)
        )
        await callback.answer("❌ Выключено!")

    @dp.callback_query(F.data == "my_subscription")
    async def callback_my_subscription(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        sub_info = db.get_subscription_info(user_id)
        
        if not sub_info.get("has_subscription", False) or not sub_info.get("active", False):
            await callback.message.edit_text(
                "❌ <b>У вас нет активной подписки</b>\n\n"
                f"💰 Стоимость: {SUBSCRIPTION_STARS} ⭐ в месяц",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard(["pay_subscription_btn", "admin_back_btn"])
            )
            await callback.answer()
            return
        
        expires_at = sub_info["expires_at"]
        days_left = sub_info.get("days_left", 0)
        
        text = (
            f"📊 <b>Моя подписка</b>\n\n"
            f"✅ <b>Статус:</b> Активна\n"
            f"📅 <b>Действует до:</b> {expires_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"⏳ <b>Осталось дней:</b> {days_left}\n"
            f"⭐ <b>Стоимость:</b> {sub_info.get('price_stars', SUBSCRIPTION_STARS)} ⭐\n"
            f"📋 <b>План:</b> {sub_info.get('plan_type', 'Premium')}"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["pay_subscription_btn", "admin_back_btn"])
        )
        await callback.answer()

    @dp.callback_query(F.data == "mute_help")
    async def callback_mute_help(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        is_muted = db.is_muted(user_id)
        
        text = (
            "🔇 <b>Помощь по муту</b>\n\n"
            "📌 <b>Как замутить:</b>\n"
            "<code>.mute @username</code> - по юзернейму\n"
            "<code>.mute 123456789</code> - по ID\n"
            "<code>.mute @username причина</code> - с причиной\n\n"
            "📌 <b>Как размутить:</b>\n"
            "<code>.unmute @username</code>\n"
            "<code>.unmute 123456789</code>\n\n"
            "⚡️ <b>Важно:</b>\n"
            "• Работает только с активной подпиской\n"
            "• Замученные не могут писать в ЛС\n"
            "• Админы могут мутить всех"
        )
        
        buttons = []
        if is_muted:
            buttons.append(("🔊 Размутить меня", "unmute_me"))
        buttons.append(("⬅️ Назад", "admin_back" if is_admin(user_id) else "back_to_menu"))
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(buttons)
        )
        await callback.answer()

    @dp.callback_query(F.data == "unmute_me")
    async def callback_unmute_me(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        
        if not db.is_muted(user_id):
            await callback.answer("✅ Вы не замучены!", show_alert=True)
            return
        
        muters = db.get_muters(user_id)
        for m in muters:
            db.unmute_user(m["muter_id"], user_id)
        
        await callback.answer("✅ Вы размучены!", show_alert=True)
        await callback.message.edit_text(
            "🔊 <b>Вы размучены!</b>\n\n"
            "Теперь вы можете писать сообщения.",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["toggle_on", "my_subscription"])
        )

    @dp.callback_query(F.data == "about")
    async def callback_about(callback: CallbackQuery) -> None:
        text = (
            f"ℹ️ <b>О боте {BOT_NAME}</b>\n\n"
            f"Версия: <b>{VERSION}</b>\n\n"
            f"🤖 <b>Что умеет:</b>\n"
            f"• Показывать время в бизнес-аккаунте\n"
            f"• Система мута пользователей\n"
            f"• Подписка через Stars\n"
            f"• Полная админ-панель\n\n"
            f"🎨 <b>Особенности:</b>\n"
            f"• Premium эмодзи на кнопках\n"
            f"• Цветные кнопки (Telegram 9.4+)\n"
            f"• Полная кастомизация\n\n"
            f"👑 <b>Админы получают подписку автоматически!</b>\n\n"
            f"💰 <b>Стоимость:</b> {SUBSCRIPTION_STARS} ⭐ в месяц"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["admin_back_btn"])
        )
        await callback.answer()

    @dp.callback_query(F.data == "connect_business")
    async def callback_connect_business(callback: CallbackQuery) -> None:
        await callback.message.edit_text(
            "🔗 <b>Подключение бизнес-бота</b>\n\n"
            "1️⃣ Нажмите <b>Подключить</b>\n"
            "2️⃣ Нажмите <b>Скопировать</b>\n"
            "3️⃣ Перейдите в <b>Настройки</b>\n"
            "4️⃣ Выберите <b>Автоматизация чатов</b>\n"
            "5️⃣ Нажмите <b>Добавить бота</b>\n"
            "6️⃣ Вставьте скопированный юзернейм\n"
            "7️⃣ Разрешите <b>Управление профилем</b>\n\n"
            "✅ После подключения бот будет работать!",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["connect", "copy_username", "admin_back_btn"])
        )
        await callback.answer()

    @dp.callback_query(F.data == "back_to_menu")
    async def callback_back_to_menu(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        is_enabled = bool(row and row["enabled"]) if row else False
        is_subscribed = db.is_subscribed(user_id)
        is_connected = bool(row and row["business_connection_id"]) if row else False
        
        if not is_subscribed:
            await callback.message.edit_text(
                "🌟 <b>Добро пожаловать в TimeNick!</b>",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard(["pay_subscription_btn", "about_btn"])
            )
            await callback.answer()
            return
        
        if not is_connected:
            await callback.message.edit_text(
                "🔗 <b>Подключите бизнес-бота</b>",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard(["connect", "copy_username", "about_btn"], row_width=2)
            )
            await callback.answer()
            return
        
        status = "🟢 Включено" if is_enabled else "🔴 Выключено"
        main_buttons = []
        main_buttons.append("toggle_off" if is_enabled else "toggle_on")
        main_buttons.append("my_subscription")
        main_buttons.append("mute_help_btn")
        main_buttons.append("about_btn")
        
        if is_admin(user_id):
            main_buttons.append("admin_stats_btn")
        
        await callback.message.edit_text(
            f"⏰ <b>TimeNick</b>\n\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(main_buttons, row_width=2)
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_back")
    async def callback_admin_back(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await cmd_admin(callback.message)
        await callback.answer()

    @dp.callback_query(F.data == "admin_refresh")
    async def callback_admin_refresh(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await callback.answer("🔄 Обновлено!")
        await cmd_admin(callback.message)

    @dp.callback_query(F.data == "support")
    async def callback_support(callback: CallbackQuery) -> None:
        await callback.message.edit_text(
            "🆘 <b>Поддержка</b>\n\n"
            "По всем вопросам обращайтесь к администратору.\n\n"
            f"👑 Список админов: <code>{settings.admin_ids}</code>",
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["admin_back_btn"])
        )
        await callback.answer()

    # ==================================================================================
    # CALLBACK - ОПЛАТА
    # ==================================================================================
    
    @dp.callback_query(F.data == "pay_subscription")
    async def callback_pay_subscription(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        
        # Если админ - выдаем подписку бесплатно
        if is_admin(user_id):
            db.set_subscription(user_id, settings.subscription_days_admin)
            await callback.answer("👑 Вам как админу выдана подписка автоматически!", show_alert=True)
            await callback_my_subscription(callback)
            return
        
        prices = [LabeledPrice(label="Подписка на 30 дней", amount=SUBSCRIPTION_STARS)]
        link = await callback.bot.create_invoice_link(
            title="TimeNick Подписка",
            description=f"Доступ к боту на 30 дней за {SUBSCRIPTION_STARS} Stars",
            payload=f"{SUBSCRIPTION_PAYLOAD}_{user_id}",
            currency="XTR",
            prices=prices,
        )
        
        await callback.message.edit_text(
            f"💳 <b>Оплата подписки</b>\n\n"
            f"💰 Стоимость: <b>{SUBSCRIPTION_STARS} ⭐</b>\n"
            f"📅 Период: <b>30 дней</b>\n\n"
            f"Нажмите кнопку ниже для оплаты:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ Оплатить {SUBSCRIPTION_STARS} ⭐", url=link)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_subscription")]
            ])
        )
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        await pre_checkout_query.answer(ok=True)

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        user_id = message.from_user.id
        
        # Извлекаем user_id из payload
        payload = payment.invoice_payload
        target_user_id = int(payload.split("_")[1]) if "_" in payload else user_id
        
        db.set_subscription(target_user_id, 30)
        db.increment_stats("total_payments")
        db.increment_stats("total_stars_spent", SUBSCRIPTION_STARS)
        
        # Логируем платеж
        with db.connect() as conn:
            conn.execute("""
                INSERT INTO payments (user_id, amount, currency, status, telegram_payment_id, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                target_user_id,
                SUBSCRIPTION_STARS,
                "XTR",
                "completed",
                payment.telegram_payment_charge_id,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            ))
        
        await message.answer(
            "✅ <b>Подписка активирована!</b>\n\n"
            "Теперь вам доступны все функции бота на 30 дней.\n"
            "Используйте /start для управления.",
            parse_mode="HTML"
        )
        
        # Уведомляем админов
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id,
                    f"💰 <b>Новый платеж!</b>\n\n"
                    f"Пользователь: <code>{target_user_id}</code>\n"
                    f"Сумма: {SUBSCRIPTION_STARS} ⭐\n"
                    f"Подписка активирована на 30 дней",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    # ==================================================================================
    # BUSINESS CONNECTION
    # ==================================================================================
    
    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        user = connection.user
        
        db.upsert_user(
            user_id=user_id,
            first_name=user.first_name or "",
            username=user.username,
            is_premium=user.is_premium or False
        )
        
        if connection.is_enabled:
            db.set_business_connection(user_id, connection.id)
            
            if not db.is_subscribed(user_id):
                try:
                    await connection.bot.send_message(user_id,
                        "⭐ <b>Требуется подписка</b>\n\n"
                        f"Стоимость: {SUBSCRIPTION_STARS} ⭐ в месяц",
                        parse_mode="HTML",
                        reply_markup=button_maker.make_keyboard(["pay_subscription_btn"])
                    )
                except Exception:
                    pass
                return
            
            try:
                await connection.bot.send_message(user_id,
                    "✅ <b>Бизнес-бот подключен!</b>\n\n"
                    "Используйте /start для управления.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return
        
        # Отключение
        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        
        db.set_business_connection(user_id, None)
        db.set_enabled(user_id, False)

    # ==================================================================================
    # УДАЛЕНИЕ СООБЩЕНИЙ ОТ ЗАМУЧЕННЫХ
    # ==================================================================================
    
    @dp.message(F.chat.type == "private")
    async def delete_muted_messages(message: Message) -> None:
        if db.is_muted(message.from_user.id):
            try:
                await message.delete()
                logger.info(f"🗑️ Deleted message from muted user {message.from_user.id}")
            except Exception as e:
                logger.error(f"Failed to delete message: {e}")

    # ==================================================================================
    # АДМИН - СТАТИСТИКА
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        total = db.count_users()
        subscribed = db.count_active_subscribers()
        connected = db.count_connected()
        enabled = db.count_enabled()
        muted = len(db.get_all_muted())
        total_mutes = int(db.get_stats("total_mutes") or 0)
        total_payments = int(db.get_stats("total_payments") or 0)
        total_stars = int(db.get_stats("total_stars_spent") or 0)
        
        text = (
            f"📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: <b>{total}</b>\n"
            f"⭐ Активных подписок: <b>{subscribed}</b>\n"
            f"🔗 Подключили бота: <b>{connected}</b>\n"
            f"🟢 Функция включена: <b>{enabled}</b>\n"
            f"🔇 Замученных: <b>{muted}</b>\n"
            f"📝 Всего мутов: <b>{total_mutes}</b>\n"
            f"💰 Всего платежей: <b>{total_payments}</b>\n"
            f"⭐ Потрачено Stars: <b>{total_stars}</b>\n\n"
            f"🤖 Версия: <b>{VERSION}</b>"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["admin_refresh_btn", "admin_back_btn"])
        )
        await callback.answer()

    # ==================================================================================
    # АДМИН - ПОЛЬЗОВАТЕЛИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_users")
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        users = db.get_all_users(limit=20)
        text = "👥 <b>Последние 20 пользователей</b>\n\n"
        
        for i, u in enumerate(users, 1):
            uname = f"@{u['username']}" if u["username"] else "—"
            sub = "✅" if db.is_subscribed(u["user_id"]) else "❌"
            muted = "🔇" if u["is_muted"] else "🔊"
            enabled = "🟢" if u["enabled"] else "🔴"
            text += f"{i}. <code>{u['user_id']}</code> {uname} {enabled} {muted} {sub}\n"
        
        total = db.count_users()
        if total > 20:
            text += f"\n... и еще {total - 20} пользователей"
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard([
                ("🔍 Найти пользователя", "admin_find_user"),
                ("📊 Полный список", "admin_all_users"),
                ("admin_back_btn")
            ])
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_all_users")
    async def admin_all_users(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        users = db.get_all_users(limit=100)
        text = "👥 <b>Все пользователи</b>\n\n"
        
        for i, u in enumerate(users, 1):
            uname = f"@{u['username']}" if u["username"] else "—"
            sub = "✅" if db.is_subscribed(u["user_id"]) else "❌"
            muted = "🔇" if u["is_muted"] else "🔊"
            text += f"{i}. <code>{u['user_id']}</code> {uname} {muted} {sub}\n"
        
        if len(users) >= 100:
            text += "\n... показаны первые 100"
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["admin_back_btn"])
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_find_user")
    async def admin_find_user_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await state.set_state(AdminStates.waiting_user_id)
        await callback.message.answer(
            "🔍 <b>Поиск пользователя</b>\n\n"
            "Введите ID или юзернейм пользователя:",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_user_id)
    async def admin_find_user_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        query = message.text.strip()
        
        user_id = None
        if query.isdigit():
            user_id = int(query)
        else:
            if query.startswith("@"):
                query = query[1:]
            for u in db.get_all_users():
                if u["username"] and u["username"].lower() == query.lower():
                    user_id = u["user_id"]
                    break
        
        if not user_id:
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return
        
        row = db.get_user(user_id)
        if not row:
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return
        
        sub_info = db.get_subscription_info(user_id)
        is_muted = db.is_muted(user_id)
        
        text = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Имя: {row['first_name']}\n"
            f"Юзернейм: @{row['username'] or '—'}\n"
            f"Премиум: {'✅' if row['is_premium'] else '❌'}\n\n"
            f"⭐ Подписка: {'✅ Активна' if sub_info.get('active', False) else '❌'}\n"
            f"📅 До: {sub_info.get('expires_at', '—')}\n\n"
            f"🔗 Бот подключен: {'✅' if row['business_connection_id'] else '❌'}\n"
            f"🟢 Функция: {'✅' if row['enabled'] else '❌'}\n"
            f"🔇 Замучен: {'✅' if is_muted else '❌'}\n"
            f"🕐 Последний раз: {row['last_seen'] or '—'}"
        )
        
        await message.answer(text, parse_mode="HTML")
        await state.clear()

    # ==================================================================================
    # АДМИН - ВЫДАЧА ПОДПИСКИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_give_sub")
    async def admin_give_sub(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await state.set_state(AdminStates.waiting_give_sub_user)
        await callback.message.answer(
            "⭐ <b>Выдача подписки</b>\n\n"
            "Введите ID пользователя:",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_give_sub_user)
    async def admin_give_sub_user(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        try:
            user_id = int(message.text.strip())
            await state.update_data(user_id=user_id)
            await state.set_state(AdminStates.waiting_give_sub_days)
            await message.answer(
                f"⭐ <b>Выдача подписки</b>\n\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Введите количество дней:",
                parse_mode="HTML"
            )
        except ValueError:
            await message.answer("❌ Введите корректный ID (только цифры)")

    @dp.message(AdminStates.waiting_give_sub_days)
    async def admin_give_sub_days(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        try:
            days = int(message.text.strip())
            if days <= 0:
                await message.answer("❌ Количество дней должно быть больше 0")
                return
            
            data = await state.get_data()
            user_id = data.get("user_id")
            
            db.set_subscription(user_id, days)
            
            await message.answer(
                f"✅ <b>Подписка выдана!</b>\n\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Дней: {days}\n"
                f"Подписка активна!",
                parse_mode="HTML"
            )
            
            # Уведомляем пользователя
            try:
                await bot.send_message(user_id,
                    f"⭐ <b>Вам выдана подписка!</b>\n\n"
                    f"📅 Дней: {days}\n"
                    f"Теперь вам доступны все функции бота.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            
            await state.clear()
            
        except ValueError:
            await message.answer("❌ Введите корректное число")

    # ==================================================================================
    # АДМИН - УПРАВЛЕНИЕ КНОПКАМИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        buttons = db.get_all_buttons()
        
        if not buttons:
            await callback.message.edit_text(
                "🎨 <b>Управление кнопками</b>\n\n"
                "Кнопок пока нет.",
                parse_mode="HTML",
                reply_markup=button_maker.make_keyboard([
                    ("➕ Создать кнопку", "admin_create_button"),
                    "admin_back_btn"
                ])
            )
            await callback.answer()
            return
        
        text = "🎨 <b>Управление кнопками</b>\n\n"
        text += f"Всего кнопок: {len(buttons)}\n"
        text += "Нажмите на кнопку для редактирования:\n\n"
        
        keyboard = []
        for btn in buttons:
            status = "✅" if btn["is_visible"] else "❌"
            style_emoji = {
                "success": "🟢",
                "danger": "🔴",
                "primary": "🔵"
            }.get(btn["style"], "⚪️")
            
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{style_emoji} {btn['label']} {status}",
                    callback_data=f"edit_button:{btn['button_key']}"
                )
            ])
        
        keyboard.append([
            InlineKeyboardButton(text="➕ Создать кнопку", callback_data="admin_create_button")
        ])
        keyboard.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")
        ])
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("edit_button:"))
    async def admin_edit_button(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        btn = db.get_button(key)
        
        text = (
            f"🔧 <b>Редактирование кнопки</b>\n\n"
            f"📌 Ключ: <code>{key}</code>\n"
            f"📝 Текст: {btn['label']}\n"
            f"🎨 Стиль: {btn['style'] or 'по умолчанию'}\n"
            f"🔢 Порядок: {btn['row_order']}\n"
            f"👁️ Видимость: {'✅' if btn['is_visible'] else '❌'}\n"
            f"✨ Emoji ID: {btn['icon_custom_emoji_id'] or 'нет'}\n"
            f"🎯 Callback: {btn['callback_data'] or 'нет'}\n"
            f"🔗 URL: {btn['url'] or 'нет'}\n"
            f"📋 Copy: {btn['copy_text'] or 'нет'}"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Текст", callback_data=f"btn_label:{key}")],
                [InlineKeyboardButton(text="🎨 Стиль", callback_data=f"btn_style:{key}")],
                [InlineKeyboardButton(text="🔄 Порядок", callback_data=f"btn_order:{key}")],
                [InlineKeyboardButton(text="👁️ Видимость", callback_data=f"btn_visible:{key}")],
                [InlineKeyboardButton(text="✨ Emoji ID", callback_data=f"btn_emoji:{key}")],
                [InlineKeyboardButton(text="📎 Префикс", callback_data=f"btn_prefix:{key}")],
                [InlineKeyboardButton(text="📎 Суффикс", callback_data=f"btn_suffix:{key}")],
                [InlineKeyboardButton(text="🎯 Callback", callback_data=f"btn_callback:{key}")],
                [InlineKeyboardButton(text="🔗 URL", callback_data=f"btn_url:{key}")],
                [InlineKeyboardButton(text="📋 Copy", callback_data=f"btn_copy:{key}")],
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"btn_delete:{key}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")]
            ])
        )
        await callback.answer()

    # ==================================================================================
    # АДМИН - НАСТРОЙКИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_settings")
    async def admin_settings(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        text = (
            f"⚙️ <b>Настройки бота</b>\n\n"
            f"<b>Общие:</b>\n"
            f"• Версия: {VERSION}\n"
            f"• Бот: @{bot_username}\n"
            f"• Часовой пояс: UTC+{settings.timezone_offset_hours}\n\n"
            f"<b>Подписка:</b>\n"
            f"• Цена: {SUBSCRIPTION_STARS} ⭐\n"
            f"• Период: 30 дней\n"
            f"• Авто-подписка админов: {'✅' if settings.auto_subscribe_admins else '❌'}\n"
            f"• Дней админам: {settings.subscription_days_admin}\n\n"
            f"<b>Админы:</b>\n"
            f"• Список: <code>{settings.admin_ids}</code>"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard(["admin_back_btn"])
        )
        await callback.answer()

    # ==================================================================================
    # АДМИН - ЛОГИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_logs")
    async def admin_logs(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        logs = db.get_logs(limit=20)
        
        if not logs:
            text = "📋 <b>Логи</b>\n\nПока пусто."
        else:
            text = "📋 <b>Последние логи</b>\n\n"
            for log in logs:
                text += f"• {log['log_time'][:16]} | {log['action_type']} | {log['user_id']}\n"
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard([
                ("🔄 Обновить", "admin_refresh"),
                "admin_back_btn"
            ])
        )
        await callback.answer()

    # ==================================================================================
    # АДМИН - МУТЫ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_mutes")
    async def admin_mutes(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        muted = db.get_all_muted()
        
        if not muted:
            text = "🔇 <b>Управление мутами</b>\n\nНет замученных пользователей."
        else:
            text = f"🔇 <b>Замученные пользователи ({len(muted)})</b>\n\n"
            for m in muted[:20]:
                text += f"• Мутящий: <code>{m['muter_id']}</code> → Мут: <code>{m['muted_id']}</code>\n"
                text += f"  Причина: {m['reason'] or 'Без причины'}\n"
                text += f"  Время: {m['muted_at'][:16]}\n\n"
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=button_maker.make_keyboard([
                ("🔄 Размутить всех", "admin_unmute_all"),
                ("admin_back_btn")
            ])
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_unmute_all")
    async def admin_unmute_all(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        muted = db.get_all_muted()
        count = len(muted)
        
        for m in muted:
            db.unmute_user(m["muter_id"], m["muted_id"])
        
        await callback.answer(f"✅ Размучено {count} пользователей!", show_alert=True)
        await admin_mutes(callback)

    # ==================================================================================
    # АДМИН - СОЗДАНИЕ КНОПКИ
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_create_button")
    async def admin_create_button(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await state.set_state(AdminStates.waiting_button_key)
        await callback.message.answer(
            "➕ <b>Создание новой кнопки</b>\n\n"
            "Введите уникальный ключ для кнопки (латиница, без пробелов):\n"
            "Пример: <code>my_button</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_button_key)
    async def admin_create_button_key(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        key = message.text.strip()
        if not key or " " in key:
            await message.answer("❌ Ключ не должен содержать пробелов!")
            return
        
        try:
            db.get_button(key)
            await message.answer("❌ Кнопка с таким ключом уже существует!")
            return
        except KeyError:
            pass
        
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_label)
        await message.answer(f"✅ Ключ: {key}\n\nТеперь введите текст кнопки:")

    @dp.message(AdminStates.waiting_button_label)
    async def admin_create_button_label(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        label = message.text.strip()
        
        db.create_button(key, label, style="success")
        await state.clear()
        
        await message.answer(
            f"✅ <b>Кнопка создана!</b>\n\n"
            f"📌 Ключ: <code>{key}</code>\n"
            f"📝 Текст: {label}\n"
            f"🎨 Стиль: success (зеленый)\n\n"
            f"Теперь вы можете настроить её в меню редактирования.",
            parse_mode="HTML"
        )
        
        await admin_buttons(message)

    # ==================================================================================
    # АДМИН - РЕДАКТИРОВАНИЕ КНОПОК (ВСЕ ФУНКЦИИ)
    # ==================================================================================
    
    @dp.callback_query(F.data.startswith("btn_label:"))
    async def admin_btn_label(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_label)
        
        await callback.message.answer(f"✏️ Введите новый текст для кнопки «{key}»:")
        await callback.answer()

    @dp.message(AdminStates.waiting_button_label)
    async def admin_btn_label_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        db.update_button(key, label=message.text.strip())
        await state.clear()
        await message.answer(f"✅ Текст кнопки «{key}» обновлен!")
        await admin_edit_button(message)

    @dp.callback_query(F.data.startswith("btn_style:"))
    async def admin_btn_style(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        
        await callback.message.edit_text(
            f"🎨 <b>Выберите стиль для кнопки «{key}»</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🟢 Success (Зеленый)", callback_data=f"set_style:{key}:success")],
                [InlineKeyboardButton(text="🔴 Danger (Красный)", callback_data=f"set_style:{key}:danger")],
                [InlineKeyboardButton(text="🔵 Primary (Синий)", callback_data=f"set_style:{key}:primary")],
                [InlineKeyboardButton(text="⚪️ По умолчанию", callback_data=f"set_style:{key}:none")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"edit_button:{key}")]
            ])
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_style:"))
    async def admin_set_style(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        parts = callback.data.split(":", 2)
        key = parts[1]
        style = parts[2] if len(parts) > 2 else None
        
        if style == "none":
            style = None
        
        db.update_button(key, style=style)
        await callback.answer(f"✅ Стиль обновлен: {style or 'по умолчанию'}")
        await admin_edit_button(callback)

    @dp.callback_query(F.data.startswith("btn_order:"))
    async def admin_btn_order(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_order)
        
        await callback.message.answer(f"🔄 Введите номер порядка для кнопки «{key}»:")
        await callback.answer()

    @dp.message(AdminStates.waiting_button_order)
    async def admin_btn_order_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        try:
            order = int(message.text.strip())
            db.update_button(key, row_order=order)
            await state.clear()
            await message.answer(f"✅ Порядок кнопки «{key}»: {order}")
            await admin_edit_button(message)
        except ValueError:
            await message.answer("❌ Введите число!")

    @dp.callback_query(F.data.startswith("btn_visible:"))
    async def admin_btn_visible(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        btn = db.get_button(key)
        db.update_button(key, is_visible=not btn["is_visible"])
        
        await callback.answer(f"✅ Видимость: {'показана' if not btn['is_visible'] else 'скрыта'}")
        await admin_edit_button(callback)

    @dp.callback_query(F.data.startswith("btn_emoji:"))
    async def admin_btn_emoji(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_emoji)
        
        await callback.message.answer(
            f"✨ Введите Custom Emoji ID для кнопки «{key}»:\n\n"
            f"Чтобы получить ID эмодзи:\n"
            f"1. Отправьте эмодзи боту @getstickerbot\n"
            f"2. Скопируйте его ID\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_button_emoji)
    async def admin_btn_emoji_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        emoji = message.text.strip()
        if emoji.lower() == "none":
            emoji = None
        
        db.update_button(key, icon_custom_emoji_id=emoji)
        await state.clear()
        await message.answer(f"✅ Emoji ID для кнопки «{key}» обновлен!")
        await admin_edit_button(message)

    @dp.callback_query(F.data.startswith("btn_prefix:"))
    async def admin_btn_prefix(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_emoji)
        
        await callback.message.answer(
            f"📎 Введите эмодзи-префикс для кнопки «{key}»:\n"
            f"Пример: ⭐\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("btn_suffix:"))
    async def admin_btn_suffix(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_emoji)
        
        await callback.message.answer(
            f"📎 Введите эмодзи-суффикс для кнопки «{key}»:\n"
            f"Пример: ✨\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("btn_callback:"))
    async def admin_btn_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_callback)
        
        await callback.message.answer(
            f"🎯 Введите callback_data для кнопки «{key}»:\n"
            f"Пример: <code>my_action</code>\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_button_callback)
    async def admin_btn_callback_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        callback_data = message.text.strip()
        if callback_data.lower() == "none":
            callback_data = None
        
        db.update_button(key, callback_data=callback_data)
        await state.clear()
        await message.answer(f"✅ Callback для кнопки «{key}» обновлен!")
        await admin_edit_button(message)

    @dp.callback_query(F.data.startswith("btn_url:"))
    async def admin_btn_url(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_url)
        
        await callback.message.answer(
            f"🔗 Введите URL для кнопки «{key}»:\n"
            f"Пример: <code>https://t.me/username</code>\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_button_url)
    async def admin_btn_url_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        url = message.text.strip()
        if url.lower() == "none":
            url = None
        
        db.update_button(key, url=url)
        await state.clear()
        await message.answer(f"✅ URL для кнопки «{key}» обновлен!")
        await admin_edit_button(message)

    @dp.callback_query(F.data.startswith("btn_copy:"))
    async def admin_btn_copy(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_button_copy)
        
        await callback.message.answer(
            f"📋 Введите текст для копирования для кнопки «{key}»:\n"
            f"Пример: <code>@username</code>\n\n"
            f"Или отправьте <b>none</b> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_button_copy)
    async def admin_btn_copy_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        copy_text = message.text.strip()
        if copy_text.lower() == "none":
            copy_text = None
        
        db.update_button(key, copy_text=copy_text)
        await state.clear()
        await message.answer(f"✅ Copy текст для кнопки «{key}» обновлен!")
        await admin_edit_button(message)

    @dp.callback_query(F.data.startswith("btn_delete:"))
    async def admin_btn_delete(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        
        await callback.message.edit_text(
            f"⚠️ <b>Удалить кнопку «{key}»?</b>\n\n"
            f"Это действие нельзя отменить!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{key}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"edit_button:{key}")]
            ])
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("confirm_delete:"))
    async def admin_confirm_delete(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        db.delete_button(key)
        
        await callback.answer("🗑️ Кнопка удалена!")
        await admin_buttons(callback)

    # ==================================================================================
    # CALLBACK - ADMIN HOME
    # ==================================================================================
    
    @dp.callback_query(F.data == "admin_home")
    async def callback_admin_home(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await cmd_admin(callback.message)
        await callback.answer()

# ======================================================================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ
# ======================================================================================

def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05

async def update_loop(db: Database, clock: NicknameClock, tz: timezone) -> None:
    logger.info("⏰ Запущена задача обновления времени в никах")
    
    while True:
        try:
            delay = seconds_until_next_minute(tz)
            await asyncio.sleep(delay)
            
            # Обновляем ники
            for row in db.get_enabled_users():
                user_id = row["user_id"]
                
                # Проверяем подписку
                if not db.is_subscribed(user_id):
                    db.set_enabled(user_id, False)
                    await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                    continue
                
                await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
                
        except Exception as e:
            logger.error(f"Ошибка в update_loop: {e}")
            await asyncio.sleep(5)

# ======================================================================================
# ЗАПУСК
# ======================================================================================

async def main() -> None:
    logger.info(f"🚀 Запуск {BOT_NAME} v{VERSION}")
    
    settings = Settings.from_env()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    db = Database(settings.db_path)
    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours)
    
    me = await bot.get_me()
    logger.info(f"🤖 Бот: @{me.username} (ID: {me.id})")
    logger.info(f"👑 Админы: {settings.admin_ids}")
    
    register_handlers(dp, db, clock, me.username, settings, bot)
    
    # Запускаем фоновую задачу
    asyncio.create_task(update_loop(db, clock, tz))
    
    logger.info("✅ Бот готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())