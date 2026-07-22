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
from aiogram.filters import Command
from aiogram.methods import SetBusinessAccountName
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set in environment (.env)")

        return cls(
            bot_token=bot_token,
            db_path=os.getenv("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.getenv("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
        )


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
                    business_connection_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def upsert_user(self, user_id: int, first_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, first_name) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET first_name = excluded.first_name
                """,
                (user_id, first_name),
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


def build_toggle_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    label = "Выключить" if enabled else "Включить"
    action = "toggle_off" if enabled else "toggle_on"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=action)]]
    )


def build_connect_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подключить", url="tg://settings/edit")],
            [InlineKeyboardButton(
                text="Скопировать",
                copy_text=CopyTextButton(text=f"@{bot_username}"),
            )],
        ]
    )


def build_not_connected_text() -> str:
    return (
        "<b>Бот не подключён.</b>\n\n"
        "Нажмите на кнопку <b>Подключить</b>, затем на кнопку <b>Скопировать</b>, "
        "далее - <b>Автоматизация чатов</b>, вставьте текст который вы скопировали, "
        "и нажмите <b>Добавить</b>. Дальше разрешите <b>Управлять профилем</b>."
    )


def register_handlers(
    dp: Dispatcher, db: Database, clock: NicknameClock, bot_username: str
) -> None:
    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        user_id = message.from_user.id
        db.upsert_user(user_id, message.from_user.first_name or "")

        row = db.get_user(user_id)
        is_connected = bool(row and row["business_connection_id"])

        if not is_connected:
            await message.answer(
                build_not_connected_text(),
                parse_mode="HTML",
                reply_markup=build_connect_keyboard(bot_username),
            )
            return

        is_enabled = bool(row["enabled"])
        status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
        await message.answer(
            status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(is_enabled)
        )

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        db.upsert_user(user_id, connection.user.first_name or "")

        if connection.is_enabled:
            db.set_connection(user_id, connection.id)
            try:
                await connection.bot.send_message(
                    user_id,
                    "<b>Бот подключён.</b>",
                    parse_mode="HTML",
                    reply_markup=build_toggle_keyboard(False),
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

        db.set_enabled(user_id, True)
        await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
        await callback.message.edit_text(
            "<b>Время в нике включено.</b>",
            parse_mode="HTML",
            reply_markup=build_toggle_keyboard(True),
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
            reply_markup=build_toggle_keyboard(False),
        )
        await callback.answer()


def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05


async def run_update_loop(db: Database, clock: NicknameClock, tz: timezone) -> None:
    while True:
        delay = seconds_until_next_minute(tz)
        await asyncio.sleep(delay)

        for row in db.get_enabled_users():
            await clock.apply(
                row["user_id"], row["business_connection_id"], row["first_name"] or ""
            )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = Settings.from_env()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    db = Database(settings.db_path)
    db.init_schema()

    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours)

    me = await bot.get_me()
    register_handlers(dp, db, clock, me.username)

    asyncio.create_task(run_update_loop(db, clock, tz))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
