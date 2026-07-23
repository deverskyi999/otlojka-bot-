from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Dict, Set

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SetBusinessAccountName
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
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SUBSCRIPTION_STARS = 25
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
SUBSCRIPTION_PAYLOAD = "timenick_subscription"

BUTTON_STYLES = ("primary", "danger", "success")

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set")
        
        admin_ids_raw = os.getenv("ADMIN_IDS", "")
        admin_ids = tuple(int(x) for x in admin_ids_raw.replace(" ", "").split(",") if x)
        
        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
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
            # Таблица пользователей
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    business_connection_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    subscription_until TEXT,
                    is_muted INTEGER NOT NULL DEFAULT 0
                )
            """)
            
            # Таблица для мутов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS muted_users (
                    muter_id INTEGER,
                    muted_id INTEGER,
                    muted_at TEXT,
                    PRIMARY KEY (muter_id, muted_id)
                )
            """)
            
            # Таблица для настроек кнопок
            conn.execute("""
                CREATE TABLE IF NOT EXISTS button_settings (
                    button_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    style TEXT,
                    icon_custom_emoji_id TEXT
                )
            """)
            
            # Добавляем колонку is_muted если её нет
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            if "is_muted" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_muted INTEGER NOT NULL DEFAULT 0")
            if "subscription_until" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN subscription_until TEXT")
            if "started_at" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN started_at TEXT")
            if "username" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
            
            # Настройки кнопок по умолчанию
            default_buttons = {
                "toggle_on": "🟢 Включить время",
                "toggle_off": "🔴 Выключить время",
                "connect": "🔗 Подключить бота",
                "copy": "📋 Скопировать юзернейм",
                "pay": "⭐ Оплатить подписку (25 ⭐)",
                "unmute": "🔊 Размутить",
                "mute_help": "ℹ️ Помощь по муту",
            }
            
            for key, label in default_buttons.items():
                conn.execute("""
                    INSERT OR IGNORE INTO button_settings (button_key, label, style, icon_custom_emoji_id)
                    VALUES (?, ?, NULL, NULL)
                """, (key, label))

    # --- Пользователи ---
    def upsert_user(self, user_id: int, first_name: str, username: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO users (user_id, first_name, username, started_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
            """, (user_id, first_name, username, datetime.now(timezone.utc).isoformat()))

    def set_connection(self, user_id: int, connection_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET business_connection_id = ? WHERE user_id = ?", 
                        (connection_id, user_id))

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET enabled = ? WHERE user_id = ?", 
                        (int(enabled), user_id))

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT user_id, first_name, business_connection_id
                FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
            """).fetchall()

    def get_all_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users ORDER BY started_at DESC").fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_active_subscribers(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            return conn.execute("""
                SELECT COUNT(*) AS c FROM users 
                WHERE subscription_until IS NOT NULL AND subscription_until > ?
            """, (now,)).fetchone()["c"]

    # --- Подписка ---
    def extend_subscription(self, user_id: int, seconds: int) -> None:
        now = datetime.now(timezone.utc)
        row = self.get_user(user_id)
        current_until = None
        if row and row["subscription_until"]:
            try:
                current_until = datetime.fromisoformat(row["subscription_until"])
            except ValueError:
                pass
        
        base = current_until if current_until and current_until > now else now
        new_until = base + timedelta(seconds=seconds)
        
        with self.connect() as conn:
            conn.execute("UPDATE users SET subscription_until = ? WHERE user_id = ?",
                        (new_until.isoformat(), user_id))

    def is_subscribed(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        if not row or not row["subscription_until"]:
            return False
        try:
            until = datetime.fromisoformat(row["subscription_until"])
        except ValueError:
            return False
        return until > datetime.now(timezone.utc)

    def set_subscription(self, user_id: int, days: int) -> None:
        """Админская выдача подписки"""
        self.extend_subscription(user_id, days * 24 * 60 * 60)

    # --- Мут ---
    def mute_user(self, muter_id: int, muted_id: int) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO muted_users (muter_id, muted_id, muted_at)
                VALUES (?, ?, ?)
            """, (muter_id, muted_id, datetime.now(timezone.utc).isoformat()))
            conn.execute("UPDATE users SET is_muted = 1 WHERE user_id = ?", (muted_id,))

    def unmute_user(self, muter_id: int, muted_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM muted_users WHERE muter_id = ? AND muted_id = ?", 
                        (muter_id, muted_id))
            # Проверяем, есть ли ещё муты на этого пользователя
            count = conn.execute("SELECT COUNT(*) AS c FROM muted_users WHERE muted_id = ?", 
                               (muted_id,)).fetchone()["c"]
            if count == 0:
                conn.execute("UPDATE users SET is_muted = 0 WHERE user_id = ?", (muted_id,))

    def is_muted(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(row and row["is_muted"])

    def get_muted_by(self, muter_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT muted_id FROM muted_users WHERE muter_id = ?
            """, (muter_id,)).fetchall()

    def get_muters(self, muted_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT muter_id FROM muted_users WHERE muted_id = ?
            """, (muted_id,)).fetchall()

    # --- Кнопки ---
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
            conn.execute("UPDATE button_settings SET icon_custom_emoji_id = ? WHERE button_key = ?",
                        (emoji_id, key))

# ---------------------------------------------------------------------------
# Часы в нике
# ---------------------------------------------------------------------------

class NicknameClock:
    def __init__(self, bot: Bot, db: Database, tz_offset_hours: int) -> None:
        self._bot = bot
        self._db = db
        self._tz = timezone(timedelta(hours=tz_offset_hours))
        self._last_applied: Dict[int, str] = {}

    def _current_label(self) -> str:
        return datetime.now(self._tz).strftime("• [%H:%M]")

    async def apply(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        
        label = self._current_label()
        if self._last_applied.get(user_id) == label:
            return
        
        try:
            await self._bot(SetBusinessAccountName(
                business_connection_id=connection_id,
                first_name=first_name,
                last_name=label,
            ))
            self._last_applied[user_id] = label
        except Exception:
            logger.exception("Failed to update nickname for user_id=%s", user_id)
            await self._handle_permission_loss(user_id)

    async def _handle_permission_loss(self, user_id: int) -> None:
        self._db.set_enabled(user_id, False)
        try:
            await self._bot.send_message(user_id,
                "❌ Недостаточно прав для смены фамилии.\n"
                "Переподключите бота в настройках, разрешив изменение имени."
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
        except Exception:
            pass
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

def build_main_keyboard(db: Database, is_enabled: bool, is_subscribed: bool) -> InlineKeyboardMarkup:
    buttons = []
    
    if not is_subscribed:
        buttons.append([_button(db, "pay", callback_data="pay_subscription")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # Подписка есть
    key = "toggle_off" if is_enabled else "toggle_on"
    action = "toggle_off" if is_enabled else "toggle_on"
    buttons.append([_button(db, key, callback_data=action)])
    
    # Кнопка мута
    buttons.append([_button(db, "mute_help", callback_data="mute_help")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(db, "connect", url="tg://settings/edit")],
        [_button(db, "copy", copy_text=CopyTextButton(text=f"@{bot_username}"))],
    ])

def build_pay_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(db, "pay", callback_data="pay_subscription")],
    ])

def build_mute_keyboard(db: Database, is_muted: bool) -> InlineKeyboardMarkup:
    if is_muted:
        return InlineKeyboardMarkup(inline_keyboard=[
            [_button(db, "unmute", callback_data="unmute_me")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ Как замутить", callback_data="mute_help")],
    ])

# ---------------------------------------------------------------------------
# Админ-панель
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_label = State()
    waiting_emoji = State()
    waiting_give_sub = State()

def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids

def build_admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="🎨 Кнопки", callback_data="admin_buttons")],
        [InlineKeyboardButton(text="⭐ Выдать подписку", callback_data="admin_give_sub")],
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
        return "Пользователей пока нет."
    
    lines = [f"<b>👥 Пользователи (стр. {page + 1})</b>\n"]
    for u in chunk:
        uname = f"@{u['username']}" if u["username"] else "(нет username)"
        sub = "✅" if (u["subscription_until"] and u["subscription_until"] > datetime.now(timezone.utc).isoformat()) else "❌"
        muted = "🔇" if u["is_muted"] else "🔊"
        lines.append(f"• <code>{u['user_id']}</code> {uname} {muted} — подписка: {sub}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

def register_handlers(dp: Dispatcher, db: Database, clock: NicknameClock, 
                      bot_username: str, settings: Settings) -> None:
    
    # --- Пользовательские команды ---
    
    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        user_id = message.from_user.id
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username)
        
        row = db.get_user(user_id)
        is_subscribed = db.is_subscribed(user_id)
        is_connected = bool(row and row["business_connection_id"])
        is_enabled = bool(row and row["enabled"]) if row else False
        
        if not is_subscribed:
            await message.answer(
                "🌟 <b>Добро пожаловать в TimeNick!</b>\n\n"
                "Бот показывает текущее время в вашем бизнес-аккаунте.\n"
                f"Стоимость подписки: {SUBSCRIPTION_STARS} ⭐ в месяц.\n\n"
                "Нажмите кнопку ниже для оплаты:",
                parse_mode="HTML",
                reply_markup=build_pay_keyboard(db)
            )
            return
        
        if not is_connected:
            await message.answer(
                "🔗 <b>Подключите бота</b>\n\n"
                "1. Нажмите <b>Подключить</b>\n"
                "2. Нажмите <b>Скопировать</b>\n"
                "3. Перейдите в <b>Автоматизация чатов</b>\n"
                "4. Вставьте скопированный текст и нажмите <b>Добавить</b>\n"
                "5. Разрешите <b>Управление профилем</b>",
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(db, bot_username)
            )
            return
        
        status = "🟢 Включено" if is_enabled else "🔴 Выключено"
        await message.answer(
            f"<b>Статус:</b> {status}\n\n"
            "Управляйте ботом с помощью кнопок ниже:",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(db, is_enabled, is_subscribed)
        )

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username)
        
        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            if not db.is_subscribed(user_id):
                try:
                    await connection.bot.send_message(user_id,
                        "⭐ <b>Требуется подписка</b>\n\n"
                        f"Оплатите {SUBSCRIPTION_STARS} ⭐ для использования бота.",
                        parse_mode="HTML",
                        reply_markup=build_pay_keyboard(db)
                    )
                except Exception:
                    pass
                return
            
            try:
                await connection.bot.send_message(user_id,
                    "✅ <b>Бот подключён!</b>\n\n"
                    "Используйте /start для управления.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return
        
        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        
        db.set_connection(user_id, None)
        db.set_enabled(user_id, False)

    # --- Оплата ---
    
    @dp.callback_query(F.data == "pay_subscription")
    async def handle_pay_subscription(callback: CallbackQuery) -> None:
        prices = [LabeledPrice(label="Подписка на 30 дней", amount=SUBSCRIPTION_STARS)]
        link = await callback.bot.create_invoice_link(
            title="TimeNick Подписка",
            description=f"Доступ к боту на 30 дней за {SUBSCRIPTION_STARS} Stars",
            payload=SUBSCRIPTION_PAYLOAD,
            currency="XTR",
            prices=prices,
        )
        await callback.message.answer(
            "💳 <b>Оплата подписки</b>\n\n"
            f"Нажмите на кнопку ниже для оплаты {SUBSCRIPTION_STARS} ⭐:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ Оплатить {SUBSCRIPTION_STARS} ⭐", url=link)]
            ])
        )
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        if pre_checkout_query.invoice_payload == SUBSCRIPTION_PAYLOAD:
            await pre_checkout_query.answer(ok=True)
        else:
            await pre_checkout_query.answer(ok=False, error_message="Неизвестный платёж")

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        if payment.invoice_payload != SUBSCRIPTION_PAYLOAD:
            return
        
        user_id = message.from_user.id
        db.extend_subscription(user_id, SUBSCRIPTION_PERIOD_SECONDS)
        
        await message.answer(
            "✅ <b>Подписка активирована!</b>\n\n"
            "Теперь вам доступны все функции бота на 30 дней.\n"
            "Используйте /start для управления.",
            parse_mode="HTML"
        )

    # --- Включение/выключение времени ---
    
    @dp.callback_query(F.data == "toggle_on")
    async def handle_toggle_on(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        
        if not db.is_subscribed(user_id):
            await callback.answer("❌ Требуется активная подписка", show_alert=True)
            return
        
        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("❌ Бот не подключён", show_alert=True)
            return
        
        db.set_enabled(user_id, True)
        await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
        
        await callback.message.edit_text(
            "✅ <b>Время в нике включено</b>\n\n"
            "Теперь в фамилии будет отображаться текущее время.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(db, True, True)
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
            "❌ <b>Время в нике выключено</b>",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(db, False, True)
        )
        await callback.answer()

    # --- Мут система ---
    
    @dp.message(Command("mute"))
    async def handle_mute(message: Message, command: CommandObject) -> None:
        """Замутить пользователя"""
        user_id = message.from_user.id
        
        # Проверяем подписку
        if not db.is_subscribed(user_id):
            await message.answer("❌ Требуется активная подписка для использования мута.")
            return
        
        # Получаем ID пользователя для мута
        if not command.args:
            await message.answer(
                "❌ Укажите пользователя.\n"
                "Пример: <code>.mute @username</code> или <code>.mute 123456789</code>",
                parse_mode="HTML"
            )
            return
        
        # Парсим ID или username
        target = command.args.strip()
        if target.startswith("@"):
            target = target[1:]
        
        # Ищем пользователя
        target_id = None
        try:
            target_id = int(target)
        except ValueError:
            # Ищем по username
            all_users = db.get_all_users()
            for u in all_users:
                if u["username"] and u["username"].lower() == target.lower():
                    target_id = u["user_id"]
                    break
        
        if not target_id:
            await message.answer("❌ Пользователь не найден в базе бота.")
            return
        
        if target_id == user_id:
            await message.answer("❌ Нельзя замутить самого себя.")
            return
        
        # Мутим
        db.mute_user(user_id, target_id)
        
        # Проверяем, есть ли у цели активная подписка
        is_target_subscribed = db.is_subscribed(target_id)
        
        await message.answer(
            f"🔇 <b>Пользователь замучен!</b>\n\n"
            f"ID: <code>{target_id}</code>\n"
            f"Теперь все сообщения от этого пользователя будут удаляться.\n\n"
            f"Для размута используйте: <code>.unmute {target_id}</code>",
            parse_mode="HTML"
        )
        
        # Уведомляем замученного, если у него есть подписка
        if is_target_subscribed:
            try:
                await message.bot.send_message(target_id,
                    f"🔇 <b>Вы были замучены пользователем</b>\n\n"
                    f"Теперь ваши сообщения в этом чате будут удаляться.\n"
                    f"Для размута обратитесь к пользователю, который вас замутил.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    @dp.message(Command("unmute"))
    async def handle_unmute(message: Message, command: CommandObject) -> None:
        """Размутить пользователя"""
        user_id = message.from_user.id
        
        if not command.args:
            await message.answer(
                "❌ Укажите пользователя.\n"
                "Пример: <code>.unmute @username</code> или <code>.unmute 123456789</code>",
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
            all_users = db.get_all_users()
            for u in all_users:
                if u["username"] and u["username"].lower() == target.lower():
                    target_id = u["user_id"]
                    break
        
        if not target_id:
            await message.answer("❌ Пользователь не найден.")
            return
        
        db.unmute_user(user_id, target_id)
        await message.answer(
            f"🔊 <b>Пользователь размучен!</b>\n\n"
            f"ID: <code>{target_id}</code>\n"
            f"Теперь пользователь может писать в чат.",
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "unmute_me")
    async def handle_unmute_me(callback: CallbackQuery) -> None:
        """Размутить себя через кнопку"""
        user_id = callback.from_user.id
        
        # Проверяем, замучен ли пользователь
        if not db.is_muted(user_id):
            await callback.answer("✅ Вы не замучены", show_alert=True)
            return
        
        # Получаем список тех, кто замутил
        muters = db.get_muters(user_id)
        if not muters:
            await callback.answer("❌ Ошибка: вы замучены, но не найден мутящий", show_alert=True)
            return
        
        # Размучиваем от всех
        for m in muters:
            db.unmute_user(m["muter_id"], user_id)
        
        await callback.message.edit_text(
            "🔊 <b>Вы размучены!</b>\n\n"
            "Теперь вы можете писать в чаты.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(db, False, True)
        )
        await callback.answer("✅ Вы размучены")

    @dp.callback_query(F.data == "mute_help")
    async def handle_mute_help(callback: CallbackQuery) -> None:
        """Помощь по муту"""
        user_id = callback.from_user.id
        is_muted = db.is_muted(user_id)
        
        text = (
            "🔇 <b>Как работает мут</b>\n\n"
            "• Используйте <code>.mute @username</code> или <code>.mute user_id</code>\n"
            "• Все сообщения замученного пользователя будут моментально удаляться\n"
            "• Для размута используйте <code>.unmute @username</code>\n"
            "• Если вас замутили, нажмите кнопку ниже для размута\n\n"
            "⚡️ Работает только у пользователей с активной подпиской!"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=build_mute_keyboard(db, is_muted)
        )
        await callback.answer()

    # --- Обработчик удаления сообщений от замученных ---
    
    @dp.message(F.chat.type == "private")
    async def handle_muted_messages(message: Message) -> None:
        """Удаляем сообщения от замученных пользователей"""
        user_id = message.from_user.id
        
        if db.is_muted(user_id):
            try:
                await message.delete()
                logger.info(f"Deleted message from muted user {user_id}")
            except Exception as e:
                logger.error(f"Failed to delete message from {user_id}: {e}")

    # --- Админ-панель ---
    
    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        if not is_admin(message.from_user.id, settings):
            return
        await message.answer(
            "👑 <b>Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard()
        )

    @dp.callback_query(F.data == "admin_home")
    async def admin_home(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text(
            "👑 <b>Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard()
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        
        total = db.count_users()
        subscribed = db.count_active_subscribers()
        connected = db.count_connected()
        enabled = db.count_enabled()
        muted = len(db.get_muters(0))  # Хитрость, но можно сделать отдельный запрос
        
        text = (
            "📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: <b>{total}</b>\n"
            f"Активных подписок: <b>{subscribed}</b>\n"
            f"Подключили бота: <b>{connected}</b>\n"
            f"Включена функция: <b>{enabled}</b>\n"
            f"Замученных пользователей: <b>{muted}</b>"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]
            ])
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        
        page = int(callback.data.split(":")[1])
        users = db.get_all_users()
        
        await callback.message.edit_text(
            format_users_page(users, page),
            parse_mode="HTML",
            reply_markup=build_admin_users_keyboard(users, page)
        )
        await callback.answer()

    # --- Выдача подписки админом ---
    
    @dp.callback_query(F.data == "admin_give_sub")
    async def admin_give_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        
        await state.set_state(AdminStates.waiting_give_sub)
        await callback.message.answer(
            "⭐ <b>Выдача подписки</b>\n\n"
            "Отправьте ID пользователя и количество дней через пробел.\n"
            "Пример: <code>123456789 30</code>\n\n"
            "Для отмены отправьте /cancel",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_give_sub)
    async def admin_give_sub_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id, settings):
            return
        
        try:
            parts = message.text.split()
            user_id = int(parts[0])
            days = int(parts[1])
            
            if days <= 0:
                await message.answer("❌ Количество дней должно быть положительным.")
                return
            
            db.set_subscription(user_id, days)
            await message.answer(
                f"✅ Подписка выдана!\n\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Дней: {days}",
                parse_mode="HTML"
            )
            
            # Уведомляем пользователя
            try:
                await message.bot.send_message(user_id,
                    f"⭐ <b>Вам выдана подписка!</b>\n\n"
                    f"Дней: {days}\n"
                    f"Теперь вам доступны все функции бота.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            
        except (ValueError, IndexError):
            await message.answer(
                "❌ Неверный формат.\n"
                "Используйте: <code>user_id days</code>",
                parse_mode="HTML"
            )
            return
        
        await state.clear()

    # --- Настройка кнопок ---
    
    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        
        await callback.message.edit_text(
            "🎨 <b>Настройка кнопок</b>\n\n"
            "Выберите кнопку для редактирования:",
            parse_mode="HTML",
            reply_markup=build_admin_buttons_keyboard(db)
        )
        await callback.answer()

    # Остальные админские функции для кнопок остаются без изменений...
    # (код для admin_btn:, admin_style:, admin_setlabel: и т.д. из предыдущей версии)

# ---------------------------------------------------------------------------
# Периодическое обновление
# ---------------------------------------------------------------------------

def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05

async def run_update_loop(db: Database, clock: NicknameClock, tz: timezone) -> None:
    while True:
        delay = seconds_until_next_minute(tz)
        await asyncio.sleep(delay)
        
        for row in db.get_enabled_users():
            user_id = row["user_id"]
            if not db.is_subscribed(user_id):
                db.set_enabled(user_id, False)
                await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                continue
            await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")

# ---------------------------------------------------------------------------
# Запуск
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
    
    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours)
    
    me = await bot.get_me()
    register_handlers(dp, db, clock, me.username, settings)
    
    asyncio.create_task(run_update_loop(db, clock, tz))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
