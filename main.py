from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
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
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("timenick")

# ============================================================
# КОНСТАНТЫ
# ============================================================

SUBSCRIPTION_STARS = 25
SUBSCRIPTION_DAYS = 30
ADMIN_SUBSCRIPTION_DAYS = 365  # админам на год

# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

ADMIN_IDS = []
admin_ids_raw = os.getenv("ADMIN_IDS", "")
for x in admin_ids_raw.replace(" ", "").split(","):
    if x:
        ADMIN_IDS.append(int(x))

logger.info(f"Админы: {ADMIN_IDS}")

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

class Database:
    def __init__(self):
        self._path = "timenick.db"

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    business_connection_id TEXT,
                    enabled INTEGER DEFAULT 0,
                    subscription_until TEXT,
                    is_muted INTEGER DEFAULT 0
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS muted_users (
                    muter_id INTEGER,
                    muted_id INTEGER,
                    PRIMARY KEY (muter_id, muted_id)
                )
            """)
            
            # Проверяем колонки
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
            if "is_muted" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_muted INTEGER DEFAULT 0")
            if "subscription_until" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN subscription_until TEXT")

    def get_user(self, user_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def save_user(self, user_id, first_name, username):
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO users (user_id, first_name, username)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
            """, (user_id, first_name, username))

    def set_subscription(self, user_id, days):
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=days)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET subscription_until = ? WHERE user_id = ?",
                (until.isoformat(), user_id)
            )

    def is_subscribed(self, user_id):
        row = self.get_user(user_id)
        if not row or not row["subscription_until"]:
            return False
        try:
            until = datetime.fromisoformat(row["subscription_until"])
            return until > datetime.now(timezone.utc)
        except:
            return False

    def set_connection(self, user_id, conn_id):
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET business_connection_id = ? WHERE user_id = ?",
                (conn_id, user_id)
            )

    def set_enabled(self, user_id, enabled):
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET enabled = ? WHERE user_id = ?",
                (1 if enabled else 0, user_id)
            )

    def get_enabled_users(self):
        with self.connect() as conn:
            return conn.execute("""
                SELECT user_id, first_name, business_connection_id
                FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
            """).fetchall()

    def mute_user(self, muter_id, muted_id):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO muted_users (muter_id, muted_id) VALUES (?, ?)",
                (muter_id, muted_id)
            )
            conn.execute("UPDATE users SET is_muted = 1 WHERE user_id = ?", (muted_id,))

    def unmute_user(self, muter_id, muted_id):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM muted_users WHERE muter_id = ? AND muted_id = ?",
                (muter_id, muted_id)
            )
            count = conn.execute(
                "SELECT COUNT(*) as c FROM muted_users WHERE muted_id = ?",
                (muted_id,)
            ).fetchone()["c"]
            if count == 0:
                conn.execute("UPDATE users SET is_muted = 0 WHERE user_id = ?", (muted_id,))

    def is_muted(self, user_id):
        row = self.get_user(user_id)
        return bool(row and row["is_muted"])

    def get_muters(self, muted_id):
        with self.connect() as conn:
            return conn.execute(
                "SELECT muter_id FROM muted_users WHERE muted_id = ?",
                (muted_id,)
            ).fetchall()

    def count_users(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]

    def count_subscribed(self):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) as c FROM users WHERE subscription_until > ?",
                (now,)
            ).fetchone()["c"]

    def get_all_users(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users ORDER BY started_at DESC").fetchall()

    def get_all_muted(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM muted_users").fetchall()

db = Database()
db.init()

# ============================================================
# СОСТОЯНИЯ
# ============================================================

class States(StatesGroup):
    give_sub = State()

# ============================================================
# ЧАСЫ
# ============================================================

class Clock:
    def __init__(self, bot):
        self.bot = bot
        self.tz = timezone(timedelta(hours=3))
        self.last = {}

    def get_time(self):
        return datetime.now(self.tz).strftime("• [%H:%M]")

    async def apply(self, user_id, conn_id, first_name):
        if not conn_id:
            return
        label = self.get_time()
        if self.last.get(user_id) == label:
            return
        try:
            await self.bot(SetBusinessAccountName(
                business_connection_id=conn_id,
                first_name=first_name,
                last_name=label,
            ))
            self.last[user_id] = label
        except Exception as e:
            logger.error(f"Ошибка обновления {user_id}: {e}")
            db.set_enabled(user_id, False)

    async def clear(self, user_id, conn_id, first_name):
        if not conn_id:
            return
        try:
            await self.bot(SetBusinessAccountName(
                business_connection_id=conn_id,
                first_name=first_name,
                last_name="",
            ))
        except:
            pass
        self.last.pop(user_id, None)

clock = Clock(None)

# ============================================================
# КНОПКИ
# ============================================================

def make_btn(text, callback=None, url=None, copy=None, style="success"):
    """Создает кнопку с зеленым цветом по умолчанию"""
    kwargs = {"text": text}
    if style in ("primary", "danger", "success"):
        kwargs["style"] = style
    if callback:
        kwargs["callback_data"] = callback
    elif url:
        kwargs["url"] = url
    elif copy:
        kwargs["copy_text"] = CopyTextButton(text=copy)
    return InlineKeyboardButton(**kwargs)

def make_keyboard(buttons, row_width=1):
    """Создает клавиатуру из списка кнопок"""
    kb = []
    row = []
    for btn in buttons:
        row.append(btn)
        if len(row) >= row_width:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ============================================================
# ОБРАБОТЧИКИ
# ============================================================

async def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_main_buttons(user_id):
    """Главное меню"""
    buttons = []
    
    # Кнопка включения/выключения
    row = db.get_user(user_id)
    if row and row["enabled"]:
        buttons.append(make_btn("🔴 Выключить время", "toggle_off"))
    else:
        buttons.append(make_btn("🟢 Включить время", "toggle_on"))
    
    buttons.append(make_btn("📊 Моя подписка", "my_sub"))
    buttons.append(make_btn("🔇 Помощь", "help"))
    
    if is_admin(user_id):
        buttons.append(make_btn("👑 Админ-панель", "admin_panel"))
    
    return buttons

@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    db.save_user(user_id, message.from_user.first_name or "", message.from_user.username)
    
    # Админам - подписка автоматом
    if await is_admin(user_id):
        if not db.is_subscribed(user_id):
            db.set_subscription(user_id, ADMIN_SUBSCRIPTION_DAYS)
            await message.answer(
                f"👑 Вы админ! Подписка выдана автоматически на {ADMIN_SUBSCRIPTION_DAYS} дней!"
            )
            logger.info(f"Админу {user_id} выдана подписка")
    
    # Проверяем подписку
    if not db.is_subscribed(user_id):
        await message.answer(
            f"⭐ Добро пожаловать!\n\nПодписка: {SUBSCRIPTION_STARS} ⭐ в месяц",
            reply_markup=make_keyboard([
                make_btn(f"⭐ Оплатить {SUBSCRIPTION_STARS} ⭐", "pay")
            ])
        )
        return
    
    # Проверяем подключение
    row = db.get_user(user_id)
    if not row or not row["business_connection_id"]:
        await message.answer(
            "🔗 Подключите бота:\n1. Нажмите Подключить\n2. Скопируйте",
            reply_markup=make_keyboard([
                make_btn("🔗 Подключить", url="tg://settings/edit"),
                make_btn("📋 Скопировать", copy=f"@{bot.username}")
            ], row_width=2)
        )
        return
    
    # Главное меню
    await message.answer(
        "⏰ Управление ботом:",
        reply_markup=make_keyboard(get_main_buttons(user_id), row_width=2)
    )

# ============================================================
# ВКЛЮЧЕНИЕ/ВЫКЛЮЧЕНИЕ
# ============================================================

@dp.callback_query(F.data == "toggle_on")
async def toggle_on(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if not db.is_subscribed(user_id):
        await callback.answer("❌ Нет подписки!", True)
        return
    
    row = db.get_user(user_id)
    if not row or not row["business_connection_id"]:
        await callback.answer("❌ Бот не подключен!", True)
        return
    
    db.set_enabled(user_id, True)
    await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
    
    await callback.message.edit_text(
        "✅ Время включено!",
        reply_markup=make_keyboard(get_main_buttons(user_id), row_width=2)
    )
    await callback.answer()

@dp.callback_query(F.data == "toggle_off")
async def toggle_off(callback: CallbackQuery):
    user_id = callback.from_user.id
    row = db.get_user(user_id)
    
    if row and row["business_connection_id"]:
        await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
    
    db.set_enabled(user_id, False)
    
    await callback.message.edit_text(
        "❌ Время выключено",
        reply_markup=make_keyboard(get_main_buttons(user_id), row_width=2)
    )
    await callback.answer()

# ============================================================
# ПОДПИСКА
# ============================================================

@dp.callback_query(F.data == "my_sub")
async def my_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_subscribed(user_id):
        row = db.get_user(user_id)
        await callback.message.edit_text(
            f"✅ Подписка активна!\nДо: {row['subscription_until'][:10]}",
            reply_markup=make_keyboard([
                make_btn("⬅️ Назад", "back")
            ])
        )
    else:
        await callback.message.edit_text(
            f"❌ Нет подписки\n\nЦена: {SUBSCRIPTION_STARS} ⭐",
            reply_markup=make_keyboard([
                make_btn(f"⭐ Оплатить {SUBSCRIPTION_STARS} ⭐", "pay"),
                make_btn("⬅️ Назад", "back")
            ])
        )
    await callback.answer()

@dp.callback_query(F.data == "pay")
async def pay(callback: CallbackQuery):
    # Админам бесплатно
    if await is_admin(callback.from_user.id):
        db.set_subscription(callback.from_user.id, ADMIN_SUBSCRIPTION_DAYS)
        await callback.answer("👑 Админам бесплатно!", True)
        await my_sub(callback)
        return
    
    prices = [LabeledPrice(label="30 дней", amount=SUBSCRIPTION_STARS)]
    link = await callback.bot.create_invoice_link(
        title="TimeNick",
        description=f"Подписка {SUBSCRIPTION_STARS} ⭐",
        payload="sub",
        currency="XTR",
        prices=prices,
    )
    
    await callback.message.edit_text(
        f"💳 Оплатите {SUBSCRIPTION_STARS} ⭐:",
        reply_markup=make_keyboard([
            make_btn(f"⭐ Оплатить", url=link),
            make_btn("⬅️ Назад", "back")
        ])
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    user_id = message.from_user.id
    db.set_subscription(user_id, SUBSCRIPTION_DAYS)
    await message.answer("✅ Подписка активирована на 30 дней!\nИспользуйте /start")

# ============================================================
# МУТЫ
# ============================================================

@dp.message(Command("mute"))
async def mute_cmd(message: Message):
    user_id = message.from_user.id
    
    if not db.is_subscribed(user_id):
        await message.answer("❌ Нет подписки!")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ .mute @username или .mute 123456789")
        return
    
    target = args[1]
    if target.startswith("@"):
        target = target[1:]
    
    target_id = None
    try:
        target_id = int(target)
    except:
        for u in db.get_all_users():
            if u["username"] and u["username"].lower() == target.lower():
                target_id = u["user_id"]
                break
    
    if not target_id:
        await message.answer("❌ Пользователь не найден")
        return
    
    if target_id == user_id:
        await message.answer("❌ Нельзя замутить себя")
        return
    
    db.mute_user(user_id, target_id)
    await message.answer(f"🔇 Замучен! ID: {target_id}")

@dp.message(Command("unmute"))
async def unmute_cmd(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ .unmute @username")
        return
    
    target = args[1]
    if target.startswith("@"):
        target = target[1:]
    
    target_id = None
    try:
        target_id = int(target)
    except:
        for u in db.get_all_users():
            if u["username"] and u["username"].lower() == target.lower():
                target_id = u["user_id"]
                break
    
    if not target_id:
        await message.answer("❌ Пользователь не найден")
        return
    
    db.unmute_user(message.from_user.id, target_id)
    await message.answer(f"🔊 Размучен! ID: {target_id}")

@dp.callback_query(F.data == "help")
async def help_cmd(callback: CallbackQuery):
    text = "🔇 Команды:\n.mute @user - замутить\n.unmute @user - размутить"
    await callback.message.edit_text(
        text,
        reply_markup=make_keyboard([
            make_btn("⬅️ Назад", "back")
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):
    await start(callback.message)
    await callback.answer()

# Удаление сообщений от замученных
@dp.message(F.chat.type == "private")
async def delete_muted(message: Message):
    if db.is_muted(message.from_user.id):
        try:
            await message.delete()
        except:
            pass

# ============================================================
# БИЗНЕС-ПОДКЛЮЧЕНИЕ
# ============================================================

@dp.business_connection()
async def business_conn(connection: BusinessConnection):
    user_id = connection.user.id
    db.save_user(user_id, connection.user.first_name or "", connection.user.username)
    
    if connection.is_enabled:
        db.set_connection(user_id, connection.id)
        if not db.is_subscribed(user_id):
            try:
                await connection.bot.send_message(user_id,
                    "⭐ Требуется подписка",
                    reply_markup=make_keyboard([
                        make_btn("⭐ Оплатить", "pay")
                    ])
                )
            except:
                pass
            return
        
        try:
            await connection.bot.send_message(user_id, "✅ Бот подключен! /start")
        except:
            pass
    else:
        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        db.set_connection(user_id, None)
        db.set_enabled(user_id, False)

# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    user_id = message.from_user.id
    
    if not await is_admin(user_id):
        await message.answer(f"❌ Доступ запрещен! Ваш ID: {user_id}")
        return
    
    await message.answer(
        "👑 Админ-панель",
        reply_markup=make_keyboard([
            make_btn("📊 Статистика", "admin_stats"),
            make_btn("👥 Пользователи", "admin_users"),
            make_btn("⭐ Выдать подписку", "admin_give"),
            make_btn("🔇 Муты", "admin_mutes"),
            make_btn("⬅️ Назад", "back")
        ], row_width=2)
    )

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(callback: CallbackQuery):
    await admin_panel(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    
    total = db.count_users()
    subscribed = db.count_subscribed()
    muted = len(db.get_all_muted())
    
    await callback.message.edit_text(
        f"📊 Статистика:\n\n"
        f"👥 Всего: {total}\n"
        f"⭐ Подписок: {subscribed}\n"
        f"🔇 Замучено: {muted}",
        reply_markup=make_keyboard([
            make_btn("⬅️ Назад", "admin_panel")
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    
    users = db.get_all_users()
    text = "👥 Пользователи:\n\n"
    for u in users[:10]:
        sub = "✅" if db.is_subscribed(u["user_id"]) else "❌"
        text += f"{u['user_id']} {sub}\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=make_keyboard([
            make_btn("⬅️ Назад", "admin_panel")
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_give")
async def admin_give(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    
    await state.set_state(States.give_sub)
    await callback.message.answer(
        "⭐ Введите: ID_пользователя количество_дней\nПример: 123456789 30"
    )
    await callback.answer()

@dp.message(States.give_sub)
async def admin_give_do(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        days = int(parts[1])
        
        db.set_subscription(user_id, days)
        await message.answer(f"✅ Подписка выдана!\nID: {user_id}\nДней: {days}")
        
        try:
            await message.bot.send_message(user_id, f"⭐ Вам выдали подписку на {days} дней!")
        except:
            pass
        
    except:
        await message.answer("❌ Ошибка! Формат: ID_пользователя количество_дней")
    
    await state.clear()

@dp.callback_query(F.data == "admin_mutes")
async def admin_mutes(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    
    muted = db.get_all_muted()
    
    if not muted:
        text = "🔇 Нет замученных"
    else:
        text = f"🔇 Замученные ({len(muted)}):\n"
        for m in muted:
            text += f"{m['muter_id']} → {m['muted_id']}\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=make_keyboard([
            make_btn("🔄 Размутить всех", "admin_unmute_all"),
            make_btn("⬅️ Назад", "admin_panel")
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_unmute_all")
async def admin_unmute_all(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    
    muted = db.get_all_muted()
    for m in muted:
        db.unmute_user(m["muter_id"], m["muted_id"])
    
    await callback.answer(f"✅ Размучено {len(muted)}!", True)
    await admin_mutes(callback)

# ============================================================
# ФОНОВОЕ ОБНОВЛЕНИЕ
# ============================================================

async def update_loop():
    while True:
        try:
            now = datetime.now()
            next_min = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            await asyncio.sleep((next_min - now).total_seconds())
            
            for row in db.get_enabled_users():
                user_id = row["user_id"]
                
                if not db.is_subscribed(user_id):
                    db.set_enabled(user_id, False)
                    await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
                    continue
                
                await clock.apply(user_id, row["business_connection_id"], row["first_name"] or "")
                
        except Exception as e:
            logger.error(f"Update error: {e}")
            await asyncio.sleep(5)

# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    global bot
    
    bot = Bot(token=BOT_TOKEN)
    clock.bot = bot
    
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем обработчики
    dp.message.register(start, Command("start"))
    dp.message.register(admin_panel, Command("admin"))
    dp.message.register(mute_cmd, Command("mute"))
    dp.message.register(unmute_cmd, Command("unmute"))
    dp.message.register(delete_muted, F.chat.type == "private")
    dp.message.register(payment_ok, F.successful_payment)
    dp.pre_checkout_query.register(pre_checkout)
    dp.business_connection.register(business_conn)
    
    # Регистрируем callback'и
    dp.callback_query.register(toggle_on, F.data == "toggle_on")
    dp.callback_query.register(toggle_off, F.data == "toggle_off")
    dp.callback_query.register(my_sub, F.data == "my_sub")
    dp.callback_query.register(pay, F.data == "pay")
    dp.callback_query.register(help_cmd, F.data == "help")
    dp.callback_query.register(back, F.data == "back")
    dp.callback_query.register(admin_panel_cb, F.data == "admin_panel")
    dp.callback_query.register(admin_stats, F.data == "admin_stats")
    dp.callback_query.register(admin_users, F.data == "admin_users")
    dp.callback_query.register(admin_give, F.data == "admin_give")
    dp.callback_query.register(admin_mutes, F.data == "admin_mutes")
    dp.callback_query.register(admin_unmute_all, F.data == "admin_unmute_all")
    
    # Фоновая задача
    asyncio.create_task(update_loop())
    
    me = await bot.get_me()
    logger.info(f"✅ Бот @{me.username} запущен!")
    logger.info(f"👑 Админы: {ADMIN_IDS}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())