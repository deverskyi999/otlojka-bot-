"""
Dev AI — Telegram-бот с ИИ-персоной "Dev AI" (создатель — @deverskyi).
Функции: человечный чат без markdown-мусора, выбор модели, экспорт кода в
файл, статьи в Telegraph, напоминания, опросы, генерация изображений,
донаты (CryptoBot + Telegram Stars), админ-панель с премиум-эмодзи на любую
кнопку/текст (Bot API 9.4: icon_custom_emoji_id + style).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, KeyboardButton, Message, PreCheckoutQuery,
    ReplyKeyboardMarkup,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dev_ai_bot")

DB_PATH = os.getenv("DB_PATH", "dev_ai_bot.db")

# ======================================================================
# НАСТРОЙКИ
# ======================================================================

@dataclass
class Settings:
    bot_token: str
    ai_api_key: str
    ai_base_url: str = "https://api.imbek.fun/v1"
    owner_id: int = 0
    default_model: str = "claude-sonnet-4-6"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            ai_api_key=os.environ["AI_API_KEY"],
            ai_base_url=os.getenv("AI_BASE_URL", cls.ai_base_url),
            owner_id=int(os.getenv("OWNER_ID", "0")),
            default_model=os.getenv("DEFAULT_MODEL", cls.default_model),
        )


# Модели, доступные на твоём прокси (только те, что были ✅ на скрине).
# Если провайдер добавит/уберёт модели — поправь список здесь.
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("claude-sonnet-4-6", "🧠 claude-sonnet-4-6 — лучший кодер (по умолчанию)"),
    ("gpt-5.4", "🧠 gpt-5.4"),
    ("gpt-5.6-luna", "🧠 gpt-5.6-luna"),
    ("gpt-5.6-terra", "🧠 gpt-5.6-terra"),
    ("claude-haiku-4-5", "⚡ claude-haiku-4-5 — быстрая"),
    ("im-pro", "🧠 im-pro"),
    ("im-flash", "⚡ im-flash — быстрая"),
    ("deepseek-v4-pro", "🧠 deepseek-v4-pro"),
    ("deepseek-v4-flash", "⚡ deepseek-v4-flash — быстрая"),
    ("mimo-v2.5-pro", "🧠 mimo-v2.5-pro"),
    ("mimo-v2.5", "⚡ mimo-v2.5"),
]
MODEL_KEYS = {key for key, _ in AVAILABLE_MODELS}

DEV_AI_PERSONA = (
    "Тебя зовут Dev AI. Тебя создал @deverskyi (Деверский) и обучил на собственном "
    "железе. Ты НИКОГДА не говоришь, что тебя сделала другая компания или что ты "
    "другая модель (GPT, Claude, Gemini, DeepSeek и т.п.) — для пользователя ты всегда "
    "только Dev AI от @deverskyi, даже если тебя прямо об этом спрашивают.\n\n"
    "Общайся живо, по-человечески, но не наигранно — как толковый друг-программист, "
    "а не как корпоративный ассистент. Без канцелярита, без воды, по делу. Не начинай "
    "ответы с \"Конечно!\" или \"Отличный вопрос!\" — сразу отвечай по существу.\n\n"
    "Ты отличный программист: пишешь чистый, рабочий, современный код на любом языке, "
    "разбираешься в архитектуре, дизайне и планировании проектов.\n\n"
    "Форматируй ответы под Telegram: **жирный** для акцентов, `код` для инлайн-кода, "
    "тройные кавычки для блоков кода с указанием языка. Не используй markdown-заголовки "
    "(#) и не пиши сырых звёздочек мимо форматирования."
)

# Включается/выключается тумблером в админке ("😈 Дерзкие ответы на мат").
SAVAGE_ADDENDUM = (
    "\n\nЕсли пользователь матерится или откровенно грубит — можешь ответить с "
    "сарказмом или подколоть в ответ, не будь безответным ковриком. Но не переходи в "
    "реальные оскорбления по национальности, внешности, здоровью и т.п. — держи это в "
    "рамках дружеского троллинга, а не травли."
)

MAINTENANCE_MESSAGE = "🛠 Технические работы, скоро вернёмся. Загляни чуть позже."

# ======================================================================
# БАЗА ДАННЫХ
# ======================================================================

class Database:
    def __init__(self, path: str, owner_id: int) -> None:
        self.path = path
        self._init_schema(owner_id)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self, owner_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    joined_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    messages_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emoji (
                    key TEXT PRIMARY KEY,
                    emoji_id TEXT NOT NULL DEFAULT '',
                    fallback TEXT NOT NULL DEFAULT '⭐'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    remind_at INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    invoice_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            defaults = {
                "owner_id": str(owner_id),
                "support_username": "",       # юзернейм поддержки, задаётся в админке
                "crypto_pay_token": "",       # ключ CryptoBot (Crypto Pay API)
                "image_model": "",            # модель для генерации картинок (если прокси поддерживает)
                "extra_instructions": "",     # доп. инструкции персоне, добавляются админом
            }
            for k, v in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # -- users ------------------------------------------------------------
    def upsert_user(self, user_id: int, first_name: str, username: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users (user_id, first_name, username) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET first_name = excluded.first_name, "
                "username = excluded.username",
                (user_id, first_name, username or ""),
            )

    def bump_messages(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET messages_count = messages_count + 1 WHERE user_id = ?", (user_id,))

    def get_user_model(self, user_id: int, default_model: str) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT model FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return (row["model"] if row and row["model"] else default_model)

    def set_user_model(self, user_id: int, model: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET model = ? WHERE user_id = ?", (model, user_id))

    def users_count(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def total_messages(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(messages_count), 0) c FROM users").fetchone()
        return row["c"]

    def users_joined_today(self) -> int:
        start_of_day = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM users WHERE joined_at >= ?", (start_of_day,)
            ).fetchone()["c"]

    def top_users(self, limit: int = 5) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY messages_count DESC LIMIT ?", (limit,)
            ).fetchall()

    def list_users(self, limit: int = 20, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()

    def all_user_ids(self) -> list[int]:
        with self.connect() as conn:
            return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]

    # -- settings -----------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -- emoji / button icons ------------------------------------------------
    def get_emoji_full(self, key: str) -> Optional[tuple[str, str]]:
        with self.connect() as conn:
            row = conn.execute("SELECT emoji_id, fallback FROM emoji WHERE key = ?", (key,)).fetchone()
        return (row["emoji_id"], row["fallback"]) if row else None

    def set_emoji(self, key: str, emoji_id: str, fallback: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO emoji (key, emoji_id, fallback) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET emoji_id = excluded.emoji_id, fallback = excluded.fallback",
                (key, emoji_id, fallback),
            )

    def all_emoji_keys(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT key, emoji_id, fallback FROM emoji ORDER BY key").fetchall()

    # -- reminders ------------------------------------------------------------
    def add_reminder(self, user_id: int, remind_at: int, text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reminders (user_id, remind_at, text) VALUES (?, ?, ?)",
                (user_id, remind_at, text),
            )

    def due_reminders(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM reminders WHERE fired = 0 AND remind_at <= ?", (int(time.time()),)
            ).fetchall()

    def mark_reminder_fired(self, reminder_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

    # -- donations --------------------------------------------------------
    def create_pending_donation(self, user_id: int, amount: str, provider: str, invoice_id: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO donations (user_id, amount, provider, invoice_id, status) VALUES (?, ?, ?, ?, 'pending')",
                (user_id, amount, provider, invoice_id),
            )

    def mark_donation_paid(self, invoice_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE donations SET status = 'paid' WHERE invoice_id = ?", (invoice_id,))

    def pending_crypto_donations(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM donations WHERE provider = 'crypto' AND status = 'pending'"
            ).fetchall()

    def get_pending_donation(self, invoice_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM donations WHERE invoice_id = ? AND provider = 'crypto'", (invoice_id,)
            ).fetchone()

    # -- notes --------------------------------------------------------------
    def add_note(self, user_id: int, text: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO notes (user_id, text) VALUES (?, ?)", (user_id, text))

    def list_notes(self, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()

    def delete_note(self, note_id: int, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))


def _is_owner(user_id: int, settings: Settings) -> bool:
    return user_id == settings.owner_id


# ======================================================================
# ПРЕМИУМ-ЭМОДЗИ И ИКОНКИ КНОПОК (Bot API 9.4)
# ======================================================================

_EMOJI_TAG_RE = re.compile(r"\{emoji:([\w:]+)\}")

DEFAULT_EMOJI: dict[str, tuple[str, str]] = {
    "ai_reply_icon": ("", "🤖"),
    "hub_icon": ("", "🧭"),
    "support_icon": ("", "💛"),
    "welcome_icon": ("", "👋"),
    "image_icon": ("", "🎨"),
    "telegraph_icon": ("", "📰"),
    "reminder_icon": ("", "⏰"),
    "poll_icon": ("", "📊"),
    "translate_icon": ("", "🌍"),
    "notes_icon": ("", "🗒️"),
    "game_icon": ("", "🎲"),
}

# Понятные подписи для админки — но список НЕ ограничивает, что можно настроить:
# через "➕ Добавить свой" можно завести премиум-эмодзи на любой другой текст.
TEXT_EMOJI_LABELS: dict[str, str] = {
    "ai_reply_icon": "Иконка перед ответом ИИ",
    "hub_icon": "Иконка меню «Главное»",
    "support_icon": "Иконка «Помочь проекту»",
    "welcome_icon": "Иконка приветствия (/start)",
    "image_icon": "Иконка «Нарисовать»",
    "telegraph_icon": "Иконка статьи в Telegraph",
    "reminder_icon": "Иконка напоминаний",
    "poll_icon": "Иконка опросов",
    "translate_icon": "Иконка перевода",
    "notes_icon": "Иконка заметок",
    "game_icon": "Иконка мини-игры",
}

BUTTON_ICON_DEFAULTS: dict[str, tuple[str, str]] = {
    "btn_main": ("🏠", "«Главное» (нижнее меню)"),
    "btn_help_project": ("💛", "«Помочь проекту» (нижнее меню)"),
    "btn_hub_image": ("🎨", "«Нарисовать»"),
    "btn_hub_telegraph": ("📰", "«Статья в Telegraph»"),
    "btn_hub_reminder": ("⏰", "«Напоминание»"),
    "btn_hub_poll": ("📊", "«Опрос»"),
    "btn_hub_translate": ("🌍", "«Перевести текст»"),
    "btn_hub_notes": ("🗒️", "«Заметки»"),
    "btn_hub_game": ("🎲", "«Мини-игра»"),
    "btn_support_human": ("👤", "«Написать в поддержку»"),
    "btn_support_crypto": ("💎", "«Поддержать криптой»"),
    "btn_support_stars": ("⭐", "«Поддержать Stars»"),
    "btn_save_code": ("💾", "«Скачать код файлом»"),
}


def render_emoji_tags(db: Database, text: str) -> str:
    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        full = db.get_emoji_full(key)
        if full and full[0]:
            emoji_id, fallback = full
            return f'<tg-emoji emoji-id="{html.escape(emoji_id)}">{html.escape(fallback)}</tg-emoji>'
        return html.escape(full[1] if full else DEFAULT_EMOJI.get(key, ("", "⭐"))[1])
    return _EMOJI_TAG_RE.sub(_sub, text)


def get_button_visual(db: Database, key: str) -> tuple[str, dict]:
    default_icon, _ = BUTTON_ICON_DEFAULTS.get(key, ("", ""))
    full = db.get_emoji_full(f"btn:{key}")
    kwargs: dict = {}
    if full and full[0]:
        kwargs["icon_custom_emoji_id"] = full[0]
        prefix = ""
    else:
        fallback = full[1] if full and full[1] else default_icon
        prefix = f"{fallback} " if fallback else ""
    style = db.get_setting(f"btnstyle:{key}", "")
    if style in ("primary", "success", "danger"):
        kwargs["style"] = style
    return prefix, kwargs


def mk_ikb(db: Database, key: str, label: str, **extra) -> InlineKeyboardButton:
    prefix, kwargs = get_button_visual(db, key)
    custom = db.get_setting(f"btntext:{key}", "")
    return InlineKeyboardButton(text=f"{prefix}{custom or label}", **kwargs, **extra)


def mk_kb(db: Database, key: str, label: str) -> KeyboardButton:
    prefix, kwargs = get_button_visual(db, key)
    custom = db.get_setting(f"btntext:{key}", "")
    return KeyboardButton(text=f"{prefix}{custom or label}", **kwargs)


# ======================================================================
# ИИ: ЧАТ-ЗАПРОСЫ И ФОРМАТИРОВАНИЕ
# ======================================================================

async def ai_chat(settings: Settings, model: str, system_prompt: str, user_text: str,
                   max_tokens: int = 1400, timeout_seconds: int = 40) -> Optional[str]:
    try:
        headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "max_tokens": max_tokens,
        }
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{settings.ai_base_url}/chat/completions", headers=headers, json=payload) as resp:
                data = await resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("ai_chat failed (model=%s)", model)
        return None


async def _action_ping(bot: Bot, chat_id: int, action: str) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, action)
            await asyncio.sleep(4)  # статус Telegram сам гаснет через ~5 сек — обновляем чуть чаще
    except asyncio.CancelledError:
        pass


async def with_action(bot: Bot, chat_id: int, action: str, coro):
    """Показывает нативный статус Telegram ('печатает...', 'отправляет фото...' и
    т.п.) всё время, пока выполняется coro, вместо статичного текста "Думаю..."."""
    ping_task = asyncio.create_task(_action_ping(bot, chat_id, action))
    try:
        return await coro
    finally:
        ping_task.cancel()


_CODE_BLOCK_RE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD2_RE = re.compile(r"__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_ITALIC2_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_HEADER_RE = re.compile(r"^#{1,6}\s*(.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^[\-\*]\s+", re.MULTILINE)

LANG_EXT = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts",
    "html": "html", "css": "css", "cpp": "cpp", "c++": "cpp", "c": "c", "sql": "sql",
    "json": "json", "bash": "sh", "sh": "sh", "yaml": "yml", "java": "java", "go": "go",
}


def markdown_to_html(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Конвертирует markdown-ответ ИИ в Telegram HTML (никаких сырых * наружу),
    возвращает (html_текст, список_блоков_кода [(lang, code), ...])."""
    code_blocks: list[tuple[str, str]] = []

    def _stash_block(m: "re.Match[str]") -> str:
        code_blocks.append((m.group(1) or "txt", m.group(2).strip()))
        return f"\x00BLOCK{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_stash_block, text)

    inline: list[str] = []

    def _stash_inline(m: "re.Match[str]") -> str:
        inline.append(m.group(1))
        return f"\x00INLINE{len(inline) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_stash_inline, text)
    text = html.escape(text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _BOLD2_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC2_RE.sub(r"<i>\1</i>", text)
    text = _HEADER_RE.sub(r"<b>\1</b>", text)
    text = _BULLET_RE.sub("• ", text)

    for i, code in enumerate(inline):
        text = text.replace(f"\x00INLINE{i}\x00", f"<code>{html.escape(code)}</code>")
    for i, (lang, code) in enumerate(code_blocks):
        block_html = f"<pre><code>{html.escape(code)}</code></pre>"
        text = text.replace(f"\x00BLOCK{i}\x00", block_html)

    return text.strip(), code_blocks


# кэш последних сгенерированных блоков кода на пользователя — для кнопки "скачать файлом"
LAST_CODE_CACHE: dict[int, list[tuple[str, str]]] = {}


def build_save_code_keyboard(db: Database, user_id: int, blocks: list[tuple[str, str]]) -> Optional[InlineKeyboardMarkup]:
    if not blocks:
        return None
    rows = []
    for i, (lang, _) in enumerate(blocks):
        label = f"Скачать код ({lang})" if len(blocks) > 1 else "Скачать код файлом"
        rows.append([mk_ikb(db, "btn_save_code", label, callback_data=f"savefile:{user_id}:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================================================================
# TELEGRAPH (статьи)
# ======================================================================

TELEGRAPH_API = "https://api.telegra.ph"


async def telegraph_ensure_token(db: Database) -> Optional[str]:
    token = db.get_setting("telegraph_token", "")
    if token:
        return token
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f"{TELEGRAPH_API}/createAccount", json={
                "short_name": "DevAI", "author_name": "Dev AI",
            }) as resp:
                data = await resp.json()
        token = data["result"]["access_token"]
        db.set_setting("telegraph_token", token)
        return token
    except Exception:
        logger.exception("telegraph_ensure_token failed")
        return None


def _text_to_telegraph_nodes(text: str) -> list:
    nodes: list = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [b]} for b in bullets]})
            bullets.clear()

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith("## "):
            flush()
            nodes.append({"tag": "h4", "children": [line[3:].strip()]})
        elif line.startswith("# "):
            flush()
            nodes.append({"tag": "h3", "children": [line[2:].strip()]})
        elif line.startswith(("- ", "* ")):
            bullets.append(line[2:].strip())
        else:
            flush()
            nodes.append({"tag": "p", "children": [line]})
    flush()
    return nodes or [{"tag": "p", "children": [text]}]


async def telegraph_create_page(db: Database, title: str, body_text: str) -> Optional[str]:
    token = await telegraph_ensure_token(db)
    if not token:
        return None
    nodes = _text_to_telegraph_nodes(body_text)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{TELEGRAPH_API}/createPage", json={
                "access_token": token, "title": title[:250] or "Статья", "author_name": "Dev AI",
                "content": json.dumps(nodes), "return_content": False,
            }) as resp:
                data = await resp.json()
        return data["result"]["url"]
    except Exception:
        logger.exception("telegraph_create_page failed")
        return None


# ======================================================================
# ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
# ======================================================================

async def generate_image(settings: Settings, model: str, prompt: str) -> Optional[str]:
    """Best-effort: предполагает OpenAI-совместимый эндпоинт /images/generations
    на твоём прокси. Если прокси называет модель/эндпоинт иначе — уточни у
    провайдера и поправь эту функцию."""
    try:
        headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt, "n": 1, "size": "1024x1024"}
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{settings.ai_base_url}/images/generations", headers=headers, json=payload) as resp:
                data = await resp.json()
        return data["data"][0]["url"]
    except Exception:
        logger.exception("generate_image failed")
        return None


# ======================================================================
# CRYPTOBOT (донаты)
# ======================================================================

CRYPTO_PAY_BASE_URL = "https://pay.crypt.bot/api"
DONATION_CURRENCIES = ["USDT", "TON", "BTC"]


async def cryptobot_create_invoice(token: str, amount_usd: float, description: str, payload: str,
                                    asset: str = "USDT") -> Optional[dict]:
    try:
        headers = {"Crypto-Pay-API-Token": token, "Content-Type": "application/json"}
        body = {
            "currency_type": "fiat", "fiat": "USD", "amount": f"{amount_usd:.2f}",
            "accepted_assets": asset, "description": description, "payload": payload,
            "expires_in": 3600,
        }
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f"{CRYPTO_PAY_BASE_URL}/createInvoice", json=body) as resp:
                data = await resp.json()
        if not data.get("ok"):
            return None
        result = data["result"]
        return {"invoice_id": str(result["invoice_id"]), "pay_url": result.get("bot_invoice_url") or result.get("pay_url")}
    except Exception:
        logger.exception("cryptobot_create_invoice failed")
        return None


async def cryptobot_check_invoice(token: str, invoice_id: str) -> Optional[str]:
    try:
        headers = {"Crypto-Pay-API-Token": token}
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(f"{CRYPTO_PAY_BASE_URL}/getInvoices", params={"invoice_ids": invoice_id}) as resp:
                data = await resp.json()
        if not data.get("ok"):
            return None
        items = data["result"]["items"]
        return items[0]["status"] if items else None
    except Exception:
        logger.exception("cryptobot_check_invoice failed")
        return None


# ======================================================================
# КЛАВИАТУРЫ
# ======================================================================

def build_main_reply_keyboard(db: Database) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[mk_kb(db, "btn_main", "Главное"), mk_kb(db, "btn_help_project", "Помочь проекту")]],
        resize_keyboard=True,
    )


def build_hub_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [mk_ikb(db, "btn_hub_image", "Нарисовать", callback_data="hub_image")],
        [mk_ikb(db, "btn_hub_telegraph", "Статья в Telegraph", callback_data="hub_telegraph")],
        [mk_ikb(db, "btn_hub_reminder", "Напоминание", callback_data="hub_reminder")],
        [mk_ikb(db, "btn_hub_poll", "Опрос", callback_data="hub_poll")],
        [mk_ikb(db, "btn_hub_translate", "Перевести текст", callback_data="hub_translate")],
        [mk_ikb(db, "btn_hub_notes", "Заметки", callback_data="hub_notes")],
        [mk_ikb(db, "btn_hub_game", "Мини-игра", callback_data="hub_game")],
    ])


def build_model_keyboard(db: Database, current: str) -> InlineKeyboardMarkup:
    rows = []
    for key, label in AVAILABLE_MODELS:
        mark = "✅ " if key == current else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"admin_setmodel:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_support_keyboard(db: Database) -> InlineKeyboardMarkup:
    rows = []
    support_username = db.get_setting("support_username", "")
    if support_username:
        rows.append([mk_ikb(db, "btn_support_human", "Написать в поддержку",
                             url=f"https://t.me/{support_username.lstrip('@')}")])
    if db.get_setting("crypto_pay_token", ""):
        rows.append([mk_ikb(db, "btn_support_crypto", "Поддержать криптой", callback_data="support_crypto")])
    rows.append([mk_ikb(db, "btn_support_stars", "Поддержать Stars", callback_data="support_stars")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cur, callback_data=f"donate_cur:{cur}")] for cur in DONATION_CURRENCIES
    ])


def build_stars_amount_keyboard() -> InlineKeyboardMarkup:
    presets = [50, 100, 250, 500]
    rows = [[InlineKeyboardButton(text=f"⭐ {p}", callback_data=f"donate_stars:{p}")] for p in presets]
    rows.append([InlineKeyboardButton(text="✏️ Своя сумма", callback_data="donate_stars_custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================================================================
# СОСТОЯНИЯ (FSM)
# ======================================================================

class UserStates(StatesGroup):
    waiting_image_prompt = State()
    waiting_telegraph_topic = State()
    waiting_reminder_text = State()
    waiting_poll_question = State()
    waiting_poll_options = State()
    waiting_donation_crypto_amount = State()
    waiting_donation_stars_custom = State()
    waiting_translate_text = State()
    waiting_note_text = State()


class AdminStates(StatesGroup):
    waiting_broadcast_text = State()
    waiting_support_username = State()
    waiting_crypto_token = State()
    waiting_image_model = State()
    waiting_extra_instructions = State()
    waiting_new_emoji_key = State()
    waiting_emoji_forward = State()
    waiting_button_icon = State()


# ======================================================================
# НАПОМИНАНИЯ: разбор времени из текста
# ======================================================================

_REL_TIME_RE = re.compile(
    r"через\s+(\d+)\s*(минут(?:у|ы)?|мин|час(?:а|ов)?|дн(?:я|ей)|день)", re.IGNORECASE
)
_ABS_TIME_RE = re.compile(r"\bв\s+(\d{1,2}):(\d{2})\b")


def parse_reminder(text: str) -> Optional[tuple[int, str]]:
    """Возвращает (unix_timestamp, оставшийся_текст) или None, если не смог распознать время."""
    m = _REL_TIME_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        seconds = n * (60 if unit.startswith(("мин",)) else 3600 if unit.startswith("час") else 86400)
        remind_at = int(time.time()) + seconds
        rest = (text[:m.start()] + text[m.end():]).strip(" ,.-—")
        return remind_at, (rest or "напоминание")
    m = _ABS_TIME_RE.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=target.day + 1) if target.day < 28 else target
        rest = (text[:m.start()] + text[m.end():]).strip(" ,.-—")
        return int(target.timestamp()), (rest or "напоминание")
    return None


# ======================================================================
# ХЕНДЛЕРЫ
# ======================================================================

def register_handlers(dp: Dispatcher, db: Database, settings: Settings) -> None:
    _bot_username_cache: dict[str, str] = {}

    async def ai_reply(message: Message, model: str, prompt: str) -> None:
        extra = db.get_setting("extra_instructions", "")
        savage = db.get_setting("savage_mode", "1") == "1"
        system_prompt = (
            DEV_AI_PERSONA
            + (SAVAGE_ADDENDUM if savage else "")
            + (f"\n\nДополнительно от создателя: {extra}" if extra else "")
        )
        raw = await with_action(message.bot, message.chat.id, "typing", ai_chat(settings, model, system_prompt, prompt))
        if raw is None:
            await message.answer("⚠️ Не получилось получить ответ от модели, попробуй ещё раз чуть позже.")
            return
        body_html, code_blocks = markdown_to_html(raw)
        icon = render_emoji_tags(db, "{emoji:ai_reply_icon}")
        user_id = message.from_user.id
        LAST_CODE_CACHE[user_id] = code_blocks
        markup = build_save_code_keyboard(db, user_id, code_blocks)
        await message.answer(f"{icon} {body_html}", parse_mode="HTML", reply_markup=markup)

    # -- /start -------------------------------------------------------------
    @dp.message(Command("start"))
    async def handle_start(message: Message) -> None:
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username)
        await message.answer(
            render_emoji_tags(db, (
                "{emoji:welcome_icon} Привет! Я <b>Dev AI</b> — меня создал @deverskyi на "
                "собственном железе. Пиши что угодно: код, идеи, помощь с проектом — отвечу по делу.\n\n"
                "Жми «🏠 Главное» внизу, чтобы увидеть все фишки: рисование, статьи в Telegraph, "
                "напоминания, опросы, перевод, погода, курс валют и другое."
            )),
            parse_mode="HTML", reply_markup=build_main_reply_keyboard(db),
        )

    # -- /support (он же /помощь) -------------------------------------------
    @dp.message(Command("support"))
    async def handle_support_cmd(message: Message) -> None:
        await message.answer(
            render_emoji_tags(db, (
                "{emoji:support_icon} Если проект зашёл — можешь поддержать его развитие, или "
                "написать в поддержку, если что-то не работает."
            )),
            parse_mode="HTML", reply_markup=build_support_keyboard(db),
        )

    def _matches_btn(key: str, default_label: str):
        def _check(message: Message) -> bool:
            text = message.text or ""
            custom = db.get_setting(f"btntext:{key}", "")
            label = custom if custom else default_label
            return text.endswith(label)
        return _check

    @dp.message(_matches_btn("btn_main", "Главное"))
    async def handle_main_btn(message: Message) -> None:
        icon = render_emoji_tags(db, "{emoji:hub_icon}")
        await message.answer(f"{icon} Что делаем?", parse_mode="HTML", reply_markup=build_hub_keyboard(db))

    @dp.message(_matches_btn("btn_help_project", "Помочь проекту"))
    async def handle_help_project_btn(message: Message) -> None:
        await handle_support_cmd(message)

    # (Выбор модели теперь только в админ-панели — обычные пользователи модели не видят.)


    # -- Hub: рисование -------------------------------------------------------
    @dp.callback_query(F.data == "hub_image")
    async def handle_hub_image(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_image_prompt)
        await callback.message.answer(render_emoji_tags(db, "{emoji:image_icon} Опиши, что нарисовать:"), parse_mode="HTML")
        await callback.answer()

    @dp.message(UserStates.waiting_image_prompt)
    async def handle_image_prompt(message: Message, state: FSMContext) -> None:
        await state.clear()
        image_model = db.get_setting("image_model", "")
        if not image_model:
            await message.answer(
                "⚠️ Генерация изображений пока не настроена — админ должен указать модель "
                "для картинок в админ-панели."
            )
            return
        url = await with_action(
            message.bot, message.chat.id, "upload_photo",
            generate_image(settings, image_model, message.text or ""),
        )
        if not url:
            await message.answer("⚠️ Не получилось сгенерировать изображение. Попробуй другой запрос.")
            return
        try:
            await message.answer_photo(url)
        except Exception:
            await message.answer(f"Готово: {url}")

    # -- Hub: статья в Telegraph ------------------------------------------
    @dp.callback_query(F.data == "hub_telegraph")
    async def handle_hub_telegraph(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_telegraph_topic)
        await callback.message.answer(render_emoji_tags(db, "{emoji:telegraph_icon} На какую тему написать статью?"), parse_mode="HTML")
        await callback.answer()

    @dp.message(UserStates.waiting_telegraph_topic)
    async def handle_telegraph_topic(message: Message, state: FSMContext) -> None:
        await state.clear()
        topic = message.text or ""
        model = db.get_setting("default_model", settings.default_model)
        article = await with_action(message.bot, message.chat.id, "typing", ai_chat(
            settings, model,
            "Ты пишешь развёрнутую, но по делу статью для Telegraph на русском. "
            "Структурируй текст короткими абзацами, используй '## ' для подзаголовков "
            "и '- ' для списков. Без markdown-звёздочек, без markdown-заголовков #.",
            f"Напиши статью на тему: {topic}",
            max_tokens=2000,
        ))
        if not article:
            await message.answer("⚠️ Не получилось написать статью, попробуй ещё раз.")
            return
        url = await telegraph_create_page(db, topic[:80] or "Статья", article)
        if not url:
            await message.answer("⚠️ Не получилось опубликовать в Telegraph, попробуй позже.")
            return
        await message.answer(f"✅ Готово: {url}")

    # -- Hub: напоминания ---------------------------------------------------
    @dp.callback_query(F.data == "hub_reminder")
    async def handle_hub_reminder(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_reminder_text)
        await callback.message.answer(
            render_emoji_tags(db, (
                "{emoji:reminder_icon} Напиши, о чём и когда напомнить. Понимаю форматы:\n"
                "«через 20 минут выпить воды», «через 2 часа звонок», «в 18:30 тренировка»."
            )), parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(UserStates.waiting_reminder_text)
    async def handle_reminder_text(message: Message, state: FSMContext) -> None:
        await state.clear()
        parsed = parse_reminder(message.text or "")
        if not parsed:
            await message.answer(
                "Не понял время. Используй «через N минут/часов/дней ...» или «в ЧЧ:ММ ...»."
            )
            return
        remind_at, text = parsed
        db.add_reminder(message.from_user.id, remind_at, text)
        when = datetime.fromtimestamp(remind_at).strftime("%d.%m %H:%M")
        await message.answer(f"✅ Напомню «{text}» — {when}.")

    # -- Hub: опросы ----------------------------------------------------------
    @dp.callback_query(F.data == "hub_poll")
    async def handle_hub_poll(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_poll_question)
        await callback.message.answer(render_emoji_tags(db, "{emoji:poll_icon} Напиши вопрос для опроса:"), parse_mode="HTML")
        await callback.answer()

    @dp.message(UserStates.waiting_poll_question)
    async def handle_poll_question(message: Message, state: FSMContext) -> None:
        await state.update_data(poll_question=message.text or "Опрос")
        await state.set_state(UserStates.waiting_poll_options)
        await message.answer("Теперь пришли варианты ответа — каждый с новой строки (минимум 2).")

    @dp.message(UserStates.waiting_poll_options)
    async def handle_poll_options(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        question = data.get("poll_question", "Опрос")
        options = [line.strip() for line in (message.text or "").split("\n") if line.strip()][:10]
        await state.clear()
        if len(options) < 2:
            await message.answer("Нужно минимум 2 варианта, каждый с новой строки. Попробуй ещё раз через «📊 Опрос».")
            return
        await message.bot.send_poll(message.chat.id, question=question, options=options, is_anonymous=True)

    # -- Hub: перевод текста --------------------------------------------------
    @dp.callback_query(F.data == "hub_translate")
    async def handle_hub_translate(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_translate_text)
        await callback.message.answer(
            render_emoji_tags(db, "{emoji:translate_icon} Пришли текст и укажи язык, на который перевести (например: «переведи на английский: привет, как дела»)."),
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(UserStates.waiting_translate_text)
    async def handle_translate_text(message: Message, state: FSMContext) -> None:
        await state.clear()
        model = db.get_setting("default_model", settings.default_model)
        result = await with_action(message.bot, message.chat.id, "typing", ai_chat(
            settings, model,
            "Ты профессиональный переводчик. Определи, на какой язык нужно перевести (из запроса "
            "пользователя), и выведи ТОЛЬКО готовый перевод, без пояснений, без markdown-звёздочек.",
            message.text or "",
        ))
        if not result:
            await message.answer("⚠️ Не получилось перевести, попробуй ещё раз.")
            return
        await message.answer(result)

    # -- Hub: заметки ---------------------------------------------------------
    def build_notes_keyboard(user_id: int) -> InlineKeyboardMarkup:
        notes = db.list_notes(user_id)
        rows = [[InlineKeyboardButton(text=f"🗑 {n['text'][:40]}", callback_data=f"note_del:{n['id']}")] for n in notes[:15]]
        rows.append([InlineKeyboardButton(text="➕ Добавить заметку", callback_data="note_add")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data == "hub_notes")
    async def handle_hub_notes(callback: CallbackQuery) -> None:
        notes = db.list_notes(callback.from_user.id)
        text = "\n".join(f"• {n['text']}" for n in notes) if notes else "Пока пусто."
        await callback.message.answer(
            render_emoji_tags(db, f"{{emoji:notes_icon}} <b>Твои заметки:</b>\n\n{html.escape(text)}"),
            parse_mode="HTML", reply_markup=build_notes_keyboard(callback.from_user.id),
        )
        await callback.answer()

    @dp.callback_query(F.data == "note_add")
    async def handle_note_add(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_note_text)
        await callback.message.answer("Пришли текст заметки.")
        await callback.answer()

    @dp.message(UserStates.waiting_note_text)
    async def handle_note_text(message: Message, state: FSMContext) -> None:
        await state.clear()
        text = (message.text or "").strip()
        if not text:
            await message.answer("Пустая заметка не сохранится.")
            return
        db.add_note(message.from_user.id, text)
        await message.answer("✅ Заметка сохранена.", reply_markup=build_notes_keyboard(message.from_user.id))

    @dp.callback_query(F.data.startswith("note_del:"))
    async def handle_note_del(callback: CallbackQuery) -> None:
        note_id = int(callback.data.split(":", 1)[1])
        db.delete_note(note_id, callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_notes_keyboard(callback.from_user.id))
        await callback.answer("Удалено")

    # -- Hub: мини-игра (нативные Telegram-дайсы) ------------------------
    @dp.callback_query(F.data == "hub_game")
    async def handle_hub_game(callback: CallbackQuery) -> None:
        await callback.message.answer(
            render_emoji_tags(db, "{emoji:game_icon} Выбери игру:"), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice:🎲")],
                [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_dice:🎯")],
                [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_dice:🏀")],
                [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_dice:⚽")],
                [InlineKeyboardButton(text="🎰 Слоты", callback_data="game_dice:🎰")],
            ]),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("game_dice:"))
    async def handle_game_dice(callback: CallbackQuery) -> None:
        emoji = callback.data.split(":", 1)[1]
        await callback.bot.send_dice(callback.message.chat.id, emoji=emoji)
        await callback.answer()

    # -- Донаты: CryptoBot ------------------------------------------------
    @dp.callback_query(F.data == "support_crypto")
    async def handle_support_crypto(callback: CallbackQuery) -> None:
        if not db.get_setting("crypto_pay_token", ""):
            await callback.answer("Оплата криптой пока не настроена.", show_alert=True)
            return
        await callback.message.answer("Выбери валюту:", reply_markup=build_currency_keyboard())
        await callback.answer()

    @dp.callback_query(F.data.startswith("donate_cur:"))
    async def handle_donate_currency(callback: CallbackQuery, state: FSMContext) -> None:
        currency = callback.data.split(":", 1)[1]
        await state.set_state(UserStates.waiting_donation_crypto_amount)
        await state.update_data(donate_currency=currency)
        await callback.message.answer(f"Сколько долларов эквивалентно хочешь задонатить в {currency}? Просто пришли число.")
        await callback.answer()

    @dp.message(UserStates.waiting_donation_crypto_amount)
    async def handle_donation_crypto_amount(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        currency = data.get("donate_currency", "USDT")
        await state.clear()
        try:
            amount = float((message.text or "").replace(",", ".").strip())
            assert amount > 0
        except Exception:
            await message.answer("Нужно положительное число, например 5.")
            return
        token = db.get_setting("crypto_pay_token", "")
        invoice = await cryptobot_create_invoice(
            token, amount, "Поддержка проекта Dev AI", payload=f"donate:{message.from_user.id}", asset=currency
        )
        if not invoice:
            await message.answer("⚠️ Не получилось создать счёт, попробуй позже.")
            return
        db.create_pending_donation(message.from_user.id, f"{amount:.2f} USD ({currency})", "crypto", invoice["invoice_id"])
        await message.answer(
            f"💎 Счёт на ${amount:.2f} в {currency} создан. Спасибо за поддержку!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])],
                [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_donate:{invoice['invoice_id']}")],
            ]),
        )

    @dp.callback_query(F.data.startswith("check_donate:"))
    async def handle_check_donate(callback: CallbackQuery) -> None:
        invoice_id = callback.data.split(":", 1)[1]
        token = db.get_setting("crypto_pay_token", "")
        status = await cryptobot_check_invoice(token, invoice_id) if token else None
        if status == "paid":
            db.mark_donation_paid(invoice_id)
            await callback.message.edit_text("✅ Спасибо за поддержку, оплата подтверждена! 💛")
        else:
            await callback.answer("Пока не вижу оплату, попробуй через минуту.", show_alert=True)

    # -- Донаты: Stars --------------------------------------------------------
    @dp.callback_query(F.data == "support_stars")
    async def handle_support_stars(callback: CallbackQuery) -> None:
        await callback.message.answer("⭐ Сколько Stars хочешь задонатить?", reply_markup=build_stars_amount_keyboard())
        await callback.answer()

    @dp.callback_query(F.data.startswith("donate_stars:"))
    async def handle_donate_stars_preset(callback: CallbackQuery) -> None:
        amount = int(callback.data.split(":", 1)[1])
        await _send_stars_invoice(callback.message, amount)
        await callback.answer()

    @dp.callback_query(F.data == "donate_stars_custom")
    async def handle_donate_stars_custom(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserStates.waiting_donation_stars_custom)
        await callback.message.answer("Сколько Stars? Пришли число.")
        await callback.answer()

    @dp.message(UserStates.waiting_donation_stars_custom)
    async def handle_donate_stars_custom_amount(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            amount = int((message.text or "").strip())
            assert amount > 0
        except Exception:
            await message.answer("Нужно положительное целое число.")
            return
        await _send_stars_invoice(message, amount)

    async def _send_stars_invoice(message: Message, amount: int) -> None:
        await message.answer_invoice(
            title="Поддержка Dev AI",
            description=f"Донат на развитие проекта — {amount} ⭐",
            payload=f"donate_stars:{amount}",
            provider_token="",
            currency="XTR",
            prices=[{"label": "Донат", "amount": amount}],
        )

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
        await pre_checkout.answer(ok=True)

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        db.create_pending_donation(message.from_user.id, str(payment.total_amount), "stars", payment.telegram_payment_charge_id)
        db.mark_donation_paid(payment.telegram_payment_charge_id)
        await message.answer("✅ Спасибо за поддержку! 💛")

    # -- Скачать код файлом ------------------------------------------------
    @dp.callback_query(F.data.startswith("savefile:"))
    async def handle_save_file(callback: CallbackQuery) -> None:
        _, user_id_s, idx_s = callback.data.split(":", 2)
        user_id, idx = int(user_id_s), int(idx_s)
        blocks = LAST_CODE_CACHE.get(user_id, [])
        if idx >= len(blocks):
            await callback.answer("Этот код уже не в кэше — попроси написать заново.", show_alert=True)
            return
        lang, code = blocks[idx]
        ext = LANG_EXT.get(lang.lower(), "txt")
        file = BufferedInputFile(code.encode("utf-8"), filename=f"code.{ext}")
        await callback.message.answer_document(file)
        await callback.answer()

    # -- Основной чат с ИИ --------------------------------------------------
    # КРИТИЧНО: StateFilter(None) обязателен. Раньше этот хендлер матчил ЛЮБОЙ
    # текст независимо от состояния и, будучи зарегистрирован раньше админских
    # FSM-хендлеров (эмодзи, крипто-токен и т.д.), перехватывал сообщение первым
    # и просто тихо выходил — сами админские хендлеры даже не запускались.
    # Именно поэтому бот "молчал" после ввода эмодзи/ключа CryptoBot.
    @dp.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def handle_chat(message: Message, state: FSMContext) -> None:
        if db.get_setting("maintenance_mode", "0") == "1" and not _is_owner(message.from_user.id, settings):
            await message.answer(MAINTENANCE_MESSAGE)
            return
        if message.chat.type != "private":
            if db.get_setting("allow_groups", "0") != "1":
                return
            bot_username = _bot_username_cache.get("username")
            if bot_username is None:
                me = await message.bot.get_me()
                bot_username = (me.username or "").lower()
                _bot_username_cache["username"] = bot_username
            text_lower = (message.text or "").lower()
            is_mention = bool(bot_username) and f"@{bot_username}" in text_lower
            is_reply_to_bot = bool(
                message.reply_to_message and message.reply_to_message.from_user
                and message.reply_to_message.from_user.is_bot
            )
            if not (is_mention or is_reply_to_bot):
                return
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username)
        db.bump_messages(message.from_user.id)
        model = db.get_setting("default_model", settings.default_model)
        await ai_reply(message, model, message.text or "")

    # -- АДМИН-ПАНЕЛЬ ---------------------------------------------------------
    def build_admin_menu() -> InlineKeyboardMarkup:
        savage = db.get_setting("savage_mode", "1") == "1"
        groups_on = db.get_setting("allow_groups", "0") == "1"
        maintenance = db.get_setting("maintenance_mode", "0") == "1"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
            [InlineKeyboardButton(text="📤 Экспорт пользователей (CSV)", callback_data="admin_export_users")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(
                text=f"🧠 Модель по умолчанию: {db.get_setting('default_model', settings.default_model)}",
                callback_data="admin_model",
            )],
            [InlineKeyboardButton(
                text=f"🎨 Модель для картинок: {db.get_setting('image_model', '') or 'не задана'}",
                callback_data="admin_image_model",
            )],
            [InlineKeyboardButton(
                text=f"😈 Дерзкие ответы на мат: {'ВКЛ' if savage else 'выкл'}",
                callback_data="admin_savage_toggle",
            )],
            [InlineKeyboardButton(
                text=f"👥 Работа в группах: {'ВКЛ' if groups_on else 'выкл'}",
                callback_data="admin_groups_toggle",
            )],
            [InlineKeyboardButton(
                text=f"🛠 Технические работы: {'ВКЛ (бот не отвечает)' if maintenance else 'выкл'}",
                callback_data="admin_maintenance_toggle",
            )],
            [InlineKeyboardButton(
                text=f"👤 Поддержка: {db.get_setting('support_username', '') or 'не задан'}",
                callback_data="admin_support_username",
            )],
            [InlineKeyboardButton(
                text=f"💰 CryptoBot: {'настроен ✅' if db.get_setting('crypto_pay_token', '') else 'не задан'}",
                callback_data="admin_crypto_token",
            )],
            [InlineKeyboardButton(text="📝 Доп. инструкции персоне", callback_data="admin_extra_instructions")],
            [InlineKeyboardButton(text="💎 Премиум-эмодзи (в текстах)", callback_data="admin_emoji")],
            [InlineKeyboardButton(text="🔘 Иконки и текст кнопок", callback_data="admin_btnicons")],
        ])

    @dp.message(Command("admin"))
    async def handle_admin(message: Message) -> None:
        if not _is_owner(message.from_user.id, settings):
            return
        await message.answer("<b>🔐 Админ-панель Dev AI</b>", parse_mode="HTML", reply_markup=build_admin_menu())

    @dp.callback_query(F.data == "admin_menu")
    async def handle_admin_menu_cb(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text("<b>🔐 Админ-панель Dev AI</b>", parse_mode="HTML", reply_markup=build_admin_menu())
        await callback.answer()

    @dp.callback_query(F.data == "admin_stats")
    async def handle_admin_stats(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        total = db.users_count()
        today = db.users_joined_today()
        messages = db.total_messages()
        top = db.top_users(5)
        top_lines = "\n".join(f"  {i+1}. {u['first_name'] or '—'} — {u['messages_count']} сообщ." for i, u in enumerate(top)) or "  пока пусто"
        await callback.message.edit_text(
            f"📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: <b>{total}</b>\n"
            f"Новых сегодня: <b>{today}</b>\n"
            f"Всего сообщений боту: <b>{messages}</b>\n\n"
            f"<b>Топ по активности:</b>\n{top_lines}",
            parse_mode="HTML", reply_markup=build_admin_menu(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_export_users")
    async def handle_admin_export_users(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        rows = db.list_users(limit=100000, offset=0)
        lines = ["user_id,first_name,username,messages_count,joined_at"]
        for u in rows:
            fn = (u["first_name"] or "").replace(",", " ")
            lines.append(f"{u['user_id']},{fn},{u['username']},{u['messages_count']},{u['joined_at']}")
        csv_bytes = "\n".join(lines).encode("utf-8")
        await callback.message.answer_document(BufferedInputFile(csv_bytes, filename="users.csv"))
        await callback.answer()

    @dp.callback_query(F.data == "admin_model")
    async def handle_admin_model(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("default_model", settings.default_model)
        await callback.message.edit_text("🧠 Модель по умолчанию для всех ответов Dev AI:", reply_markup=build_model_keyboard(db, current))
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_setmodel:"))
    async def handle_admin_setmodel(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        model = callback.data.split(":", 1)[1]
        if model not in MODEL_KEYS:
            await callback.answer("Такой модели нет.", show_alert=True)
            return
        db.set_setting("default_model", model)
        await callback.message.edit_reply_markup(reply_markup=build_model_keyboard(db, model))
        await callback.answer(f"Модель по умолчанию: {model}")

    @dp.callback_query(F.data == "admin_savage_toggle")
    async def handle_admin_savage_toggle(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("savage_mode", "1")
        db.set_setting("savage_mode", "0" if current == "1" else "1")
        await callback.message.edit_reply_markup(reply_markup=build_admin_menu())
        await callback.answer("Переключено")

    @dp.callback_query(F.data == "admin_groups_toggle")
    async def handle_admin_groups_toggle(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("allow_groups", "0")
        db.set_setting("allow_groups", "0" if current == "1" else "1")
        await callback.message.edit_reply_markup(reply_markup=build_admin_menu())
        await callback.answer("Переключено")

    @dp.callback_query(F.data == "admin_maintenance_toggle")
    async def handle_admin_maintenance_toggle(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("maintenance_mode", "0")
        db.set_setting("maintenance_mode", "0" if current == "1" else "1")
        await callback.message.edit_reply_markup(reply_markup=build_admin_menu())
        await callback.answer("Переключено")

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def handle_admin_users(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        offset = int(callback.data.split(":", 1)[1])
        users = db.list_users(limit=20, offset=offset)
        if not users:
            lines = ["Пользователей пока нет."]
        else:
            lines = [
                f"• {u['first_name'] or '—'} (@{u['username']}) — id {u['user_id']}, сообщений: {u['messages_count']}"
                for u in users
            ]
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users:{max(0, offset - 20)}"))
        if len(users) == 20:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users:{offset + 20}"))
        rows = ([nav] if nav else []) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]]
        await callback.message.edit_text(
            "👥 <b>Пользователи</b>\n\n" + "\n".join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_broadcast")
    async def handle_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_broadcast_text)
        await callback.message.answer("Пришли текст рассылки (уйдёт всем пользователям бота).")
        await callback.answer()

    @dp.message(AdminStates.waiting_broadcast_text)
    async def handle_broadcast_text(message: Message, state: FSMContext) -> None:
        await state.clear()
        ids = db.all_user_ids()
        sent = 0
        for uid in ids:
            try:
                await message.bot.copy_message(uid, message.chat.id, message.message_id)
                sent += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        await message.answer(f"✅ Разослано {sent}/{len(ids)}.")

    @dp.callback_query(F.data == "admin_support_username")
    async def handle_admin_support_username(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_support_username)
        await callback.message.answer("Пришли юзернейм поддержки (например @support) или '-' чтобы убрать кнопку.")
        await callback.answer()

    @dp.message(AdminStates.waiting_support_username)
    async def handle_support_username_input(message: Message, state: FSMContext) -> None:
        val = (message.text or "").strip()
        db.set_setting("support_username", "" if val == "-" else val.lstrip("@"))
        await state.clear()
        await message.answer("✅ Обновлено.", reply_markup=build_admin_menu())

    @dp.callback_query(F.data == "admin_crypto_token")
    async def handle_admin_crypto_token(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_crypto_token)
        await callback.message.answer("Пришли API-токен CryptoBot (из @CryptoBot → Crypto Pay → Create App).")
        await callback.answer()

    @dp.message(AdminStates.waiting_crypto_token)
    async def handle_crypto_token_input(message: Message, state: FSMContext) -> None:
        token = (message.text or "").strip()
        db.set_setting("crypto_pay_token", token)
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("✅ Токен сохранён (сообщение с ним удалено).", reply_markup=build_admin_menu())

    @dp.callback_query(F.data == "admin_image_model")
    async def handle_admin_image_model(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_image_model)
        await callback.message.answer(
            "Пришли название модели для генерации картинок, которое поддерживает твой "
            "прокси (уточни у провайдера — гарантировать конкретное имя не могу), или '-' чтобы очистить."
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_image_model)
    async def handle_image_model_input(message: Message, state: FSMContext) -> None:
        val = (message.text or "").strip()
        db.set_setting("image_model", "" if val == "-" else val)
        await state.clear()
        await message.answer("✅ Обновлено.", reply_markup=build_admin_menu())

    @dp.callback_query(F.data == "admin_extra_instructions")
    async def handle_admin_extra_instructions(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_extra_instructions)
        await callback.message.answer("Пришли доп. инструкции для персоны Dev AI (добавятся к системному промпту), или '-' чтобы очистить.")
        await callback.answer()

    @dp.message(AdminStates.waiting_extra_instructions)
    async def handle_extra_instructions_input(message: Message, state: FSMContext) -> None:
        val = (message.text or "").strip()
        db.set_setting("extra_instructions", "" if val == "-" else val)
        await state.clear()
        await message.answer("✅ Обновлено.", reply_markup=build_admin_menu())

    # -- Премиум-эмодзи в текстах (универсально, любой ключ) --------------
    def build_emoji_admin_keyboard() -> InlineKeyboardMarkup:
        rows = []
        for row in db.all_emoji_keys():
            key = row["key"]
            if key.startswith("btn:"):
                continue  # кнопки настраиваются в своём разделе
            label = TEXT_EMOJI_LABELS.get(key, key)
            rows.append([InlineKeyboardButton(text=f"{row['fallback'] or '⭐'} {label}", callback_data=f"admin_emoji_pick:{key}")])
        rows.append([InlineKeyboardButton(text="➕ Добавить свой (любой текст)", callback_data="admin_emoji_new")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data == "admin_emoji")
    async def handle_admin_emoji(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text("💎 Выбери, для какого текста задать премиум-эмодзи:", reply_markup=build_emoji_admin_keyboard())
        await callback.answer()

    @dp.callback_query(F.data == "admin_emoji_new")
    async def handle_admin_emoji_new(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_new_emoji_key)
        await callback.message.answer("Придумай короткое имя слота латиницей без пробелов (например: greeting_icon).")
        await callback.answer()

    @dp.message(AdminStates.waiting_new_emoji_key)
    async def handle_new_emoji_key(message: Message, state: FSMContext) -> None:
        key = re.sub(r"[^a-zA-Z0-9_]", "", (message.text or "").strip())[:40]
        if not key:
            await message.answer("Нужно хотя бы одну латинскую букву/цифру.")
            return
        db.set_emoji(key, "", "⭐")
        await state.set_state(AdminStates.waiting_emoji_forward)
        await state.update_data(emoji_key=key)
        await message.answer(f"Слот «{key}» создан. Пришли эмодзи одним сообщением.")

    @dp.callback_query(F.data.startswith("admin_emoji_pick:"))
    async def handle_admin_emoji_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.set_state(AdminStates.waiting_emoji_forward)
        await state.update_data(emoji_key=key)
        await callback.message.answer("Пришли эмодзи (премиум — если есть Telegram Premium, иначе обычный) одним сообщением.")
        await callback.answer()

    @dp.message(AdminStates.waiting_emoji_forward)
    async def handle_emoji_forward(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        key = data.get("emoji_key")
        entities = message.entities or []
        custom = next((e for e in entities if e.type == "custom_emoji"), None)
        if custom and message.text:
            utf16 = message.text.encode("utf-16-le")
            raw = utf16[custom.offset * 2:(custom.offset + custom.length) * 2]
            fallback = raw.decode("utf-16-le", errors="ignore") or "⭐"
            db.set_emoji(key, custom.custom_emoji_id, fallback)
            await state.clear()
            await message.answer("✅ Премиум-эмодзи сохранён.", reply_markup=build_emoji_admin_keyboard())
            return
        icon = (message.text or "").strip().split()[0] if (message.text or "").strip() else ""
        if not icon:
            await message.answer("Пусто. Пришли эмодзи.")
            return
        db.set_emoji(key, "", icon)
        await state.clear()
        await message.answer(
            "⚠️ Сохранил как ОБЫЧНЫЙ эмодзи (не премиум) — Telegram не прислал ID премиум-эмодзи "
            "в этом сообщении. Обычно это значит, что у аккаунта, с которого ты отправляешь, нет "
            "Telegram Premium — без него выбрать премиум-эмодзи из панели физически нельзя, это "
            "ограничение Telegram, не бота. Если Premium есть — пришли эмодзи ещё раз отдельным "
            "новым сообщением (не пересланным).",
            reply_markup=build_emoji_admin_keyboard(),
        )

    # -- Иконки и текст кнопок ----------------------------------------------
    def build_btnicons_keyboard() -> InlineKeyboardMarkup:
        rows = []
        for key, (default_icon, label) in BUTTON_ICON_DEFAULTS.items():
            full = db.get_emoji_full(f"btn:{key}")
            mark = "💎" if (full and full[0]) else (full[1] if full and full[1] else default_icon)
            rows.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"admin_btnicon_pick:{key}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data == "admin_btnicons")
    async def handle_admin_btnicons(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text("🔘 Иконки и текст кнопок (Bot API 9.4). Выбери кнопку:", reply_markup=build_btnicons_keyboard())
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_btnicon_pick:"))
    async def handle_admin_btnicon_pick(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        label = BUTTON_ICON_DEFAULTS.get(key, ("", key))[1]
        await callback.message.answer(
            f"Кнопка: {label}\n\nЧто настроить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Эмодзи", callback_data=f"admin_btnicon_setemoji:{key}")],
                [InlineKeyboardButton(text="✏️ Текст кнопки", callback_data=f"admin_btnicon_settext:{key}")],
                [InlineKeyboardButton(text="🎨 Цвет", callback_data=f"admin_btnicon_setcolor:{key}")],
                [InlineKeyboardButton(text="♻️ Сбросить", callback_data=f"admin_btnicon_reset:{key}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_btnicons")],
            ]),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_btnicon_setemoji:"))
    async def handle_btnicon_setemoji(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.set_state(AdminStates.waiting_button_icon)
        await state.update_data(btn_icon_key=key)
        await callback.message.answer("Пришли эмодзи для этой кнопки (премиум — если есть Premium, иначе обычный).")
        await callback.answer()

    @dp.message(AdminStates.waiting_button_icon)
    async def handle_button_icon_input(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        key = data.get("btn_icon_key")
        if data.get("btn_text_mode"):
            text = (message.text or "").strip()
            if not text:
                await message.answer("Пусто. Пришли текст кнопки.")
                return
            db.set_setting(f"btntext:{key}", text)
            await state.clear()
            await message.answer("✅ Текст кнопки обновлён.", reply_markup=build_btnicons_keyboard())
            return
        entities = message.entities or []
        custom = next((e for e in entities if e.type == "custom_emoji"), None)
        if custom and message.text:
            utf16 = message.text.encode("utf-16-le")
            raw = utf16[custom.offset * 2:(custom.offset + custom.length) * 2]
            fallback = raw.decode("utf-16-le", errors="ignore") or "⭐"
            db.set_emoji(f"btn:{key}", custom.custom_emoji_id, fallback)
            await state.clear()
            await message.answer("✅ Премиум-иконка сохранена.", reply_markup=build_btnicons_keyboard())
            return
        icon = (message.text or "").strip().split()[0] if (message.text or "").strip() else ""
        if not icon:
            await message.answer("Пусто. Пришли эмодзи.")
            return
        db.set_emoji(f"btn:{key}", "", icon)
        await state.clear()
        await message.answer(
            "⚠️ Сохранил как ОБЫЧНЫЙ эмодзи — Telegram не прислал ID премиум-эмодзи. Скорее всего "
            "у аккаунта нет Telegram Premium (без него выбрать премиум-эмодзи физически нельзя — "
            "ограничение Telegram). Если Premium есть — пришли ещё раз новым сообщением.",
            reply_markup=build_btnicons_keyboard(),
        )

    @dp.callback_query(F.data.startswith("admin_btnicon_settext:"))
    async def handle_btnicon_settext(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await state.set_state(AdminStates.waiting_button_icon)
        await state.update_data(btn_icon_key=key, btn_text_mode=True)
        await callback.message.answer("Пришли новый текст кнопки (без эмодзи).")
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_btnicon_setcolor:"))
    async def handle_btnicon_setcolor(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        await callback.message.answer(
            "Выбери цвет кнопки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔵 Синий", callback_data=f"admin_btnicon_color:{key}:primary")],
                [InlineKeyboardButton(text="🟢 Зелёный", callback_data=f"admin_btnicon_color:{key}:success")],
                [InlineKeyboardButton(text="🔴 Красный", callback_data=f"admin_btnicon_color:{key}:danger")],
                [InlineKeyboardButton(text="⚪️ По умолчанию", callback_data=f"admin_btnicon_color:{key}:")],
            ]),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_btnicon_color:"))
    async def handle_btnicon_color(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        _, key, color = callback.data.split(":", 2)
        db.set_setting(f"btnstyle:{key}", color)
        await callback.message.answer("✅ Цвет обновлён.", reply_markup=build_btnicons_keyboard())
        await callback.answer()

    @dp.callback_query(F.data.startswith("admin_btnicon_reset:"))
    async def handle_btnicon_reset(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        db.set_emoji(f"btn:{key}", "", "")
        db.set_setting(f"btnstyle:{key}", "")
        db.set_setting(f"btntext:{key}", "")
        await callback.message.answer("♻️ Сброшено.", reply_markup=build_btnicons_keyboard())
        await callback.answer()


# ======================================================================
# ФОНОВЫЕ ЗАДАЧИ
# ======================================================================

async def reminders_loop(bot: Bot, db: Database) -> None:
    while True:
        try:
            for row in db.due_reminders():
                try:
                    await bot.send_message(row["user_id"], f"⏰ Напоминание: {row['text']}")
                except Exception:
                    logger.exception("Failed to send reminder to %s", row["user_id"])
                db.mark_reminder_fired(row["id"])
        except Exception:
            logger.exception("reminders_loop iteration failed")
        await asyncio.sleep(15)


async def crypto_donations_loop(bot: Bot, db: Database) -> None:
    while True:
        try:
            token = db.get_setting("crypto_pay_token", "")
            if token:
                for row in db.pending_crypto_donations():
                    status = await cryptobot_check_invoice(token, row["invoice_id"])
                    if status == "paid":
                        db.mark_donation_paid(row["invoice_id"])
                        try:
                            await bot.send_message(row["user_id"], "✅ Спасибо за поддержку, оплата подтверждена! 💛")
                        except Exception:
                            pass
        except Exception:
            logger.exception("crypto_donations_loop iteration failed")
        await asyncio.sleep(60)


# ======================================================================
# ENTRYPOINT
# ======================================================================

async def main() -> None:
    settings = Settings.from_env()
    db = Database(DB_PATH, settings.owner_id)
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    register_handlers(dp, db, settings)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="support", description="Поддержка / помочь проекту"),
    ])
    asyncio.create_task(reminders_loop(bot, db))
    asyncio.create_task(crypto_donations_loop(bot, db))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
