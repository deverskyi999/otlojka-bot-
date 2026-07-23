from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from aiogram import Bot, Dispatcher, F
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
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")

# ---------------------------------------------------------------------------
# Константы подписки
# ---------------------------------------------------------------------------

SUBSCRIPTION_STARS = 15
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60  # 30 дней
SUBSCRIPTION_PAYLOAD = "timenick_subscription"

BUTTON_STYLES = ("primary", "danger", "success")

BUTTON_KEYS = {
    "toggle_on": "🔥 Включить",
    "toggle_off": "⛔ Выключить",
    "connect": "🔗 Подключить",
    "copy": "📋 Скопировать",
    "pay": f"⭐ Оплатить подписку — {SUBSCRIPTION_STARS} Stars",
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
                "OWNER_ID is not set in .env — укажите свой Telegram user_id, "
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
                    is_admin INTEGER NOT NULL DEFAULT 0
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

            existing_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)")
            }
            for col, ddl in (
                ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
                ("started_at", "ALTER TABLE users ADD COLUMN started_at TEXT"),
                ("subscription_until", "ALTER TABLE users ADD COLUMN subscription_until TEXT"),
                ("is_admin", "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
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

    # --- пользователи -----------------------------------------------------

    def upsert_user(self, user_id: int, first_name: str, username: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, first_name, username, started_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
                """,
                (user_id, first_name, username, datetime.now(timezone.utc).isoformat()),
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

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, first_name, business_connection_id
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
                "UPDATE users SET subscription_until = ? WHERE user_id = ?",
                (new_until.isoformat(), user_id),
            )

    def is_subscribed(self, user_id: int, owner_id: int) -> bool:
        # владелец бота всегда считается подписанным
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


# ---------------------------------------------------------------------------
# Часы в нике (бизнес-функция бота)
# ---------------------------------------------------------------------------

class NicknameClock:
    def __init__(self, bot: Bot, db: Database, tz_offset_hours: int) -> None:
        self._bot = bot
        self._db = db
        self._tz = timezone(timedelta(hours=tz_offset_hours))
        self._last_applied: dict[int, str] = {}

    def _current_label(self) -> str:
        return datetime.now(self._tz).strftime("• [%H:%M]")

    async def apply(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return

        label = self._current_label()
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
# Построение клавиатур (текст/цвет/premium-emoji подтягиваются из БД)
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
        inline_keyboard=[[_button(db, "pay", callback_data="pay_subscription")]]
    )


def build_toggle_keyboard(db: Database, enabled: bool) -> InlineKeyboardMarkup:
    key = "toggle_off" if enabled else "toggle_on"
    action = "toggle_off" if enabled else "toggle_on"
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(db, key, callback_data=action)]]
    )


def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(db, "connect", url="tg://settings/edit")],
            [_button(db, "copy", copy_text=CopyTextButton(text=f"@{bot_username}"))],
        ]
    )


def build_not_connected_text() -> str:
    return (
        "<b>Бот не подключён.</b>\n\n"
        "Нажмите <b>Подключить</b>, затем <b>Скопировать</b>, "
        "далее откройте <b>Автоматизация чатов</b>, вставьте скопированный текст "
        "и нажмите <b>Добавить</b>. Разрешите <b>Управлять профилем</b>."
    )


def build_subscription_required_text() -> str:
    return (
        f"<b>Требуется подписка.</b>\n\n"
        f"Доступ к боту стоит {SUBSCRIPTION_STARS} ⭐ в месяц."
    )


# ---------------------------------------------------------------------------
# Админ-панель
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_label = State()
    waiting_emoji = State()
    waiting_grant_sub_id = State()
    waiting_grant_admin_id = State()
    waiting_revoke_admin_id = State()


def build_admin_main_keyboard(is_owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="🎨 Кнопки", callback_data="admin_buttons")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_grant_sub")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton(text="🛡 Управление админами", callback_data="admin_admins")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_admins_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Назначить админа", callback_data="admin_grant_admin")],
            [InlineKeyboardButton(text="➖ Снять админа", callback_data="admin_revoke_admin")],
            [InlineKeyboardButton(text="📋 Список админов", callback_data="admin_list_admins")],
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
        sub_mark = "✅" if (u["subscription_until"] and u["subscription_until"] > now_iso) else "—"
        admin_mark = " 🛡" if u["is_admin"] else ""
        lines.append(f"• <code>{u['user_id']}</code> {uname}{admin_mark} — подписка: {sub_mark}")
    return "\n".join(lines)


def parse_user_id(text: str) -> Optional[int]:
    text = text.strip().lstrip("@")
    if text.isdigit():
        return int(text)
    return None


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

    def is_admin(user_id: int) -> bool:
        return db.is_admin(user_id, settings.owner_id)

    # ------------------------- пользовательская часть -------------------------

    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        user_id = message.from_user.id
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username)

        if not db.is_subscribed(user_id, settings.owner_id):
            await message.answer(
                build_subscription_required_text(),
                parse_mode="HTML",
                reply_markup=build_pay_keyboard(db),
            )
            return

        row = db.get_user(user_id)
        is_connected = bool(row and row["business_connection_id"])

        if not is_connected:
            await message.answer(
                build_not_connected_text(),
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(db, bot_username),
            )
            return

        is_enabled = bool(row["enabled"])
        status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
        await message.answer(
            status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(db, is_enabled)
        )

    @dp.callback_query(F.data == "pay_subscription")
    async def handle_pay_subscription(callback: CallbackQuery) -> None:
        prices = [LabeledPrice(label=f"Подписка на 30 дней", amount=SUBSCRIPTION_STARS)]
        link = await callback.bot.create_invoice_link(
            title="Подписка TimeNick",
            description=f"Доступ к боту на 30 дней за {SUBSCRIPTION_STARS} Stars",
            payload=SUBSCRIPTION_PAYLOAD,
            currency="XTR",
            prices=prices,
            subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
        )
        await callback.message.answer(
            "Нажмите кнопку ниже, чтобы оплатить прямо в Telegram:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=f"⭐ Оплатить {SUBSCRIPTION_STARS} Stars", url=link)]]
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
                build_not_connected_text(),
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(db, bot_username),
            )

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username)

        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            if not db.is_subscribed(user_id, settings.owner_id):
                try:
                    await connection.bot.send_message(
                        user_id,
                        build_subscription_required_text(),
                        parse_mode="HTML",
                        reply_markup=build_pay_keyboard(db),
                    )
                except Exception:
                    logger.exception("Failed to notify user_id=%s about subscription", user_id)
                return
            try:
                await connection.bot.send_message(
                    user_id,
                    "<b>Бот подключён.</b>",
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
        await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
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

    # ------------------------------ админ-панель ------------------------------

    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        user_id = message.from_user.id
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username)
        if not is_admin(user_id):
            return
        await message.answer(
            "<b>Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard(is_owner(user_id)),
        )

    @dp.callback_query(F.data == "admin_home")
    async def admin_home(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        await callback.message.edit_text(
            "<b>Админ-панель</b>",
            parse_mode="HTML",
            reply_markup=build_admin_main_keyboard(is_owner(callback.from_user.id)),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        total = db.count_users()
        subscribed = db.count_active_subscribers()
        connected = db.count_connected()
        enabled = db.count_enabled()
        text = (
            "<b>📊 Статистика</b>\n\n"
            f"Всего пользователей (/start): <b>{total}</b>\n"
            f"Активных подписок: <b>{subscribed}</b>\n"
            f"Подключили бизнес-бота: <b>{connected}</b>\n"
            f"Сейчас включена функция: <b>{enabled}</b>"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_home")]]
            ),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def admin_users(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
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

    # --- кнопки: текст / цвет / premium emoji ---

    @dp.callback_query(F.data == "admin_buttons")
    async def admin_buttons(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
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
            f"Premium emoji ID: {row['icon_custom_emoji_id'] or '—'}\n\n"
            f"<i>Premium emoji на кнопке отображается только если у владельца бота "
            f"есть активная подписка Telegram Premium.</i>"
        )

    @dp.callback_query(F.data.startswith("admin_btn:"))
    async def admin_btn_edit(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.edit_text(
            _button_edit_text(key), parse_mode="HTML", reply_markup=build_admin_button_edit_keyboard(key)
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_style:"))
    async def admin_set_style(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
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
        if not is_admin(callback.from_user.id):
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
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_label)
        await callback.message.answer(f"Отправьте новый текст для кнопки «{key}»:")
        await callback.answer()

    @dp.message(AdminStates.waiting_label)
    async def admin_set_label_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
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
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.update_data(button_key=key)
        await state.set_state(AdminStates.waiting_emoji)
        await callback.message.answer(
            "Отправьте сюда сообщение с premium-эмодзи (просто отправьте стикер-эмодзи "
            "как обычное сообщение) — я возьму его custom_emoji_id автоматически.\n\n"
            "Работает только если у владельца бота есть Telegram Premium."
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji)
    async def admin_set_emoji_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
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
        if not is_admin(callback.from_user.id):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_grant_sub_id)
        await callback.message.answer(
            "Отправьте user_id пользователя, которому выдать подписку на 30 дней:"
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_sub_id)
    async def admin_grant_sub_finish(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
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

    # --- управление админами (только владелец) ---

    @dp.callback_query(F.data == "admin_admins")
    async def admin_admins_menu(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer("Только владелец бота", show_alert=True)
            return
        await callback.message.edit_text(
            "<b>🛡 Управление админами</b>",
            parse_mode="HTML",
            reply_markup=build_admin_admins_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_list_admins")
    async def admin_list_admins(callback: CallbackQuery) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer("Только владелец бота", show_alert=True)
            return
        admins = db.get_admins()
        if not admins:
            text = "Дополнительных админов нет."
        else:
            lines = ["<b>Админы:</b>"]
            for a in admins:
                uname = f"@{a['username']}" if a["username"] else "(нет username)"
                lines.append(f"• <code>{a['user_id']}</code> {uname}")
            text = "\n".join(lines)
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=build_admin_admins_keyboard()
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_grant_admin")
    async def admin_grant_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer("Только владелец бота", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_grant_admin_id)
        await callback.message.answer(
            "Отправьте user_id пользователя, которого сделать админом.\n"
            "Он получит доступ к статистике, юзерам, кнопкам и выдаче подписок "
            "(без права назначать других админов)."
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_admin_id)
    async def admin_grant_admin_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        target_id = parse_user_id(message.text or "")
        await state.clear()
        if target_id is None:
            await message.answer("Некорректный user_id. Отправьте число.")
            return

        if not db.get_user(target_id):
            db.upsert_user(target_id, "", None)
        db.set_admin(target_id, True)
        await message.answer(f"✅ Пользователь <code>{target_id}</code> назначен админом.", parse_mode="HTML")

        try:
            await message.bot.send_message(
                target_id,
                "🛡 Вам выдан доступ к админ-панели бота. Откройте её командой /admin",
            )
        except Exception:
            logger.info("Could not notify user_id=%s about admin grant", target_id)

    @dp.callback_query(F.data == "admin_revoke_admin")
    async def admin_revoke_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(callback.from_user.id):
            await callback.answer("Только владелец бота", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_revoke_admin_id)
        await callback.message.answer("Отправьте user_id админа, которого нужно снять:")
        await callback.answer()

    @dp.message(AdminStates.waiting_revoke_admin_id)
    async def admin_revoke_admin_finish(message: Message, state: FSMContext) -> None:
        if not is_owner(message.from_user.id):
            return
        target_id = parse_user_id(message.text or "")
        await state.clear()
        if target_id is None:
            await message.answer("Некорректный user_id. Отправьте число.")
            return

        db.set_admin(target_id, False)
        await message.answer(f"✅ Права админа сняты с <code>{target_id}</code>.", parse_mode="HTML")


# ---------------------------------------------------------------------------
# Периодическое обновление ников
# ---------------------------------------------------------------------------

def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05


async def run_update_loop(db: Database, clock: NicknameClock, tz: timezone, owner_id: int) -> None:
    while True:
        delay = seconds_until_next_minute(tz)
        await asyncio.sleep(delay)

        for row in db.get_enabled_users():
            user_id = row["user_id"]
            if not db.is_subscribed(user_id, owner_id):
                db.set_enabled(user_id, False)
                await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                continue
            await clock.apply(
                user_id, row["business_connection_id"], row["first_name"] or ""
            )


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
    # владелец сразу считается админом в базе (для единообразия отображения в списках)
    db.upsert_user(settings.owner_id, "", None)
    db.set_admin(settings.owner_id, True)

    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours)

    me = await bot.get_me()
    register_handlers(dp, db, clock, me.username, settings)

    asyncio.create_task(run_update_loop(db, clock, tz, settings.owner_id))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
