#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Luna — AI стилист Telegram-бот (Gemini Pro)
- Анализ фото через Gemini Pro (официальный Python SDK google-genai)
- Составление образов через Gemini
- Оплата НЕ подключена (кнопки существуют, можно самому выдать подписку)
- DB: sqlite (luna_gemini.db)

Перед запуском: установить зависимости:
  pip install python-telegram-bot==20.6 pillow google-genai

Переменные окружения:
  TELEGRAM_TOKEN - токен бота
  GEMINI_API_KEY - ключ Gemini (Google Generative AI API)
  ADMIN_CHAT_ID  - ваш telegram id (по умолчанию подставлен 7236856802)
"""

import os
import io
import json
import sqlite3
import datetime
from functools import wraps

from PIL import Image

# Telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# Gemini (Google GenAI SDK)
from google import genai
from google.genai import types

# --- Configuration ---
TELEGRAM_TOKEN = "8461730209:AAGc4mL_XB9cQK1w1I8Gnikz0SfMVKrLo0Q"
GEMINI_API_KEY = "AIzaSyCiX6817ZKWt2oIrdfFPdipdOWFejNOpK4"
ADMIN_CHAT_ID = 7236856802  # можешь оставить это число — это твой Telegram ID

DB_PATH = "luna_gemini.db"

# модель Gemini Pro для vision-capable запросов — можно заменить на 'gemini-pro-vision' или актуальную модель
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # рекомендую проверить доступные модели в вашей учётке

# Подписки (цены просто отображаем, оплаты нет)
PRICES = {
    "week": {"label": "Неделя", "price": "4.99 EUR"},
    "month": {"label": "Месяц", "price": "12.99 EUR"},
    "year": {"label": "Год", "price": "79.99 EUR"},
}

# Инициализация GenAI client
# Библиотека читает GEMINI_API_KEY из переменных окружения, но явно передаём:
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
client = genai.Client()  # использует GEMINI_API_KEY

# --- DB helpers ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            gender TEXT,
            color_type TEXT,
            color_palette TEXT,
            subscription_expire TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id, gender, color_type, color_palette, subscription_expire FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "chat_id": row[0],
        "gender": row[1],
        "color_type": row[2],
        "color_palette": json.loads(row[3]) if row[3] else None,
        "subscription_expire": datetime.datetime.fromisoformat(row[4]) if row[4] else None
    }

def upsert_user(chat_id, gender=None, color_type=None, color_palette=None, subscription_expire=None):
    u = get_user(chat_id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if u:
        # update selectively
        if gender is not None:
            c.execute("UPDATE users SET gender=? WHERE chat_id=?", (gender, chat_id))
        if color_type is not None:
            c.execute("UPDATE users SET color_type=? WHERE chat_id=?", (color_type, chat_id))
        if color_palette is not None:
            c.execute("UPDATE users SET color_palette=? WHERE chat_id=?", (json.dumps(color_palette), chat_id))
        if subscription_expire is not None:
            c.execute("UPDATE users SET subscription_expire=? WHERE chat_id=?", (subscription_expire.isoformat() if subscription_expire else None, chat_id))
    else:
        c.execute("INSERT INTO users(chat_id, gender, color_type, color_palette, subscription_expire) VALUES (?, ?, ?, ?, ?)",
                  (chat_id, gender, color_type, json.dumps(color_palette) if color_palette else None,
                   subscription_expire.isoformat() if subscription_expire else None))
    conn.commit()
    conn.close()

# --- Keyboards & texts ---
def start_screen_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Старт", callback_data="start_flow")]])

def gender_keyboard():
    kb = [
        [InlineKeyboardButton("Женщина", callback_data="gender_female"),
         InlineKeyboardButton("Мужчина", callback_data="gender_male")],
        [InlineKeyboardButton("Другое / Не указывать", callback_data="gender_other")]
    ]
    return InlineKeyboardMarkup(kb)

def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton("Анализ фото", callback_data="analyze_photo"),
         InlineKeyboardButton("Подобрать образ", callback_data="create_outfit")],
        [InlineKeyboardButton("Подписка", callback_data="subscribe"),
         InlineKeyboardButton("Техподдержка", callback_data="support")]
    ]
    return InlineKeyboardMarkup(kb)

def subscription_keyboard():
    kb = [
        [InlineKeyboardButton(f"{PRICES['week']['label']} — {PRICES['week']['price']}", callback_data="sub_week")],
        [InlineKeyboardButton(f"{PRICES['month']['label']} — {PRICES['month']['price']}", callback_data="sub_month")],
        [InlineKeyboardButton(f"{PRICES['year']['label']} — {PRICES['year']['price']}", callback_data="sub_year")],
        [InlineKeyboardButton("Выдать подписку себе (DEMO)", callback_data="grant_self")]
    ]
    return InlineKeyboardMarkup(kb)

def style_keyboard():
    kb = [
        [InlineKeyboardButton("Кэжуал", callback_data="style_casual"),
         InlineKeyboardButton("Офис", callback_data="style_office")],
        [InlineKeyboardButton("Вечерний", callback_data="style_evening"),
         InlineKeyboardButton("Спортивный", callback_data="style_sport")],
        [InlineKeyboardButton("Романтичный", callback_data="style_romantic"),
         InlineKeyboardButton("Стрит/Уличный", callback_data="style_street")]
    ]
    return InlineKeyboardMarkup(kb)

WELCOME_TEXT = (
    "Привет! Я AI-стилист *Luna* 🌙\n\n"
    "Я могу определить ваш цветотип по фото (через Gemini Pro) и помочь с подбором образов.\n\n"
    "Выберите функцию ниже."
)

PHOTO_GUIDELINES = (
    "Как сделать хорошее фото для анализа цветотипа:\n"
    "• дневное/нейтральное освещение (без цветных ламп);\n"
    "• лицо и верхняя часть туловища без фильтров и сильного макияжа;\n"
    "• однотонный фон предпочтителен.\n\n"
    "После присылки фото я отправлю результат анализа (цветотип + палитра) — всё через Gemini Pro."
)

# --- Decorator: subscription required for 'Подобрать образ' ---
def requires_subscription(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = get_user(chat_id)
        now = datetime.datetime.utcnow()
        if user and user.get("subscription_expire") and user["subscription_expire"] > now:
            return await func(update, context)
        await update.effective_message.reply_text("Эта функция доступна только по подписке. Оформите подписку (кнопка 'Подписка') или выдайте её себе (DEMO).")
    return wrapper

# --- Gemini helpers ---
def extract_color_type_from_text(text):
    """
    Попытка извлечь один из российских цветотипов: Весна, Лето, Осень, Зима
    из текста ответа Gemini. Возвращает знакословно найденный цветотип или None.
    """
    for ct in ["Весна", "Весна.", "весна", "весна.", "Лето", "лето", "Осень", "осень", "Зима", "зима"]:
        if ct.lower() in text.lower():
            # normalize
            if "весна" in ct.lower(): return "Весна"
            if "лето" in ct.lower(): return "Лето"
            if "осень" in ct.lower(): return "Осень"
            if "зима" in ct.lower(): return "Зима"
    return None

def palette_from_gemini_text(text):
    """
    Простая эвристика: ищем хештеги или перечисленные цвета (HEX или слова).
    Если не находим — вернём None (Gemini обычно возвращает список цветов словами).
    """
    # Для простоты — возвращаем первые 5 слововых упоминаний цветов после ключевых слов "палит" или "цвет"
    lowered = text.lower()
    idx = lowered.find("палит")
    if idx == -1:
        idx = lowered.find("цвет")
    if idx != -1:
        tail = text[idx: idx + 200]
        # split by comma and take up to 5 tokens
        parts = [p.strip().strip(".") for p in tail.split(",")]
        # filter short tokens
        parts = [p for p in parts if len(p) > 2][:5]
        if parts:
            return parts
    # fallback: None
    return None

def call_gemini_with_image(image_bytes: bytes, prompt_text: str) -> str:
    """
    Отправляет image_bytes + prompt_text в Gemini model через google-genai SDK.
    Возвращает текст ответа модели.
    Док: examples на https://ai.google.dev/gemini-api/docs/image-understanding
    """
    # создаём Part из байтов изображения
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    # содержимое: сначала картинка, затем текст-подсказка
    contents = [image_part, prompt_text]
    # вызвать модель
    response = client.models.generate_content(model=GEMINI_MODEL, contents=contents)
    # response.text() или response.text в зависимости от версии; обычно .text
    # безопасно: response.text
    return response.text

def call_gemini_textonly(prompt_text: str) -> str:
    """
    Отправить текстовый prompt в Gemini и получить ответ.
    """
    response = client.models.generate_content(model=GEMINI_MODEL, contents=[prompt_text])
    return response.text

# --- Handlers ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Что умеет бот?\n\nLuna — AI-стилист, который помогает с цветами и образами."
    )
    await update.message.reply_text(
        "Выберите действие в меню ниже 👇",
        reply_markup=main_menu_keyboard()
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "start_flow":
        await query.edit_message_text("Выберите, пожалуйста, ваш пол (это поможет адаптировать рекомендации):", reply_markup=gender_keyboard())
        return

    if data.startswith("gender_"):
        gender = data.split("_", 1)[1]
        upsert_user(chat_id, gender=gender)
        await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return

    if data == "analyze_photo":
        await query.edit_message_text(PHOTO_GUIDELINES)
        await context.bot.send_message(chat_id, "Пришлите, пожалуйста, ваше фото (фотографию в цвете, без фильтров).")
        context.user_data["awaiting_photo"] = True
        return

    if data == "create_outfit":
        # проверка подписки
        user = get_user(chat_id)
        now = datetime.datetime.utcnow()
        if user and user.get("subscription_expire") and user["subscription_expire"] > now:
            await query.edit_message_text("Выберите стиль для образа:", reply_markup=style_keyboard())
            context.user_data["awaiting_style"] = True
        else:
            await query.edit_message_text("Функция доступна по подписке. Оформите подписку (кнопка 'Подписка') или выдайте её себе (DEMO).", reply_markup=subscription_keyboard())
        return

    if data == "subscribe":
        await query.edit_message_text("Доступные подписки (оплата пока не подключена):", reply_markup=subscription_keyboard())
        return

    if data.startswith("sub_"):
        # платежи не подключаем
        await query.edit_message_text("Оплата временно недоступна. Вы можете выдать подписку себе вручную (кнопка 'Выдать подписку себе (DEMO)')", reply_markup=subscription_keyboard())
        return

    if data == "grant_self":
        # выдаём подписку на 30 дней (DEMO)
        expire = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        upsert_user(chat_id, subscription_expire=expire)
        await query.edit_message_text(f"Подписка (DEMO) выдана. Доступ активен до {expire.strftime('%Y-%m-%d %H:%M:%S UTC')}.", reply_markup=main_menu_keyboard())
        return

    if data == "support":
        await query.edit_message_text("Опишите проблему/вопрос. Ваше сообщение будет отправлено разработчику.")
        context.user_data["awaiting_support"] = True
        return

    if data.startswith("style_"):
        style = data.split("_", 1)[1]
        user = get_user(chat_id)
        if not user or not user.get("color_type"):
            await query.edit_message_text("Сначала проведите анализ фото (раздел 'Анализ фото').")
            return
        await query.edit_message_text("Генерирую аутфиты через Gemini...")
        # создаём промпт для Gemini с учётом цветотипа и правилами "аутфиты, без лиц"
        prompt = (
            f"Вы — профессиональный стилист. Пользователь: пол = {user.get('gender','не указано')}, "
            f"цветотип = {user.get('color_type')}. Задача: составить 3 варианта аутфитов в стиле '{style}' "
            "только одежда, аксессуары и цвета — без упоминания/генерации лиц и без личной информации. "
            "Каждый вариант — короткое описание: верх, низ, обувь, аксессуары, рекомендованные цвета (коротко). "
            "Не предлагайте макияж и не обсуждайте внешность человека. Дайте пометки 'уровень формальности' и 'когда носить'."
        )
        try:
            resp_text = call_gemini_textonly(prompt)
        except Exception as e:
            resp_text = f"Ошибка при обращении к Gemini: {e}"
        await context.bot.send_message(chat_id, f"Gemini:\n\n{resp_text}")
        return

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.user_data.get("awaiting_photo"):
        await update.message.reply_text("Если хотите проанализировать фото — нажмите 'Анализ фото' в меню.", reply_markup=main_menu_keyboard())
        return

    # берём максимально большое фото
    photo = update.message.photo[-1]
    file = await photo.get_file()
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)
    image_bytes = bio.read()

    # подготовим prompt — просим Gemini проанализировать фото и вернуть:
    # 1) Цветотип (одно слово: Весна/Лето/Осень/Зима)
    # 2) 4-6 рекомендованных оттенков (словами или HEX)
    # 3) Короткий совет по одежде
    prompt = (
        "Анализ изображения для определения цветотипа (весна/лето/осень/зима) и подбор палитры.\n"
        "Инструкции модели:\n"
        "1) Опишите одним словом предполагаемый цветотип (одно из: Весна, Лето, Осень, Зима).\n"
        "2) Приведите 4-6 рекомендованных цветов/оттенков (коротко, через запятую), можно словами (например: 'тёплый персиковый', 'тёмно-синий').\n"
        "3) Дайте 2–3 коротких совета по одежде (какие оттенки носить вверху, какие внизу, аксессуары).\n"
        "4) Ничего не говорите о лице и не предлагайте ретушь/макияж. Формат ответа: сначала строка 'Цветотип: <слово>', затем 'Палитра: ...', затем 'Советы: ...'."
    )

    try:
        gemini_resp = call_gemini_with_image(image_bytes=image_bytes, prompt_text=prompt)
    except Exception as e:
        await update.message.reply_text(f"Ошибка при работе с Gemini: {e}")
        context.user_data.pop("awaiting_photo", None)
        return

    # попытаемся извлечь цветотип и палитру
    color_type = extract_color_type_from_text(gemini_resp) or "Не определено"
    palette = palette_from_gemini_text(gemini_resp) or []

    # сохраняем
    upsert_user(chat_id, color_type=color_type, color_palette=palette)

    # отправляем результат пользователю
    reply = f"Результат анализа (через Gemini):\n\n{gemini_resp}\n\n" \
            f"Цветотип (определён): *{color_type}*\n" \
            f"Рекомендованная палитра: {', '.join(palette) if palette else '(не указано явной палитры)'}\n\n" \
            "Если хотите получить подробный набор аутфитов (разные варианты), нажмите 'Подобрать образ' (функция по подписке)."
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    context.user_data.pop("awaiting_photo", None)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.user_data.get("awaiting_support"):
        text = update.message.text
        # переслать админу
        await context.bot.send_message(ADMIN_CHAT_ID, f"Сообщение от пользователя {chat_id} (Техподдержка):\n\n{text}")
        await update.message.reply_text("Спасибо! Ваше сообщение отправлено разработчику.")
        context.user_data.pop("awaiting_support", None)
        return
    await update.message.reply_text("Не распознано. Используйте главное меню.", reply_markup=main_menu_keyboard())

# --- main ---
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()
