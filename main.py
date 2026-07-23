from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Dict, Any, List

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
# КОНСТАНТЫ
# ---------------------------------------------------------------------------

SUBSCRIPTION_STARS = 25
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
SUBSCRIPTION_PAYLOAD = "timenick_subscription"

BUTTON_STYLES = ("primary", "danger", "success")
BUTTON_STYLE_NAMES = {
    "primary": "🔵 Синий",
    "danger": "🔴 Красный", 
    "success": "🟢 Зеленый",
    None: "⚪️ По умолчанию"
}

# ---------------------------------------------------------------------------
# НАСТРОЙКИ
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
        
        bot_token = os.environ.get("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set!")
        
        admin_ids_raw = os.environ.get("ADMIN_IDS", "")
        admin_ids = tuple(
            int(x.strip()) for x in admin_ids_raw.replace(" ", "").split(",") if x.strip()
        )
        
        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
            db_path=os.environ.get("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.environ.get("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
        )

# ---------------------------------------------------------------------------
# БАЗА ДАННЫХ
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
            
            # Таблица для кнопок
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
                    callback_data TEXT
                )
            """)
            
            # Добавляем недостающие колонки
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            if "is_muted" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_muted INTEGER NOT NULL DEFAULT 0")
            if "subscription_until" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN subscription_until TEXT")
            if "started_at" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN started_at TEXT")
            if "username" not in existing_cols:
                conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
            
            # Инициализация кнопок
            default_buttons = {
                "toggle_on": {
                    "label": "Включить время",
                    "style": "success",
                    "callback_data": "toggle_on",
                    "row_order": 1
                },
                "toggle_off": {
                    "label": "Выключить время",
                    "style": "danger",
                    "callback_data": "toggle_off",
                    "row_order": 1
                },
                "connect": {
                    "label": "Подключить бота",
                    "style": "primary",
                    "callback_data": None,
                    "row_order": 2
                },
                "copy": {
                    "label": "Скопировать юзернейм",
                    "style": None,
                    "callback_data": None,
                    "row_order": 2
                },
                "pay": {
                    "label": "⭐ Оплатить подписку",
                    "style": "success",
                    "callback_data": "pay_subscription",
                    "row_order": 3
                },
                "unmute": {
                    "label": "🔊 Размутить",
                    "style": "primary",
                    "callback_data": "unmute_me",
                    "row_order": 4
                },
                "mute_help": {
                    "label": "ℹ️ Помощь по муту",
                    "style": None,
                    "callback_data": "mute_help",
                    "row_order": 4
                },
                "admin_back": {
                    "label": "⬅️ Назад",
                    "style": None,
                    "callback_data": "admin_home",
                    "row_order": 99
                },
                "admin_stats": {
                    "label": "📊 Статистика",
                    "style": None,
                    "callback_data": "admin_stats",
                    "row_order": 10
                },
                "admin_users": {
                    "label": "👥 Пользователи",
                    "style": None,
                    "callback_data": "admin_users",
                    "row_order": 11
                },
                "admin_buttons": {
                    "label": "🎨 Управление кнопками",
                    "style": None,
                    "callback_data": "admin_buttons",
                    "row_order": 12
                },
                "admin_give_sub": {
                    "label": "⭐ Выдать подписку",
                    "style": "success",
                    "callback_data": "admin_give_sub",
                    "row_order": 13
                },
                "admin_mutes": {
                    "label": "🔇 Управление мутами",
                    "style": "danger",
                    "callback_data": "admin_mutes",
                    "row_order": 14
                }
            }
            
            for key, config in default_buttons.items():
                conn.execute("""
                    INSERT OR IGNORE INTO buttons 
                    (button_key, label, style, callback_data, row_order, is_visible)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (key, config["label"], config["style"], config["callback_data"], config["row_order"]))

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

    def get_all_muted(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM muted_users ORDER BY muted_at DESC
            """).fetchall()

    # --- Кнопки ---
    def get_button(self, key: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM buttons WHERE button_key = ?", (key,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown button key: {key}")
            return row

    def get_all_buttons(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM buttons 
                ORDER BY row_order, button_key
            """).fetchall()

    def get_visible_buttons(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM buttons 
                WHERE is_visible = 1
                ORDER BY row_order, button_key
            """).fetchall()

    def update_button(self, key: str, **kwargs) -> None:
        with self.connect() as conn:
            fields = []
            values = []
            for field, value in kwargs.items():
                if field in ['label', 'style', 'icon_custom_emoji_id', 'emoji_prefix', 
                           'emoji_suffix', 'callback_data']:
                    fields.append(f"{field} = ?")
                    values.append(value)
                elif field in ['is_visible', 'row_order']:
                    fields.append(f"{field} = ?")
                    values.append(int(value))
            
            if fields:
                values.append(key)
                conn.execute(f"""
                    UPDATE buttons SET {', '.join(fields)} WHERE button_key = ?
                """, values)

    def delete_button(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM buttons WHERE button_key = ?", (key,))

    def create_button(self, key: str, label: str, style: Optional[str] = None,
                     callback_data: Optional[str] = None, row_order: int = 0) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO buttons (button_key, label, style, callback_data, row_order, is_visible)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (key, label, style, callback_data, row_order))

# ---------------------------------------------------------------------------
# ЧАСЫ В НИКЕ
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
# СОЗДАНИЕ КЛАВИАТУР
# ---------------------------------------------------------------------------

class KeyboardBuilder:
    def __init__(self, db: Database):
        self.db = db

    def _build_button(self, button_data: sqlite3.Row, 
                     callback_data: Optional[str] = None,
                     url: Optional[str] = None,
                     copy_text: Optional[CopyTextButton] = None) -> InlineKeyboardButton:
        """Создает кнопку с учетом всех настроек"""
        text = button_data["label"]
        
        # Добавляем префикс/суффикс эмодзи
        if button_data["emoji_prefix"]:
            text = f"{button_data['emoji_prefix']} {text}"
        if button_data["emoji_suffix"]:
            text = f"{text} {button_data['emoji_suffix']}"
        
        kwargs = {"text": text}
        
        if button_data["style"] in BUTTON_STYLES:
            kwargs["style"] = button_data["style"]
        
        if button_data["icon_custom_emoji_id"]:
            kwargs["icon_custom_emoji_id"] = button_data["icon_custom_emoji_id"]
        
        # Определяем callback_data
        final_callback = callback_data or button_data["callback_data"]
        
        if final_callback is not None:
            kwargs["callback_data"] = final_callback
        if url is not None:
            kwargs["url"] = url
        if copy_text is not None:
            kwargs["copy_text"] = copy_text
        
        return InlineKeyboardButton(**kwargs)

    def build_keyboard(self, button_keys: List[str], 
                      row_size: int = 1,
                      extra_buttons: Optional[List[Dict]] = None) -> InlineKeyboardMarkup:
        """Строит клавиатуру из списка ключей кнопок"""
        keyboard = []
        row = []
        
        for key in button_keys:
            try:
                button_data = self.db.get_button(key)
                btn = self._build_button(button_data)
                row.append(btn)
                
                if len(row) >= row_size:
                    keyboard.append(row)
                    row = []
            except KeyError:
                continue
        
        if row:
            keyboard.append(row)
        
        # Добавляем дополнительные кнопки
        if extra_buttons:
            for extra in extra_buttons:
                keyboard.append([InlineKeyboardButton(**extra)])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def build_dynamic_keyboard(self, db: Database, 
                              categories: Dict[str, List[str]],
                              current_state: Optional[str] = None) -> InlineKeyboardMarkup:
        """Строит динамическую клавиатуру по категориям"""
        keyboard = []
        
        for category, keys in categories.items():
            # Заголовок категории (как текст, не кнопка)
            # keyboard.append([InlineKeyboardButton(text=category, callback_data="ignore")])
            
            row = []
            for key in keys:
                try:
                    button_data = db.get_button(key)
                    if button_data["is_visible"]:
                        row.append(self._build_button(button_data))
                except KeyError:
                    continue
            
            if row:
                keyboard.append(row)
        
        # Добавляем кнопку назад, если есть состояние
        if current_state:
            try:
                back_btn = db.get_button("admin_back")
                keyboard.append([self._build_button(back_btn)])
            except KeyError:
                pass
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ---------------------------------------------------------------------------
# СОСТОЯНИЯ АДМИН-ПАНЕЛИ
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    # Основные состояния
    main_menu = State()
    buttons_menu = State()
    button_edit = State()
    users_menu = State()
    mutes_menu = State()
    
    # Состояния ввода
    waiting_label = State()
    waiting_emoji = State()
    waiting_emoji_id = State()
    waiting_give_sub = State()
    waiting_new_button_key = State()
    waiting_new_button_label = State()
    waiting_callback_data = State()
    waiting_user_id = State()
    waiting_mute_user = State()
    waiting_unmute_user = State()
    waiting_row_order = State()

# ---------------------------------------------------------------------------
# ОБРАБОТЧИКИ
# ---------------------------------------------------------------------------

def register_handlers(dp: Dispatcher, db: Database, clock: NicknameClock, 
                      bot_username: str, settings: Settings, bot: Bot) -> None:
    
    keyboard_builder = KeyboardBuilder(db)
    
    # ==================== ОСНОВНЫЕ КОМАНДЫ ====================
    
    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        user_id = message.from_user.id
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username)
        
        row = db.get_user(user_id)
        is_subscribed = db.is_subscribed(user_id)
        is_connected = bool(row and row["business_connection_id"])
        is_enabled = bool(row and row["enabled"]) if row else False
        
        if not is_subscribed:
            # Клавиатура с кнопкой оплаты
            pay_buttons = ["pay"]
            keyboard = keyboard_builder.build_keyboard(pay_buttons)
            
            await message.answer(
                "🌟 <b>Добро пожаловать в TimeNick!</b>\n\n"
                "Бот показывает текущее время в вашем бизнес-аккаунте.\n"
                f"Стоимость подписки: {SUBSCRIPTION_STARS} ⭐ в месяц.\n\n"
                "Нажмите кнопку ниже для оплаты:",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return
        
        if not is_connected:
            # Клавиатура для подключения
            connect_buttons = ["connect", "copy"]
            keyboard = keyboard_builder.build_keyboard(connect_buttons)
            
            await message.answer(
                "🔗 <b>Подключите бота</b>\n\n"
                "1. Нажмите <b>Подключить</b>\n"
                "2. Нажмите <b>Скопировать</b>\n"
                "3. Перейдите в <b>Автоматизация чатов</b>\n"
                "4. Вставьте скопированный текст и нажмите <b>Добавить</b>\n"
                "5. Разрешите <b>Управление профилем</b>",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return
        
        # Главное меню
        main_buttons = ["toggle_on", "toggle_off"] if is_enabled else ["toggle_on"]
        main_buttons.append("mute_help")
        
        keyboard = keyboard_builder.build_keyboard(
            main_buttons if is_subscribed else ["pay"]
        )
        
        status = "🟢 Включено" if is_enabled else "🔴 Выключено"
        await message.answer(
            f"<b>Статус:</b> {status}\n\n"
            "Управляйте ботом с помощью кнопок ниже:",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username)
        
        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            if not db.is_subscribed(user_id):
                try:
                    pay_buttons = ["pay"]
                    keyboard = keyboard_builder.build_keyboard(pay_buttons)
                    await connection.bot.send_message(user_id,
                        "⭐ <b>Требуется подписка</b>\n\n"
                        f"Оплатите {SUBSCRIPTION_STARS} ⭐ для использования бота.",
                        parse_mode="HTML",
                        reply_markup=keyboard
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

    # ==================== ОПЛАТА ====================
    
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

    # ==================== УПРАВЛЕНИЕ ВРЕМЕНЕМ ====================
    
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
        
        main_buttons = ["toggle_off", "mute_help"]
        keyboard = keyboard_builder.build_keyboard(main_buttons)
        
        await callback.message.edit_text(
            "✅ <b>Время в нике включено</b>\n\n"
            "Теперь в фамилии будет отображаться текущее время.",
            parse_mode="HTML",
            reply_markup=keyboard
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
        
        main_buttons = ["toggle_on", "mute_help"]
        keyboard = keyboard_builder.build_keyboard(main_buttons)
        
        await callback.message.edit_text(
            "❌ <b>Время в нике выключено</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    # ==================== СИСТЕМА МУТА ====================
    
    @dp.message(Command("mute"))
    async def handle_mute(message: Message, command: CommandObject) -> None:
        user_id = message.from_user.id
        
        if not db.is_subscribed(user_id):
            await message.answer("❌ Требуется активная подписка для использования мута.")
            return
        
        if not command.args:
            await message.answer(
                "❌ Укажите пользователя.\n"
                "Пример: <code>.mute @username</code> или <code>.mute 123456789</code>",
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
            await message.answer("❌ Пользователь не найден в базе бота.")
            return
        
        if target_id == user_id:
            await message.answer("❌ Нельзя замутить самого себя.")
            return
        
        db.mute_user(user_id, target_id)
        
        is_target_subscribed = db.is_subscribed(target_id)
        
        await message.answer(
            f"🔇 <b>Пользователь замучен!</b>\n\n"
            f"ID: <code>{target_id}</code>\n"
            f"Теперь все сообщения от этого пользователя будут удаляться.\n\n"
            f"Для размута используйте: <code>.unmute {target_id}</code>",
            parse_mode="HTML"
        )
        
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
        user_id = callback.from_user.id
        
        if not db.is_muted(user_id):
            await callback.answer("✅ Вы не замучены", show_alert=True)
            return
        
        muters = db.get_muters(user_id)
        if not muters:
            await callback.answer("❌ Ошибка", show_alert=True)
            return
        
        for m in muters:
            db.unmute_user(m["muter_id"], user_id)
        
        main_buttons = ["toggle_on", "mute_help"]
        keyboard = keyboard_builder.build_keyboard(main_buttons)
        
        await callback.message.edit_text(
            "🔊 <b>Вы размучены!</b>\n\n"
            "Теперь вы можете писать в чаты.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer("✅ Вы размучены")

    @dp.callback_query(F.data == "mute_help")
    async def handle_mute_help(callback: CallbackQuery) -> None:
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
        
        buttons = []
        if is_muted:
            buttons.append("unmute")
        
        keyboard = keyboard_builder.build_keyboard(buttons)
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    @dp.message(F.chat.type == "private")
    async def handle_muted_messages(message: Message) -> None:
        user_id = message.from_user.id
        
        if db.is_muted(user_id):
            try:
                await message.delete()
                logger.info(f"Deleted message from muted user {user_id}")
            except Exception as e:
                logger.error(f"Failed to delete message from {user_id}: {e}")

    # ==================== АДМИН-ПАНЕЛЬ ====================
    
    def is_admin(user_id: int) -> bool:
        return user_id in settings.admin_ids

    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        if not is_admin(message.from_user.id):
            await message.answer("❌ У вас нет доступа к админ-панели!")
            return
        
        # Главное меню админа
        admin_buttons = ["admin_stats", "admin_users", "admin_buttons", 
                        "admin_give_sub", "admin_mutes", "admin_back"]
        keyboard = keyboard_builder.build_keyboard(admin_buttons)
        
        await message.answer(
            "👑 <b>Админ-панель</b>\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data == "admin_home")
    async def admin_home(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        admin_buttons = ["admin_stats", "admin_users", "admin_buttons", 
                        "admin_give_sub", "admin_mutes", "admin_back"]
        keyboard = keyboard_builder.build_keyboard(admin_buttons)
        
        await callback.message.edit_text(
            "👑 <b>Админ-панель</b>\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    # ==================== СТАТИСТИКА ====================
    
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
        
        text = (
            "📊 <b>Статистика</b>\n\n"
            f"👥 Всего пользователей: <b>{total}</b>\n"
            f"⭐ Активных подписок: <b>{subscribed}</b>\n"
            f"🔗 Подключили бота: <b>{connected}</b>\n"
            f"🟢 Включена функция: <b>{enabled}</b>\n"
            f"🔇 Замученных: <b>{muted}</b>\n"
        )
        
        keyboard = keyboard_builder.build_keyboard(["admin_back"])
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    # ==================== ПОЛЬЗОВАТЕЛИ ====================
    
    @dp.callback_query(F.data == "admin_users")
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        users = db.get_all_users()
        text = "👥 <b>Список пользователей</b>\n\n"
        
        for i, u in enumerate(users[:20], 1):
            uname = f"@{u['username']}" if u["username"] else "—"
            sub = "✅" if db.is_subscribed(u["user_id"]) else "❌"
            muted = "🔇" if u["is_muted"] else "🔊"
            text += f"{i}. <code>{u['user_id']}</code> {uname} {muted} {sub}\n"
        
        if len(users) > 20:
            text += f"\n... и еще {len(users) - 20} пользователей"
        
        keyboard = keyboard_builder.build_keyboard(["admin_back"])
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    # ==================== УПРАВЛЕНИЕ КНОПКАМИ ====================
    
    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons_menu(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        buttons = db.get_all_buttons()
        text = "🎨 <b>Управление кнопками</b>\n\n"
        text += "Выберите кнопку для редактирования:\n\n"
        
        for btn in buttons:
            text += f"• <b>{btn['button_key']}</b>: {btn['label']}"
            if btn['style']:
                text += f" [{btn['style']}]"
            if btn['is_visible']:
                text += " ✅"
            else:
                text += " ❌"
            text += "\n"
        
        # Создаем клавиатуру с кнопками
        keyboard_buttons = []
        for btn in buttons:
            keyboard_buttons.append([InlineKeyboardButton(
                text=f"{btn['label']}",
                callback_data=f"edit_btn:{btn['button_key']}"
            )])
        
        keyboard_buttons.append([InlineKeyboardButton(
            text="➕ Создать новую кнопку",
            callback_data="admin_create_button"
        )])
        keyboard_buttons.append([InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="admin_home"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("edit_btn:"))
    async def edit_button(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        btn = db.get_button(key)
        
        text = (
            f"🔧 <b>Редактирование кнопки: {key}</b>\n\n"
            f"📝 Текст: {btn['label']}\n"
            f"🎨 Стиль: {BUTTON_STYLE_NAMES.get(btn['style'], 'по умолчанию')}\n"
            f"🔢 Порядок: {btn['row_order']}\n"
            f"📊 Видимость: {'✅' if btn['is_visible'] else '❌'}\n"
            f"🎯 Callback: {btn['callback_data'] or 'нет'}\n"
            f"✨ Emoji ID: {btn['icon_custom_emoji_id'] or 'нет'}\n"
            f"📎 Префикс: {btn['emoji_prefix'] or 'нет'}\n"
            f"📎 Суффикс: {btn['emoji_suffix'] or 'нет'}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"btn_label:{key}")],
            [InlineKeyboardButton(text="🎨 Изменить стиль", callback_data=f"btn_style:{key}")],
            [InlineKeyboardButton(text="🔄 Изменить порядок", callback_data=f"btn_order:{key}")],
            [InlineKeyboardButton(text="👁️ Переключить видимость", callback_data=f"btn_visible:{key}")],
            [InlineKeyboardButton(text="✨ Установить emoji ID", callback_data=f"btn_emoji:{key}")],
            [InlineKeyboardButton(text="📎 Установить префикс", callback_data=f"btn_prefix:{key}")],
            [InlineKeyboardButton(text="📎 Установить суффикс", callback_data=f"btn_suffix:{key}")],
            [InlineKeyboardButton(text="🎯 Изменить callback", callback_data=f"btn_callback:{key}")],
            [InlineKeyboardButton(text="🗑️ Удалить кнопку", callback_data=f"btn_delete:{key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")]
        ])
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("btn_label:"))
    async def edit_button_label(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_label)
        
        await callback.message.answer(
            f"✏️ Введите новый текст для кнопки «{key}»:",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_label)
    async def finish_edit_label(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        db.update_button(key, label=message.text.strip())
        await state.clear()
        
        await message.answer(
            f"✅ Текст кнопки «{key}» обновлен на: {message.text}",
            parse_mode="HTML"
        )
        
        # Показываем меню редактирования
        btn = db.get_button(key)
        text = (
            f"🔧 <b>Редактирование кнопки: {key}</b>\n\n"
            f"📝 Текст: {btn['label']}\n"
            f"🎨 Стиль: {BUTTON_STYLE_NAMES.get(btn['style'], 'по умолчанию')}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"btn_label:{key}")],
            [InlineKeyboardButton(text="🎨 Изменить стиль", callback_data=f"btn_style:{key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")]
        ])
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.callback_query(F.data.startswith("btn_style:"))
    async def edit_button_style(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 Primary", callback_data=f"set_style:{key}:primary")],
            [InlineKeyboardButton(text="🔴 Danger", callback_data=f"set_style:{key}:danger")],
            [InlineKeyboardButton(text="🟢 Success", callback_data=f"set_style:{key}:success")],
            [InlineKeyboardButton(text="⚪️ По умолчанию", callback_data=f"set_style:{key}:none")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"edit_btn:{key}")]
        ])
        
        await callback.message.edit_text(
            f"🎨 Выберите стиль для кнопки «{key}»:",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_style:"))
    async def finish_edit_style(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        _, key, style = callback.data.split(":", 2)
        style = None if style == "none" else style
        
        db.update_button(key, style=style)
        
        await callback.answer("✅ Стиль обновлен!")
        
        # Возвращаемся к редактированию
        await edit_button(callback)

    @dp.callback_query(F.data.startswith("btn_visible:"))
    async def toggle_button_visibility(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        btn = db.get_button(key)
        db.update_button(key, is_visible=not btn["is_visible"])
        
        await callback.answer("✅ Видимость изменена!")
        await edit_button(callback)

    @dp.callback_query(F.data.startswith("btn_order:"))
    async def edit_button_order(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_row_order)
        
        await callback.message.answer(
            f"🔄 Введите номер порядка для кнопки «{key}» (число):\n\n"
            f"Чем меньше число, тем выше кнопка будет располагаться.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_row_order)
    async def finish_edit_order(message: Message, state: FSMContext) -> None:
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
            
            await message.answer(
                f"✅ Порядок кнопки «{key}» изменен на: {order}",
                parse_mode="HTML"
            )
            
            btn = db.get_button(key)
            text = (
                f"🔧 <b>Редактирование кнопки: {key}</b>\n\n"
                f"📝 Текст: {btn['label']}\n"
                f"🔢 Порядок: {btn['row_order']}"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_buttons")]
            ])
            
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        except ValueError:
            await message.answer("❌ Введите число!")

    @dp.callback_query(F.data.startswith("btn_delete:"))
    async def delete_button(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{key}")],
            [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_btn:{key}")]
        ])
        
        await callback.message.edit_text(
            f"⚠️ <b>Удалить кнопку «{key}»?</b>\n\n"
            f"Это действие нельзя отменить!",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("confirm_delete:"))
    async def confirm_delete_button(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        db.delete_button(key)
        
        await callback.answer("🗑️ Кнопка удалена!")
        
        # Возвращаемся в меню кнопок
        await admin_buttons_menu(callback)

    @dp.callback_query(F.data == "admin_create_button")
    async def create_button_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        await state.set_state(AdminStates.waiting_new_button_key)
        await callback.message.answer(
            "➕ <b>Создание новой кнопки</b>\n\n"
            "Введите уникальный ключ для кнопки (только латиница, без пробелов):\n"
            "Пример: <code>my_button</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_new_button_key)
    async def create_button_key(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        key = message.text.strip()
        if not key or " " in key:
            await message.answer("❌ Ключ не должен содержать пробелов!")
            return
        
        # Проверяем, существует ли уже такая кнопка
        try:
            db.get_button(key)
            await message.answer("❌ Кнопка с таким ключом уже существует!")
            return
        except KeyError:
            pass
        
        await state.update_data(new_button_key=key)
        await state.set_state(AdminStates.waiting_new_button_label)
        await message.answer(
            f"✅ Ключ: {key}\n\n"
            f"Теперь введите текст для кнопки:",
            parse_mode="HTML"
        )

    @dp.message(AdminStates.waiting_new_button_label)
    async def create_button_label(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("new_button_key")
        label = message.text.strip()
        
        db.create_button(key, label)
        await state.clear()
        
        await message.answer(
            f"✅ Кнопка создана!\n\n"
            f"Ключ: {key}\n"
            f"Текст: {label}\n\n"
            f"Вы можете настроить ее в меню редактирования.",
            parse_mode="HTML"
        )
        
        await admin_buttons_menu(message)

    # ==================== ВЫДАЧА ПОДПИСКИ ====================
    
    @dp.callback_query(F.data == "admin_give_sub")
    async def admin_give_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
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
        if not is_admin(message.from_user.id):
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

    # ==================== УПРАВЛЕНИЕ МУТАМИ ====================
    
    @dp.callback_query(F.data == "admin_mutes")
    async def admin_mutes(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        muted = db.get_all_muted()
        
        if not muted:
            text = "🔇 <b>Управление мутами</b>\n\n"
            text += "Нет замученных пользователей."
        else:
            text = f"🔇 <b>Замученные пользователи ({len(muted)})</b>\n\n"
            for m in muted[:20]:
                text += f"• Мутящий: <code>{m['muter_id']}</code> → Мут: <code>{m['muted_id']}</code>\n"
                text += f"  Время: {m['muted_at']}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Размутить всех", callback_data="admin_unmute_all")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]
        ])
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
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

    # ==================== ОБРАБОТЧИКИ ДЛЯ EMOJI ====================
    
    @dp.callback_query(F.data.startswith("btn_emoji:"))
    async def edit_button_emoji(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji_id)
        
        await callback.message.answer(
            f"✨ Введите Custom Emoji ID для кнопки «{key}»:\n\n"
            f"Чтобы получить ID эмодзи:\n"
            f"1. Отправьте эмодзи боту @getstickerbot\n"
            f"2. Скопируйте его ID\n\n"
            f"Или отправьте <code>none</code> чтобы убрать эмодзи.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji_id)
    async def finish_edit_emoji(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        
        data = await state.get_data()
        key = data.get("button_key")
        if not key:
            await state.clear()
            return
        
        emoji_id = message.text.strip()
        if emoji_id.lower() == "none":
            emoji_id = None
        
        db.update_button(key, icon_custom_emoji_id=emoji_id)
        await state.clear()
        
        await message.answer(
            f"✅ Custom Emoji для кнопки «{key}» обновлен!",
            parse_mode="HTML"
        )
        
        await edit_button(message)

    @dp.callback_query(F.data.startswith("btn_prefix:"))
    async def edit_button_prefix(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji)
        
        await callback.message.answer(
            f"📎 Введите эмодзи-префикс для кнопки «{key}»:\n\n"
            f"Этот эмодзи будет добавляться в начало текста кнопки.\n"
            f"Пример: ⭐\n\n"
            f"Или отправьте <code>none</code> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji)
    async def finish_edit_prefix(message: Message, state: FSMContext) -> None:
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
        
        db.update_button(key, emoji_prefix=emoji)
        await state.clear()
        
        await message.answer(
            f"✅ Префикс для кнопки «{key}» обновлен!",
            parse_mode="HTML"
        )
        
        await edit_button(message)

    @dp.callback_query(F.data.startswith("btn_suffix:"))
    async def edit_button_suffix(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji)
        
        await callback.message.answer(
            f"📎 Введите эмодзи-суффикс для кнопки «{key}»:\n\n"
            f"Этот эмодзи будет добавляться в конец текста кнопки.\n"
            f"Пример: ✨\n\n"
            f"Или отправьте <code>none</code> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("btn_callback:"))
    async def edit_button_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_callback_data)
        
        await callback.message.answer(
            f"🎯 Введите callback_data для кнопки «{key}»:\n\n"
            f"Это данные, которые будут отправляться при нажатии на кнопку.\n"
            f"Пример: <code>my_action</code>\n\n"
            f"Или отправьте <code>none</code> чтобы убрать.",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_callback_data)
    async def finish_edit_callback(message: Message, state: FSMContext) -> None:
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
        
        await message.answer(
            f"✅ Callback для кнопки «{key}» обновлен!",
            parse_mode="HTML"
        )
        
        await edit_button(message)

    # ==================== ВСПОМОГАТЕЛЬНЫЕ КОМАНДЫ ====================
    
    @dp.message(Command("check"))
    async def check_admin(message: Message) -> None:
        user_id = message.from_user.id
        is_admin_user = is_admin(user_id)
        
        await message.answer(
            f"🔍 <b>Проверка</b>\n\n"
            f"Ваш ID: <code>{user_id}</code>\n"
            f"Вы админ? {'✅ Да' if is_admin_user else '❌ Нет'}\n"
            f"Список админов: <code>{settings.admin_ids}</code>",
            parse_mode="HTML"
        )

# ---------------------------------------------------------------------------
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ
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
# ЗАПУСК
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
    register_handlers(dp, db, clock, me.username, settings, bot)
    
    asyncio.create_task(run_update_loop(db, clock, tz))
    
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())