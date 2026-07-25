from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterator, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
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
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")

# ======================================================================
# CONFIG
# ======================================================================

DEFAULT_EMOJI: dict[str, str] = {
    "welcome_check": "5463161330649298358",  # ✅
    "opt_time": "5391026156617115607",       # 1️⃣
    "opt_date": "5391147369184141550",        # 2️⃣
    "opt_countdown": "5390812022432638736",   # 3️⃣
    "star": "5952066863931331270",            # ⭐️
}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3
    trial_days: int = 1
    default_price_stars: int = 15
    default_price_crypto_usdt: float = 0.5
    cryptobot_token: str = ""
    cryptobot_testnet: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set in environment (.env)")

        owner_id_raw = os.getenv("OWNER_ID")
        if not owner_id_raw:
            raise RuntimeError("OWNER_ID is not set in environment (.env)")

        return cls(
            bot_token=bot_token,
            owner_id=int(owner_id_raw),
            db_path=os.getenv("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.getenv("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
            trial_days=int(os.getenv("TRIAL_DAYS", cls.trial_days)),
            default_price_stars=int(
                os.getenv("SUB_PRICE_STARS", cls.default_price_stars)
            ),
            default_price_crypto_usdt=float(
                os.getenv("SUB_PRICE_USDT", cls.default_price_crypto_usdt)
            ),
            cryptobot_token=os.getenv("CRYPTOBOT_API_TOKEN", ""),
            cryptobot_testnet=os.getenv("CRYPTOBOT_TESTNET", "false").lower() == "true",
        )


# ======================================================================
# DATABASE
# ======================================================================


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

    def init_schema(self, owner_id: int, trial_days: int, default_price: int, default_price_usdt: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    business_connection_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT 'time',
                    seconds_enabled INTEGER NOT NULL DEFAULT 0,
                    countdown_target TEXT,
                    countdown_label TEXT,
                    date_format TEXT NOT NULL DEFAULT '%d.%m',
                    trial_used INTEGER NOT NULL DEFAULT 0,
                    sub_until INTEGER,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emoji (
                    key TEXT PRIMARY KEY,
                    emoji_id TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    method TEXT NOT NULL DEFAULT 'stars',
                    amount TEXT NOT NULL,
                    charge_id TEXT,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_invoices (
                    invoice_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS button_styles (
                    key TEXT PRIMARY KEY,
                    style TEXT
                )
                """
            )

            defaults = {
                "price_stars": str(default_price),
                "price_usdt": str(default_price_usdt),
                "trial_days": str(trial_days),
                "broadcast_photo_id": "",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
                )

            for key, emoji_id in DEFAULT_EMOJI.items():
                conn.execute(
                    "INSERT OR IGNORE INTO emoji (key, emoji_id) VALUES (?, ?)",
                    (key, emoji_id),
                )

    # -- users --------------------------------------------------------
    def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, first_name, username) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
                """,
                (user_id, first_name, username),
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

    def set_mode(self, user_id: int, mode: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET mode = ? WHERE user_id = ?", (mode, user_id))

    def set_countdown(self, user_id: int, target_iso: str, label: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET countdown_target = ?, countdown_label = ? WHERE user_id = ?",
                (target_iso, label, user_id),
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
                SELECT * FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
                """
            ).fetchall()

    def get_all_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users").fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_active_subs(self) -> int:
        now = int(time.time())
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE sub_until IS NOT NULL AND sub_until > ?",
                (now,),
            ).fetchone()["c"]

    # -- subscription ---------------------------------------------------
    def is_subscribed(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id:
            return True
        row = self.get_user(user_id)
        if not row or row["sub_until"] is None:
            return False
        return row["sub_until"] > int(time.time())

    def grant_subscription(self, user_id: int, days: int) -> int:
        now = int(time.time())
        row = self.get_user(user_id)
        base = now
        if row and row["sub_until"] and row["sub_until"] > now:
            base = row["sub_until"]
        new_until = base + days * 86400
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, '')",
                (user_id,),
            )
            conn.execute(
                "UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until, user_id)
            )
        return new_until

    def revoke_subscription(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET sub_until = NULL WHERE user_id = ?", (user_id,)
            )

    def use_trial(self, user_id: int, days: int) -> int:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,)
            )
        return self.grant_subscription(user_id, days)

    def trial_available(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(row and not row["trial_used"])

    # -- payments -------------------------------------------------------
    def record_payment(self, user_id: int, method: str, amount: str, charge_id: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO payments (user_id, method, amount, charge_id) VALUES (?, ?, ?, ?)",
                (user_id, method, amount, charge_id),
            )

    def total_stars_earned(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(CAST(amount AS INTEGER)),0) AS s FROM payments WHERE method='stars'"
            ).fetchone()
            return row["s"]

    def total_crypto_payments(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM payments WHERE method='crypto'"
            ).fetchone()
            return row["c"]

    # -- crypto invoices --------------------------------------------------
    def save_crypto_invoice(self, invoice_id: str, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO crypto_invoices (invoice_id, user_id, status) VALUES (?, ?, 'active')",
                (invoice_id, user_id),
            )

    def mark_crypto_invoice(self, invoice_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crypto_invoices SET status = ? WHERE invoice_id = ?",
                (status, invoice_id),
            )

    def get_active_crypto_invoices(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM crypto_invoices WHERE status = 'active'"
            ).fetchall()

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_price_stars(self) -> int:
        return int(self.get_setting("price_stars", "15"))

    def get_price_usdt(self) -> float:
        return float(self.get_setting("price_usdt", "0.5"))

    def get_trial_days(self) -> int:
        return int(self.get_setting("trial_days", "1"))

    # -- emoji --------------------------------------------------------------
    def get_emoji(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT emoji_id FROM emoji WHERE key = ?", (key,)
            ).fetchone()
            return row["emoji_id"] if row else None

    def set_emoji(self, key: str, emoji_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO emoji (key, emoji_id) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET emoji_id = excluded.emoji_id
                """,
                (key, emoji_id),
            )

    def get_all_emoji(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM emoji ORDER BY key").fetchall()

    # -- button styles (Bot API 9.4) -----------------------------------------
    def get_button_style(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT style FROM button_styles WHERE key = ?", (key,)
            ).fetchone()
            return row["style"] if row else None

    def set_button_style(self, key: str, style: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO button_styles (key, style) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET style = excluded.style
                """,
                (key, style),
            )


# ======================================================================
# CRYPTOBOT (Crypto Pay API) CLIENT
# ======================================================================


class CryptoPayError(Exception):
    pass


class CryptoBotClient:
    """Minimal async client for the @CryptoBot Crypto Pay API.
    Docs: https://help.crypt.bot/crypto-pay-api
    """

    def __init__(self, token: str, testnet: bool = False) -> None:
        self._token = token
        self._base_url = (
            "https://testnet-pay.crypt.bot/api" if testnet else "https://pay.crypt.bot/api"
        )

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def _request(self, method: str, params: dict) -> dict:
        if not self._token:
            raise CryptoPayError("CRYPTOBOT_API_TOKEN не задан в .env")
        headers = {"Crypto-Pay-API-Token": self._token}
        url = f"{self._base_url}/{method}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data.get("error", data)))
        return data["result"]

    async def create_invoice(self, amount: float, description: str, payload: str) -> dict:
        return await self._request(
            "createInvoice",
            {
                "currency_type": "crypto",
                "asset": "USDT",
                "amount": str(amount),
                "description": description,
                "payload": payload,
                "expires_in": 1800,
            },
        )

    async def get_invoice_status(self, invoice_id: str) -> Optional[str]:
        result = await self._request("getInvoices", {"invoice_ids": str(invoice_id)})
        items = result.get("items", [])
        if not items:
            return None
        return items[0].get("status")


# ======================================================================
# NICKNAME CLOCK
# ======================================================================


def format_label(row, tz: timezone) -> str:
    mode = row["mode"] or "time"
    now = datetime.now(tz)

    if mode == "seconds":
        return now.strftime("• [%H:%M:%S]")

    if mode == "date":
        fmt = row["date_format"] or "%d.%m"
        return f"• [{now.strftime(fmt)}]"

    if mode == "countdown":
        target_iso = row["countdown_target"]
        label = row["countdown_label"] or "Отсчёт"
        if not target_iso:
            return now.strftime("• [%H:%M]")
        try:
            target = datetime.fromisoformat(target_iso)
            if target.tzinfo is None:
                target = target.replace(tzinfo=tz)
        except ValueError:
            return now.strftime("• [%H:%M]")

        delta = target - now
        if delta.total_seconds() <= 0:
            return f"• [{label}: сегодня!]"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"• [{label}: {days}д {hours}ч]"
        minutes = (delta.seconds % 3600) // 60
        return f"• [{label}: {hours}ч {minutes}м]"

    return now.strftime("• [%H:%M]")


class NicknameClock:
    def __init__(self, bot: Bot, db: Database, tz_offset_hours: int) -> None:
        self._bot = bot
        self._db = db
        self._tz = timezone(timedelta(hours=tz_offset_hours))
        self._last_applied: dict[int, str] = {}

    async def apply(self, row) -> None:
        connection_id = row["business_connection_id"]
        if not connection_id:
            return

        label = format_label(row, self._tz)
        user_id = row["user_id"]
        if self._last_applied.get(user_id) == label:
            return

        try:
            await self._bot(
                SetBusinessAccountName(
                    business_connection_id=connection_id,
                    first_name=row["first_name"] or "",
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


def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05


async def run_update_loop(db: Database, clock: NicknameClock, tz: timezone) -> None:
    while True:
        delay = seconds_until_next_minute(tz)
        await asyncio.sleep(delay)
        for row in db.get_enabled_users():
            await clock.apply(row)


async def run_crypto_poll_loop(
    db: Database, crypto: CryptoBotClient, bot: Bot, settings: Settings
) -> None:
    """Poll active CryptoBot invoices for payment status (no webhook server
    required — simpler for a single-file deployment)."""
    if not crypto.configured:
        return
    while True:
        await asyncio.sleep(10)
        for inv in db.get_active_crypto_invoices():
            try:
                status = await crypto.get_invoice_status(inv["invoice_id"])
            except CryptoPayError:
                continue
            if status == "paid":
                db.mark_crypto_invoice(inv["invoice_id"], "paid")
                user_id = inv["user_id"]
                price = db.get_price_usdt()
                db.record_payment(user_id, "crypto", str(price), inv["invoice_id"])
                new_until = db.grant_subscription(user_id, 30)
                until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ <b>Оплата криптой прошла успешно!</b>\nПодписка активна до <b>{until_str}</b>.",
                        parse_mode="HTML",
                    )
                    await show_start_screen(bot, db, settings, user_id)
                except Exception:
                    logger.exception("Failed to notify user_id=%s about crypto payment", user_id)
            elif status == "expired":
                db.mark_crypto_invoice(inv["invoice_id"], "expired")


# ======================================================================
# TEXTS / EMOJI RENDERING
# ======================================================================


def emoji_tag(db: Database, key: str, fallback: str) -> str:
    emoji_id = db.get_emoji(key)
    if emoji_id:
        return f'<tg-emoji emoji-id="{escape(emoji_id)}">{fallback}</tg-emoji>'
    return fallback


def _days_word(n: int) -> str:
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дня"
    return "дней"


def welcome_text(db: Database) -> str:
    check = emoji_tag(db, "welcome_check", "✅")
    one = emoji_tag(db, "opt_time", "1️⃣")
    two = emoji_tag(db, "opt_date", "2️⃣")
    three = emoji_tag(db, "opt_countdown", "3️⃣")
    star = emoji_tag(db, "star", "⭐️")
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    trial_days = db.get_trial_days()

    return (
        f"{check} <b>Здравствуй!</b>\n\n"
        "Это бот <b>Time</b>, который может ставить в ваш никнейм:\n\n"
        f"{one} Время\n"
        f"{two} Дату\n"
        f"{three} Обратный отсчёт\n\n"
        "И многое другое!\n\n"
        f"{star} <b>{trial_days} {_days_word(trial_days)} подписки бесплатно</b>, "
        f"потом всего <b>{price_stars}⭐️/мес</b> или <b>{price_usdt}$ в крипте</b>."
    )


def instruction_text(db: Database) -> str:
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    trial_days = db.get_trial_days()
    return (
        "<b>📖 Инструкция по боту Time</b>\n\n"
        "<b>1. Подключение</b>\n"
        "Нажмите «🔗 Подключить» → «📋 Скопировать» → в Telegram откройте "
        "Настройки → Telegram для бизнеса → Чат-боты → Добавить бота → вставьте скопированный текст → "
        "разрешите пункт «Управлять профилем».\n\n"
        "<b>2. Режимы отображения в нике</b>\n"
        "🕐 Время — часы и минуты (ЧЧ:ММ)\n"
        "⏱ Время с секундами\n"
        "📅 Дата\n"
        "⏳ Обратный отсчёт — до указанной даты или события\n"
        "Выбираются в разделе «⚙️ Настройки».\n\n"
        "<b>3. Включение и выключение</b>\n"
        "Кнопка «▶️ Включить» / «⏹ Выключить» на главном экране запускает и "
        "останавливает обновление ника.\n\n"
        f"<b>4. Подписка</b>\n"
        f"Первые {trial_days} {_days_word(trial_days)} — бесплатно (кнопка «🎁 Пробный день»). "
        f"Далее — {price_stars}⭐️ или {price_usdt}$ в крипте за 30 дней.\n"
        "Продлить или оплатить можно кнопками на главном экране в любой момент.\n\n"
        "<b>5. Поддержка</b>\n"
        "Если что-то не работает — раздел «🆘 Поддержка» на главном экране."
    )


def support_text() -> str:
    return (
        "<b>🆘 Поддержка</b>\n\n"
        "<b>Частые вопросы:</b>\n\n"
        "• <b>Бот не подключается</b> — проверьте, что при добавлении бота в "
        "Telegram для бизнеса разрешён пункт «Управлять профилем».\n\n"
        "• <b>Ник не обновляется сразу</b> — обновление происходит раз в минуту, "
        "это ограничение Telegram Business API, подождите немного.\n\n"
        "• <b>Не проходит оплата</b> — попробуйте другой способ (Stars или крипта) "
        "либо повторите попытку чуть позже.\n\n"
        "• <b>Закончилась подписка</b> — продлите её кнопкой «🔁 Продлить» на главном экране.\n\n"
        "Если вопрос не решён — напишите нам напрямую:"
    )


def build_not_connected_text() -> str:
    return (
        "<b>Бот не подключён.</b>\n\n"
        "Нажмите на кнопку <b>Подключить</b>, затем на кнопку <b>Скопировать</b>, "
        "далее — <b>Автоматизация чатов</b>, вставьте текст, который вы скопировали, "
        "и нажмите <b>Добавить</b>. Дальше разрешите <b>Управлять профилем</b>."
    )


# ======================================================================
# KEYBOARDS
# ======================================================================


def _btn(db: Database, key: str, text: str, **kwargs) -> InlineKeyboardButton:
    style = db.get_button_style(key)
    icon = db.get_emoji(f"btn_{key}")
    if style:
        kwargs["style"] = style
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text=text, **kwargs)


def build_info_row(db: Database) -> list[InlineKeyboardButton]:
    return [
        _btn(db, "instruction", "📖 Инструкция", callback_data="show_instruction"),
        _btn(db, "support", "🆘 Поддержка", callback_data="show_support"),
    ]


def build_instruction_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(db, "back", "◀️ Закрыть", callback_data="close_info")]]
    )


def build_support_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(db, "support_link", "✉️ Написать в поддержку", url="https://t.me/deverskyi")],
            [_btn(db, "back", "◀️ Закрыть", callback_data="close_info")],
        ]
    )


def build_toggle_keyboard(db: Database, enabled: bool) -> InlineKeyboardMarkup:
    if enabled:
        btn = _btn(db, "toggle_off", "⏹ Выключить", callback_data="toggle_off")
    else:
        btn = _btn(db, "toggle_on", "▶️ Включить", callback_data="toggle_on")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn],
            [_btn(db, "settings", "⚙️ Настройки", callback_data="open_settings")],
            build_info_row(db),
        ]
    )


def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(db, "connect", "🔗 Подключить", url="tg://settings/edit")],
            [_btn(db, "copy", "📋 Скопировать", copy_text=CopyTextButton(text=f"@{bot_username}"))],
        ]
    )


def build_welcome_keyboard(db: Database, has_trial: bool, is_subscribed: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    if not is_subscribed and has_trial:
        rows.append([_btn(db, "trial", "🎁 Пробный день бесплатно", callback_data="use_trial")])
    label = "🔁 Продлить" if is_subscribed else "⭐️ Оплатить"
    rows.append(
        [
            _btn(db, "pay_stars", f"{label} — {price_stars}⭐️", callback_data="pay_stars"),
            _btn(db, "pay_crypto", f"💎 {price_usdt}$ крипта", callback_data="pay_crypto"),
        ]
    )
    rows.append([_btn(db, "settings", "⚙️ Настройки", callback_data="open_settings")])
    rows.append(build_info_row(db))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_main_reply_keyboard(db: Database) -> ReplyKeyboardMarkup:
    style = db.get_button_style("home")
    icon = db.get_emoji("btn_home")
    kwargs = {}
    if style:
        kwargs["style"] = style
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    button = KeyboardButton(text="🏠 Главная", **kwargs)
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, is_persistent=True)


def build_settings_keyboard(db: Database, user_row) -> InlineKeyboardMarkup:
    mode = user_row["mode"] if user_row else "time"

    def mark(m: str) -> str:
        return "✅ " if mode == m else ""

    rows = [
        [_btn(db, "mode_time", f"{mark('time')}🕐 Время", callback_data="mode_time")],
        [_btn(db, "mode_seconds", f"{mark('seconds')}⏱ Время с секундами", callback_data="mode_seconds")],
        [_btn(db, "mode_date", f"{mark('date')}📅 Дата", callback_data="mode_date")],
        [_btn(db, "mode_countdown", f"{mark('countdown')}⏳ Обратный отсчёт", callback_data="mode_countdown")],
        [_btn(db, "back", "◀️ Назад", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_menu_keyboard(db: Database) -> InlineKeyboardMarkup:
    rows = [
        [_btn(db, "admin_price", "💰 Цена подписки", callback_data="admin_price")],
        [_btn(db, "admin_trial", "🎁 Длительность пробного периода", callback_data="admin_trial")],
        [_btn(db, "admin_grant", "➕ Выдать подписку", callback_data="admin_grant")],
        [_btn(db, "admin_revoke", "➖ Отобрать подписку", callback_data="admin_revoke")],
        [_btn(db, "admin_broadcast", "📢 Рассылка", callback_data="admin_broadcast")],
        [_btn(db, "admin_photo", "🖼 Фото приветствия", callback_data="admin_photo")],
        [_btn(db, "admin_emoji", "🎨 Премиум-эмодзи", callback_data="admin_emoji")],
        [_btn(db, "admin_style", "🌈 Цвет кнопок", callback_data="admin_style")],
        [_btn(db, "admin_stats", "📊 Статистика", callback_data="admin_stats")],
        [_btn(db, "back", "◀️ Назад", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_back_keyboard(callback_data: str = "admin_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]]
    )


# ======================================================================
# FSM STATES
# ======================================================================


class AdminStates(StatesGroup):
    waiting_price = State()
    waiting_price_usdt = State()
    waiting_trial_days = State()
    waiting_grant_id = State()
    waiting_grant_days = State()
    waiting_revoke_id = State()
    waiting_broadcast_text = State()
    waiting_broadcast_photo = State()
    waiting_photo = State()


class UserStates(StatesGroup):
    waiting_countdown_target = State()


# ======================================================================
# START SCREEN (shared by /start, "Главная" button, and post-payment)
# ======================================================================


async def show_start_screen(bot: Bot, db: Database, settings: Settings, user_id: int) -> None:
    row = db.get_user(user_id)
    is_connected = bool(row and row["business_connection_id"])
    is_owner = user_id == settings.owner_id
    is_subscribed = db.is_subscribed(user_id, settings.owner_id)
    has_trial = db.trial_available(user_id) if row else True

    photo_id = db.get_setting("broadcast_photo_id", "")
    caption = welcome_text(db)
    reply_kb = build_main_reply_keyboard(db)

    if photo_id:
        await bot.send_photo(user_id, photo=photo_id, caption=caption, parse_mode="HTML", reply_markup=reply_kb)
    else:
        await bot.send_message(user_id, caption, parse_mode="HTML", reply_markup=reply_kb)

    if not is_connected:
        me = await bot.get_me()
        await bot.send_message(
            user_id,
            build_not_connected_text(),
            parse_mode="HTML",
            reply_markup=build_connect_keyboard(db, me.username),
        )
        return

    if not is_owner and not is_subscribed:
        await bot.send_message(
            user_id,
            "Чтобы включить функции бота, оформите подписку или возьмите пробный период.",
            reply_markup=build_welcome_keyboard(db, has_trial, is_subscribed),
        )
        return

    is_enabled = bool(row["enabled"])
    status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
    await bot.send_message(user_id, status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(db, is_enabled))


# ======================================================================
# USER HANDLERS
# ======================================================================


def register_user_handlers(
    dp: Dispatcher, db: Database, clock: NicknameClock, settings: Settings, crypto: CryptoBotClient
) -> None:
    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username)
        await show_start_screen(message.bot, db, settings, message.from_user.id)

    @dp.message(F.text == "🏠 Главная")
    async def handle_home_button(message: Message) -> None:
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username)
        await show_start_screen(message.bot, db, settings, message.from_user.id)

    @dp.callback_query(F.data == "back_to_start")
    async def handle_back_to_start(callback: CallbackQuery) -> None:
        await callback.message.delete()
        await show_start_screen(callback.bot, db, settings, callback.from_user.id)
        await callback.answer()

    @dp.callback_query(F.data == "show_instruction")
    async def handle_show_instruction(callback: CallbackQuery) -> None:
        await callback.message.answer(
            instruction_text(db), parse_mode="HTML", reply_markup=build_instruction_keyboard(db)
        )
        await callback.answer()

    @dp.callback_query(F.data == "show_support")
    async def handle_show_support(callback: CallbackQuery) -> None:
        await callback.message.answer(
            support_text(), parse_mode="HTML", reply_markup=build_support_keyboard(db)
        )
        await callback.answer()

    @dp.callback_query(F.data == "close_info")
    async def handle_close_info(callback: CallbackQuery) -> None:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username)

        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            try:
                await connection.bot.send_message(
                    user_id, "<b>Бот подключён.</b>", parse_mode="HTML",
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
        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("Бот не подключён", show_alert=True)
            return
        if user_id != settings.owner_id and not db.is_subscribed(user_id, settings.owner_id):
            await callback.answer("Нужна активная подписка", show_alert=True)
            return
        db.set_enabled(user_id, True)
        await clock.apply(db.get_user(user_id))
        await callback.message.edit_text(
            "<b>Время в нике включено.</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, True)
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
            "<b>Время в нике выключено.</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, False)
        )
        await callback.answer()

    @dp.callback_query(F.data == "open_settings")
    async def handle_open_settings(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            "<b>⚙️ Настройки формата ника</b>\n\nВыберите режим отображения:",
            parse_mode="HTML", reply_markup=build_settings_keyboard(db, row),
        )
        await callback.answer()

    @dp.callback_query(F.data == "mode_time")
    async def handle_mode_time(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "time")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer("Режим: Время (ЧЧ:ММ)")

    @dp.callback_query(F.data == "mode_seconds")
    async def handle_mode_seconds(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "seconds")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer(
            "Режим: Время с секундами. Ник всё равно обновляется раз в минуту "
            "(ограничение Telegram Business API).", show_alert=True,
        )

    @dp.callback_query(F.data == "mode_date")
    async def handle_mode_date(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "date")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer("Режим: Дата")

    @dp.callback_query(F.data == "mode_countdown")
    async def handle_mode_countdown(callback: CallbackQuery, state: FSMContext) -> None:
        db.set_mode(callback.from_user.id, "countdown")
        await state.set_state(UserStates.waiting_countdown_target)
        await callback.message.answer(
            "Введите дату и (опционально) название события в формате:\n"
            "<code>ГГГГ-ММ-ДД Название</code>\n\nНапример: <code>2027-01-01 Новый год</code>",
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(UserStates.waiting_countdown_target)
    async def handle_countdown_input(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        date_part = parts[0] if parts else ""
        label = parts[1] if len(parts) > 1 else "Отсчёт"
        try:
            target = datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            await message.answer("Не удалось распознать дату. Формат: <code>ГГГГ-ММ-ДД Название</code>", parse_mode="HTML")
            return
        db.set_countdown(message.from_user.id, target.isoformat(), label)
        await state.clear()
        await message.answer(f"✅ Обратный отсчёт до «{label}» ({date_part}) установлен.")
        row = db.get_user(message.from_user.id)
        await message.answer("<b>⚙️ Настройки формата ника</b>", parse_mode="HTML", reply_markup=build_settings_keyboard(db, row))

    # -- subscription / payments -----------------------------------------
    @dp.callback_query(F.data == "use_trial")
    async def handle_use_trial(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        if not db.trial_available(user_id):
            await callback.answer("Пробный период уже был использован", show_alert=True)
            return
        days = db.get_trial_days()
        db.use_trial(user_id, days)
        await callback.answer("Пробный период активирован!", show_alert=True)
        await callback.message.delete()
        await show_start_screen(callback.bot, db, settings, user_id)

    @dp.callback_query(F.data == "pay_stars")
    async def handle_pay_stars(callback: CallbackQuery) -> None:
        price = db.get_price_stars()
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Подписка Time — 1 месяц",
            description=f"Доступ ко всем функциям бота Time на 30 дней ({price}⭐️).",
            payload=f"sub_month_stars:{price}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Подписка на 1 месяц", amount=price)],
        )
        await callback.answer()

    @dp.callback_query(F.data == "pay_crypto")
    async def handle_pay_crypto(callback: CallbackQuery) -> None:
        if not crypto.configured:
            await callback.answer("Оплата криптой временно недоступна", show_alert=True)
            return
        price = db.get_price_usdt()
        user_id = callback.from_user.id
        try:
            invoice = await crypto.create_invoice(
                amount=price, description="Подписка Time — 1 месяц", payload=f"sub_month:{user_id}"
            )
        except CryptoPayError as exc:
            logger.exception("CryptoBot invoice creation failed")
            await callback.answer(f"Ошибка создания счёта: {exc}", show_alert=True)
            return

        db.save_crypto_invoice(str(invoice["invoice_id"]), user_id)
        pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"💎 Оплатить {price}$ USDT", url=pay_url)]]
        )
        await callback.message.answer(
            "Счёт на оплату создан. После оплаты подписка активируется автоматически "
            "в течение ~10 секунд.",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        await pre_checkout_query.answer(ok=True)

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        stars = payment.total_amount
        db.record_payment(message.from_user.id, "stars", str(stars), payment.telegram_payment_charge_id)
        new_until = db.grant_subscription(message.from_user.id, 30)
        until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
        await message.answer(
            f"✅ <b>Оплата прошла успешно!</b>\nПодписка активна до <b>{until_str}</b>.", parse_mode="HTML"
        )
        await show_start_screen(message.bot, db, settings, message.from_user.id)


# ======================================================================
# ADMIN HANDLERS
# ======================================================================


def _is_owner(user_id: int, settings: Settings) -> bool:
    return user_id == settings.owner_id


def register_admin_handlers(dp: Dispatcher, db: Database, settings: Settings) -> None:
    @dp.message(Command("admin"))
    async def handle_admin_entry(message: Message) -> None:
        if not _is_owner(message.from_user.id, settings):
            return
        await message.answer("<b>🔐 Секретная админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_menu_keyboard(db))

    @dp.callback_query(F.data == "admin_menu")
    async def handle_admin_menu(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text("<b>🔐 Секретная админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_menu_keyboard(db))
        await callback.answer()

    @dp.callback_query(F.data == "admin_price")
    async def handle_admin_price(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current_stars = db.get_price_stars()
        current_usdt = db.get_price_usdt()
        await state.set_state(AdminStates.waiting_price)
        await callback.message.edit_text(
            f"Текущая цена: <b>{current_stars}⭐️</b> / <b>{current_usdt}$</b>\n"
            "Введите новую цену в stars (число):",
            parse_mode="HTML", reply_markup=build_back_keyboard(),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_price)
    async def handle_price_input(message: Message, state: FSMContext) -> None:
        try:
            value = int((message.text or "").strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число.")
            return
        db.set_setting("price_stars", str(value))
        await state.set_state(AdminStates.waiting_price_usdt)
        await message.answer("Теперь введите цену в USDT (например 0.5):")

    @dp.message(AdminStates.waiting_price_usdt)
    async def handle_price_usdt_input(message: Message, state: FSMContext) -> None:
        try:
            value = float((message.text or "").strip().replace(",", "."))
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное число, например 0.5.")
            return
        db.set_setting("price_usdt", str(value))
        await state.clear()
        await message.answer(
            f"✅ Новая цена: {db.get_price_stars()}⭐️ / {value}$ USDT", reply_markup=build_back_keyboard()
        )

    @dp.callback_query(F.data == "admin_trial")
    async def handle_admin_trial(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_trial_days()
        await state.set_state(AdminStates.waiting_trial_days)
        await callback.message.edit_text(
            f"Текущий пробный период: <b>{current} дн.</b>\nВведите новое число дней:",
            parse_mode="HTML", reply_markup=build_back_keyboard(),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_trial_days)
    async def handle_trial_input(message: Message, state: FSMContext) -> None:
        try:
            value = int((message.text or "").strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число дней.")
            return
        db.set_setting("trial_days", str(value))
        await state.clear()
        await message.answer(f"✅ Пробный период теперь: {value} дн.", reply_markup=build_back_keyboard())

    @dp.callback_query(F.data == "admin_grant")
    async def handle_admin_grant(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_grant_id)
        await callback.message.edit_text("Введите user_id пользователя, которому хотите выдать подписку:", reply_markup=build_back_keyboard())
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_id)
    async def handle_grant_id_input(message: Message, state: FSMContext) -> None:
        try:
            uid = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите числовой user_id.")
            return
        await state.update_data(grant_uid=uid)
        await state.set_state(AdminStates.waiting_grant_days)
        await message.answer("На сколько дней выдать подписку?")

    @dp.message(AdminStates.waiting_grant_days)
    async def handle_grant_days_input(message: Message, state: FSMContext) -> None:
        try:
            days = int((message.text or "").strip())
            if days <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число дней.")
            return
        data = await state.get_data()
        uid = data["grant_uid"]
        new_until = db.grant_subscription(uid, days)
        until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
        await state.clear()
        await message.answer(f"✅ Пользователю <code>{uid}</code> выдана подписка до {until_str}.", parse_mode="HTML", reply_markup=build_back_keyboard())
        try:
            await message.bot.send_message(uid, f"🎁 Вам выдана подписка на {days} дн.! Действует до {until_str}.")
        except Exception:
            logger.exception("Failed to notify user_id=%s about granted subscription", uid)

    @dp.callback_query(F.data == "admin_revoke")
    async def handle_admin_revoke(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_revoke_id)
        await callback.message.edit_text("Введите user_id пользователя, у которого нужно отобрать подписку:", reply_markup=build_back_keyboard())
        await callback.answer()

    @dp.message(AdminStates.waiting_revoke_id)
    async def handle_revoke_id_input(message: Message, state: FSMContext) -> None:
        try:
            uid = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите числовой user_id.")
            return
        db.revoke_subscription(uid)
        await state.clear()
        await message.answer(f"✅ Подписка пользователя <code>{uid}</code> отозвана.", parse_mode="HTML", reply_markup=build_back_keyboard())

    @dp.callback_query(F.data == "admin_broadcast")
    async def handle_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_broadcast_text)
        await callback.message.edit_text("Введите текст рассылки (можно с HTML-разметкой):", reply_markup=build_back_keyboard())
        await callback.answer()

    @dp.message(AdminStates.waiting_broadcast_text)
    async def handle_broadcast_text_input(message: Message, state: FSMContext) -> None:
        await state.update_data(broadcast_text=message.html_text)
        await state.set_state(AdminStates.waiting_broadcast_photo)
        await message.answer("Отправьте фото для рассылки, либо напишите «нет», чтобы отправить только текст.")

    @dp.message(AdminStates.waiting_broadcast_photo)
    async def handle_broadcast_photo_input(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        text = data.get("broadcast_text", "")
        photo_id = None
        if message.photo:
            photo_id = message.photo[-1].file_id
        elif (message.text or "").strip().lower() not in ("нет", "no", "-"):
            await message.answer("Отправьте фото или напишите «нет».")
            return

        await state.clear()
        users = db.get_all_users()
        await message.answer(f"Рассылка запущена на {len(users)} пользователей...")

        sent, failed = 0, 0
        for row in users:
            try:
                if photo_id:
                    await message.bot.send_photo(row["user_id"], photo=photo_id, caption=text, parse_mode="HTML")
                else:
                    await message.bot.send_message(row["user_id"], text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}.", reply_markup=build_back_keyboard())

    @dp.callback_query(F.data == "admin_photo")
    async def handle_admin_photo(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_photo)
        await callback.message.edit_text(
            "Отправьте фото, которое будет показываться в приветствии (/start). "
            "Напишите «удалить», чтобы убрать текущее фото.",
            reply_markup=build_back_keyboard(),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_photo)
    async def handle_photo_input(message: Message, state: FSMContext) -> None:
        if message.photo:
            photo_id = message.photo[-1].file_id
            db.set_setting("broadcast_photo_id", photo_id)
            await state.clear()
            await message.answer("✅ Фото приветствия обновлено.", reply_markup=build_back_keyboard())
            return
        if (message.text or "").strip().lower() in ("удалить", "delete", "-"):
            db.set_setting("broadcast_photo_id", "")
            await state.clear()
            await message.answer("✅ Фото приветствия удалено.", reply_markup=build_back_keyboard())
            return
        await message.answer("Отправьте фото или напишите «удалить».")

    @dp.callback_query(F.data == "admin_emoji")
    async def handle_admin_emoji(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        rows = db.get_all_emoji()
        lines = ["<b>🎨 Премиум-эмодзи</b>\n"]
        for r in rows:
            lines.append(f"• <code>{r['key']}</code> → id <code>{r['emoji_id']}</code>")
        lines.append(
            "\nЧтобы изменить, отправьте команду:\n<code>/setemoji ключ emoji_id</code>\n\n"
            "Например:\n<code>/setemoji welcome_check 5463161330649298358</code>\n\n"
            "Также можно добавить emoji на любую inline-кнопку через ключ вида "
            "<code>btn_&lt;callback_key&gt;</code>."
        )
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=build_back_keyboard())
        await callback.answer()

    @dp.message(Command("setemoji"))
    async def handle_setemoji_command(message: Message) -> None:
        if not _is_owner(message.from_user.id, settings):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: <code>/setemoji ключ emoji_id</code>", parse_mode="HTML")
            return
        _, key, emoji_id = parts
        db.set_emoji(key.strip(), emoji_id.strip())
        await message.answer(f"✅ Эмодзи для ключа <code>{key.strip()}</code> обновлено.", parse_mode="HTML")

    @dp.callback_query(F.data == "admin_style")
    async def handle_admin_style(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        text = (
            "<b>🌈 Цвет inline-кнопок</b>\n\n"
            "Bot API 9.4: primary (🔵) / success (🟢) / danger (🔴) / none (сброс)\n\n"
            "Отправьте команду:\n<code>/setstyle ключ_кнопки цвет</code>\n\n"
            "Ключи: connect, copy, toggle_on, toggle_off, pay_stars, pay_crypto, trial, "
            "settings, back, home, instruction, support, support_link, "
            "admin_price, admin_trial, admin_grant, admin_revoke, "
            "admin_broadcast, admin_photo, admin_emoji, admin_style, admin_stats, "
            "mode_time, mode_seconds, mode_date, mode_countdown\n\n"
            "Например:\n<code>/setstyle pay_stars success</code>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=build_back_keyboard())
        await callback.answer()

    @dp.message(Command("setstyle"))
    async def handle_setstyle_command(message: Message) -> None:
        if not _is_owner(message.from_user.id, settings):
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            await message.answer("Использование: <code>/setstyle ключ_кнопки цвет</code>", parse_mode="HTML")
            return
        _, key, style = parts
        style = style.lower()
        if style not in ("primary", "success", "danger", "none"):
            await message.answer("Цвет должен быть: primary, success, danger или none.")
            return
        db.set_button_style(key, None if style == "none" else style)
        await message.answer(f"✅ Стиль кнопки <code>{key}</code>: {style}", parse_mode="HTML")

    @dp.callback_query(F.data == "admin_stats")
    async def handle_admin_stats(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        total_users = db.count_users()
        active_subs = db.count_active_subs()
        total_stars = db.total_stars_earned()
        total_crypto = db.total_crypto_payments()
        text = (
            "<b>📊 Статистика</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"✅ Активных подписок: <b>{active_subs}</b>\n"
            f"⭐️ Заработано stars: <b>{total_stars}</b>\n"
            f"💎 Оплат криптой: <b>{total_crypto}</b>\n"
            f"💰 Цена: <b>{db.get_price_stars()}⭐️</b> / <b>{db.get_price_usdt()}$</b>\n"
            f"🎁 Пробный период: <b>{db.get_trial_days()} дн.</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=build_back_keyboard())
        await callback.answer()


# ======================================================================
# ENTRYPOINT
# ======================================================================


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    settings = Settings.from_env()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    db = Database(settings.db_path)
    db.init_schema(settings.owner_id, settings.trial_days, settings.default_price_stars, settings.default_price_crypto_usdt)

    crypto = CryptoBotClient(settings.cryptobot_token, settings.cryptobot_testnet)

    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours)

    register_user_handlers(dp, db, clock, settings, crypto)
    register_admin_handlers(dp, db, settings)

    asyncio.create_task(run_update_loop(db, clock, tz))
    asyncio.create_task(run_crypto_poll_loop(db, crypto, bot, settings))

    logger.info("Bot started. Owner id: %s. CryptoBot: %s", settings.owner_id, "enabled" if crypto.configured else "disabled")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
