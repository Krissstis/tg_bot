import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Токен берется из переменной окружения (так безопаснее для Render)
BOT_TOKEN = "8687705334:AAFLQxeDPtf8FUa35mts2icQNBJUdPJ5kEY"

# Создаем объекты бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Обработчики команд (такие же, как были) ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот, работающий на Astra Linux и Render!\n"
        "Напиши мне что-нибудь, и я отвечу."
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📚 Доступные команды:\n"
        "/start - Начать работу\n"
        "/help - Показать это сообщение\n"
        "/info - Информация о боте"
    )

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    await message.answer(
        f"🤖 Информация о боте:\n"
        f"Платформа: Astra Linux + Render\n"
        f"Библиотека: aiogram\n"
        f"Python: 3.11.8"
    )

@dp.message()
async def echo_message(message: types.Message):
    user_name = message.from_user.first_name
    await message.answer(
        f"Привет, {user_name}! Ты написал: {message.text}"
    )

# --- Веб-сервер для Render (чтобы не засыпал и проходил проверки) ---
async def health_check(request):
    """Render будет проверять этот адрес"""
    return web.Response(text="I'm alive!")

async def webhook(request):
    """Сюда Telegram может присылать обновления (но мы пока не используем)"""
    return web.Response(text="OK")

async def start_bot():
    """Запускаем бота и веб-сервер одновременно"""
    
    # Создаем веб-приложение
    app = web.Application()
    app.router.add_get('/health', health_check)  # Для проверок Render
    app.router.add_post('/webhook', webhook)     # На будущее
    app.router.add_get('/', health_check)         # Для пинга
    
    # Запускаем веб-сервер в фоне
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))  # Render сам подставляет PORT
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"🌐 Веб-сервер запущен на порту {port}")
    print("🚀 Бот запущен и готов к работе!")
    
    # Запускаем бота (это бесконечный процесс)
    await dp.start_polling(bot)

async def main():
    await start_bot()

if __name__ == "__main__":
    asyncio.run(main())
