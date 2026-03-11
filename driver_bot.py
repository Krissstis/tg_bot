import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Токен бота
BOT_TOKEN = "8687705334:AAFO3Nzp7UOHMinjA5o40kCvhsoQOGXmfaI"

# ID администратора (ваш Telegram ID)
ADMIN_ID = 993913729  

# ID водителей
DRIVERS = {
    "Тимофеев":8054291914,  # Замените на реальные ID
    "Сурков": 222222222,
    "Криклин": 333333333
}

# Создаем объекты бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    """Создаем таблицы в базе данных"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    # Таблица водителей
    c.execute('''CREATE TABLE IF NOT EXISTS drivers
                 (id INTEGER PRIMARY KEY,
                  driver_id INTEGER UNIQUE,
                  name TEXT,
                  phone TEXT,
                  car_info TEXT)''')
    
    # Таблица клиентов (их ФИО сохраняем)
    c.execute('''CREATE TABLE IF NOT EXISTS clients
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER UNIQUE,  -- Telegram ID клиента
                  full_name TEXT,           -- Фамилия И.О.
                  phone TEXT,
                  first_seen TEXT)''')
    
    # Таблица записей
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  driver_id INTEGER,
                  client_id INTEGER,
                  client_full_name TEXT,    -- Фамилия И.О. на момент записи
                  client_phone TEXT,
                  booking_time TEXT,
                  created_at TEXT,
                  status TEXT DEFAULT 'new',  -- new, confirmed, completed, cancelled, admin_cancelled
                  created_by TEXT DEFAULT 'client',  -- client, admin
                  notes TEXT,                -- заметки админа
                  FOREIGN KEY (driver_id) REFERENCES drivers (driver_id),
                  FOREIGN KEY (client_id) REFERENCES clients (user_id))''')
    
    conn.commit()
    conn.close()

# Инициализируем базу
init_db()

# Добавляем водителей при старте (если их нет)
def ensure_drivers():
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    # Проверяем и добавляем каждого водителя
    for name, driver_id in DRIVERS.items():
        c.execute("INSERT OR IGNORE INTO drivers (driver_id, name) VALUES (?, ?)",
                  (driver_id, name))
    
    conn.commit()
    conn.close()

ensure_drivers()

# ==================== СОСТОЯНИЯ ====================
class ClientBooking(StatesGroup):
    entering_full_name = State()
    entering_phone = State()
    choosing_driver = State()
    choosing_time = State()

class AdminStates(StatesGroup):
    choosing_action = State()
    choosing_driver_for_booking = State()
    entering_client_full_name = State()
    entering_client_phone = State()
    entering_booking_time = State()
    entering_booking_notes = State()
    choosing_booking_to_cancel = State()

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard():
    """Клавиатура для обычных клиентов"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚗 Записаться к водителю")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="ℹ️ О нас")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_driver_keyboard():
    """Клавиатура для водителей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="✅ Подтвердить запись"), KeyboardButton(text="❌ Отменить")],
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_admin_keyboard():
    """Клавиатура для администратора"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Все записи")],
            [KeyboardButton(text="➕ Записать клиента"), KeyboardButton(text="❌ Отменить запись")],
            [KeyboardButton(text="🚗 Водители"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ====================

def get_or_create_client(user_id, full_name=None):
    """Получить или создать клиента"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    c.execute("SELECT * FROM clients WHERE user_id = ?", (user_id,))
    client = c.fetchone()
    
    if not client and full_name:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO clients (user_id, full_name, first_seen) VALUES (?, ?, ?)",
                  (user_id, full_name, now))
        conn.commit()
    
    conn.close()
    return client

def get_drivers():
    """Список всех водителей"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT driver_id, name FROM drivers")
    drivers = c.fetchall()
    conn.close()
    return drivers

def create_booking(driver_id, client_id, client_name, client_phone, booking_time, created_by='client', notes=''):
    """Создать запись"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("""INSERT INTO bookings 
                 (driver_id, client_id, client_full_name, client_phone, booking_time, created_at, created_by, notes) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (driver_id, client_id, client_name, client_phone, booking_time, now, created_by, notes))
    
    booking_id = c.lastrowid
    conn.commit()
    conn.close()
    return booking_id

def get_driver_bookings(driver_id, status=None):
    """Записи конкретного водителя"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    if status:
        c.execute("""SELECT id, client_full_name, client_phone, booking_time, created_at, status 
                     FROM bookings WHERE driver_id = ? AND status = ? ORDER BY booking_time""",
                  (driver_id, status))
    else:
        c.execute("""SELECT id, client_full_name, client_phone, booking_time, created_at, status 
                     FROM bookings WHERE driver_id = ? ORDER BY booking_time""",
                  (driver_id,))
    
    bookings = c.fetchall()
    conn.close()
    return bookings

def get_all_bookings(status=None):
    """Все записи (для админа)"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    query = """SELECT b.id, d.name, b.client_full_name, b.client_phone, 
                      b.booking_time, b.created_at, b.status, b.created_by, b.notes
               FROM bookings b
               LEFT JOIN drivers d ON b.driver_id = d.driver_id"""
    
    if status:
        query += " WHERE b.status = ?"
        c.execute(query, (status,))
    else:
        c.execute(query)
    
    bookings = c.fetchall()
    conn.close()
    return bookings

def update_booking_status(booking_id, status, notes=''):
    """Обновить статус записи"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    if notes:
        c.execute("UPDATE bookings SET status = ?, notes = ? WHERE id = ?", 
                  (status, notes, booking_id))
    else:
        c.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    
    conn.commit()
    conn.close()

def get_driver_name(driver_id):
    """Имя водителя по ID"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT name FROM drivers WHERE driver_id = ?", (driver_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else "Неизвестный водитель"

# ==================== ОБЩИЕ ОБРАБОТЧИКИ ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Старт - определяем роль пользователя"""
    user_id = message.from_user.id
    
    # Определяем роль
    if user_id == ADMIN_ID:
        text = "👑 Добро пожаловать, Администратор!"
        keyboard = get_admin_keyboard()
    elif user_id in DRIVERS.values():
        # Находим имя водителя
        driver_name = next(name for name, id in DRIVERS.items() if id == user_id)
        text = f"🚖 Добро пожаловать, {driver_name}!"
        keyboard = get_driver_keyboard()
    else:
        text = ("👋 Добро пожаловать в сервис 'Запись к водителю'!\n\n"
                "Мы поможем вам быстро записаться к одному из наших водителей.\n"
                "Используйте кнопки ниже для навигации.")
        keyboard = get_main_keyboard()
        
        # Сохраняем клиента, если его еще нет
        get_or_create_client(user_id)
    
    await message.answer(text, reply_markup=keyboard)

# ==================== ОБРАБОТЧИКИ ДЛЯ КЛИЕНТОВ ====================

@dp.message(lambda message: message.text == "🚗 Записаться к водителю")
async def client_start_booking(message: types.Message, state: FSMContext):
    """Клиент начинает запись"""
    # Сначала спрашиваем ФИО
    await message.answer("Введите вашу фамилию и инициалы (например: Петров А.Б.):")
    await state.set_state(ClientBooking.entering_full_name)

@dp.message(ClientBooking.entering_full_name)
async def client_process_full_name(message: types.Message, state: FSMContext):
    """Сохраняем ФИО клиента"""
    full_name = message.text
    await state.update_data(client_full_name=full_name)
    
    # Сохраняем в базу клиентов
    get_or_create_client(message.from_user.id, full_name)
    
    await message.answer("Введите ваш номер телефона:")
    await state.set_state(ClientBooking.entering_phone)

@dp.message(ClientBooking.entering_phone)
async def client_process_phone(message: types.Message, state: FSMContext):
    """Сохраняем телефон"""
    await state.update_data(client_phone=message.text)
    
    # Показываем список водителей
    drivers = get_drivers()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for driver_id, name in drivers:
        button = InlineKeyboardButton(
            text=name,
            callback_data=f"client_driver_{driver_id}"
        )
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите водителя:", reply_markup=keyboard)
    await state.set_state(ClientBooking.choosing_driver)

@dp.callback_query(ClientBooking.choosing_driver)
async def client_choose_driver(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбирает водителя"""
    driver_id = int(callback.data.replace("client_driver_", ""))
    await state.update_data(driver_id=driver_id)
    
    # Показываем варианты времени
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    now = datetime.now()
    
    for i in range(1, 9):  # Предлагаем 8 вариантов
        time = now.replace(minute=0, second=0) + timedelta(hours=i)
        time_str = time.strftime("%d.%m %H:00")
        button = InlineKeyboardButton(
            text=time_str,
            callback_data=f"client_time_{time_str}"
        )
        keyboard.inline_keyboard.append([button])
    
    await callback.message.answer("Выберите удобное время:", reply_markup=keyboard)
    await callback.answer()
    await state.set_state(ClientBooking.choosing_time)

@dp.callback_query(ClientBooking.choosing_time)
async def client_choose_time(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбирает время и завершает запись"""
    booking_time = callback.data.replace("client_time_", "")
    data = await state.get_data()
    
    # Создаем запись
    booking_id = create_booking(
        driver_id=data['driver_id'],
        client_id=callback.from_user.id,
        client_name=data['client_full_name'],
        client_phone=data['client_phone'],
        booking_time=booking_time,
        created_by='client'
    )
    
    # Подтверждение клиенту
    await callback.message.answer(
        f"✅ Вы успешно записаны!\n\n"
        f"Водитель: {get_driver_name(data['driver_id'])}\n"
        f"Время: {booking_time}\n"
        f"Ваше ФИО: {data['client_full_name']}\n\n"
        f"Водитель получил уведомление.",
        reply_markup=get_main_keyboard()
    )
    
    # Уведомление водителю
    await notify_driver(
        driver_id=data['driver_id'],
        client_name=data['client_full_name'],
        client_phone=data['client_phone'],
        booking_time=booking_time,
        booking_id=booking_id
    )
    
    # Уведомление админу
    await notify_admin(
        booking_id=booking_id,
        driver_name=get_driver_name(data['driver_id']),
        client_name=data['client_full_name'],
        client_phone=data['client_phone'],
        booking_time=booking_time
    )
    
    await callback.answer()
    await state.clear()

@dp.message(lambda message: message.text == "📋 Мои записи")
async def client_my_bookings(message: types.Message):
    """Клиент смотрит свои записи"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    
    c.execute("""SELECT b.id, d.name, b.booking_time, b.status 
                 FROM bookings b
                 LEFT JOIN drivers d ON b.driver_id = d.driver_id
                 WHERE b.client_id = ? AND b.status != 'cancelled' AND b.status != 'admin_cancelled'
                 ORDER BY b.booking_time""", (message.from_user.id,))
    
    bookings = c.fetchall()
    conn.close()
    
    if not bookings:
        await message.answer("У вас пока нет активных записей.")
        return
    
    response = "📋 <b>Ваши записи:</b>\n\n"
    for booking in bookings:
        status_text = {
            'new': '🆕 Ожидает подтверждения',
            'confirmed': '✅ Подтверждено',
            'completed': '✔️ Завершено'
        }.get(booking[3], '📌 В обработке')
        
        response += f"#{booking[0]} {booking[1]} на {booking[2]} - {status_text}\n"
    
    await message.answer(response, parse_mode="HTML")

# ==================== ОБРАБОТЧИКИ ДЛЯ ВОДИТЕЛЕЙ ====================

@dp.message(lambda message: message.text == "📋 Мои записи" and message.from_user.id in DRIVERS.values())
async def driver_my_bookings(message: types.Message):
    """Водитель смотрит свои записи"""
    bookings = get_driver_bookings(message.from_user.id)
    
    if not bookings:
        await message.answer("У вас пока нет записей.")
        return
    
    response = "📋 <b>Ваши записи:</b>\n\n"
    for booking in bookings:
        status_emoji = {
            'new': '🆕',
            'confirmed': '✅',
            'completed': '✔️',
            'cancelled': '❌'
        }.get(booking[5], '📌')
        
        response += (
            f"{status_emoji} <b>Запись #{booking[0]}</b>\n"
            f"👤 Клиент: {booking[1]}\n"
            f"📞 Телефон: {booking[2]}\n"
            f"⏰ Время: {booking[3]}\n"
            f"📅 Создана: {booking[4]}\n"
            f"🔹 Статус: {booking[5]}\n\n"
        )
    
    await message.answer(response, parse_mode="HTML")

@dp.callback_query(lambda c: c.data.startswith('driver_confirm_'))
async def driver_confirm_booking(callback: types.CallbackQuery):
    """Водитель подтверждает запись"""
    booking_id = int(callback.data.replace('driver_confirm_', ''))
    update_booking_status(booking_id, 'confirmed')
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✅ Запись #{booking_id} подтверждена!")
    
    # Уведомляем админа
    await bot.send_message(
        ADMIN_ID,
        f"✅ Водитель подтвердил запись #{booking_id}"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('driver_cancel_'))
async def driver_cancel_booking(callback: types.CallbackQuery):
    """Водитель отменяет запись"""
    booking_id = int(callback.data.replace('driver_cancel_', ''))
    update_booking_status(booking_id, 'cancelled')
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ Запись #{booking_id} отменена.")
    
    # Уведомляем админа
    await bot.send_message(
        ADMIN_ID,
        f"❌ Водитель отменил запись #{booking_id}"
    )
    await callback.answer()

# ==================== ОБРАБОТЧИКИ ДЛЯ АДМИНА ====================

@dp.message(lambda message: message.text == "📋 Все записи" and message.from_user.id == ADMIN_ID)
async def admin_all_bookings(message: types.Message):
    """Админ смотрит все записи"""
    bookings = get_all_bookings()
    
    if not bookings:
        await message.answer("Нет записей.")
        return
    
    response = "📋 <b>Все записи:</b>\n\n"
    for booking in bookings:
        status_emoji = {
            'new': '🆕',
            'confirmed': '✅',
            'completed': '✔️',
            'cancelled': '❌',
            'admin_cancelled': '👑❌'
        }.get(booking[6], '📌')
        
        response += (
            f"{status_emoji} <b>#{booking[0]}</b> | {booking[1]}\n"
            f"👤 {booking[2]} | 📞 {booking[3]}\n"
            f"⏰ {booking[4]}\n"
            f"📝 {booking[8] if booking[8] else '—'}\n"
            f"🔹 {booking[6]} | через {booking[7]}\n\n"
        )
        
        # Ограничим длину сообщения
        if len(response) > 3000:
            await message.answer(response, parse_mode="HTML")
            response = ""
    
    if response:
        await message.answer(response, parse_mode="HTML")

@dp.message(lambda message: message.text == "➕ Записать клиента" and message.from_user.id == ADMIN_ID)
async def admin_start_booking(message: types.Message, state: FSMContext):
    """Админ начинает запись клиента"""
    drivers = get_drivers()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for driver_id, name in drivers:
        button = InlineKeyboardButton(
            text=name,
            callback_data=f"admin_driver_{driver_id}"
        )
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите водителя для записи:", reply_markup=keyboard)
    await state.set_state(AdminStates.choosing_driver_for_booking)

@dp.callback_query(AdminStates.choosing_driver_for_booking)
async def admin_choose_driver(callback: types.CallbackQuery, state: FSMContext):
    """Админ выбирает водителя"""
    driver_id = int(callback.data.replace("admin_driver_", ""))
    await state.update_data(driver_id=driver_id)
    
    await callback.message.answer("Введите ФИО клиента (Фамилия И.О.):")
    await callback.answer()
    await state.set_state(AdminStates.entering_client_full_name)

@dp.message(AdminStates.entering_client_full_name)
async def admin_enter_client_name(message: types.Message, state: FSMContext):
    """Админ вводит ФИО клиента"""
    await state.update_data(client_full_name=message.text)
    await message.answer("Введите телефон клиента:")
    await state.set_state(AdminStates.entering_client_phone)

@dp.message(AdminStates.entering_client_phone)
async def admin_enter_client_phone(message: types.Message, state: FSMContext):
    """Админ вводит телефон"""
    await state.update_data(client_phone=message.text)
    
    # Показываем варианты времени
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    now = datetime.now()
    
    for i in range(1, 13):  # 12 вариантов
        time = now.replace(minute=0, second=0) + timedelta(hours=i)
        time_str = time.strftime("%d.%m %H:00")
        button = InlineKeyboardButton(
            text=time_str,
            callback_data=f"admin_time_{time_str}"
        )
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите время записи:", reply_markup=keyboard)
    await state.set_state(AdminStates.entering_booking_time)

@dp.callback_query(AdminStates.entering_booking_time)
async def admin_choose_time(callback: types.CallbackQuery, state: FSMContext):
    """Админ выбирает время"""
    booking_time = callback.data.replace("admin_time_", "")
    await state.update_data(booking_time=booking_time)
    
    await callback.message.answer("Добавьте заметки к записи (или отправьте 'нет'):")
    await callback.answer()
    await state.set_state(AdminStates.entering_booking_notes)

@dp.message(AdminStates.entering_booking_notes)
async def admin_enter_notes(message: types.Message, state: FSMContext):
    """Админ добавляет заметки и завершает запись"""
    notes = message.text if message.text.lower() != 'нет' else ''
    data = await state.get_data()
    
    # Создаем запись (client_id = 0 для записей от админа)
    booking_id = create_booking(
        driver_id=data['driver_id'],
        client_id=0,
        client_name=data['client_full_name'],
        client_phone=data['client_phone'],
        booking_time=data['booking_time'],
        created_by='admin',
        notes=notes
    )
    
    await message.answer(
        f"✅ Клиент успешно записан!\n\n"
        f"Водитель: {get_driver_name(data['driver_id'])}\n"
        f"Клиент: {data['client_full_name']}\n"
        f"Телефон: {data['client_phone']}\n"
        f"Время: {data['booking_time']}\n"
        f"Заметки: {notes if notes else 'нет'}",
        reply_markup=get_admin_keyboard()
    )
    
    # Уведомление водителю
    await notify_driver(
        driver_id=data['driver_id'],
        client_name=data['client_full_name'],
        client_phone=data['client_phone'],
        booking_time=data['booking_time'],
        booking_id=booking_id,
        from_admin=True
    )
    
    await state.clear()

@dp.message(lambda message: message.text == "❌ Отменить запись" and message.from_user.id == ADMIN_ID)
async def admin_cancel_booking_menu(message: types.Message):
    """Админ выбирает запись для отмены"""
    bookings = get_all_bookings(status='new')
    bookings.extend(get_all_bookings(status='confirmed'))
    
    if not bookings:
        await message.answer("Нет активных записей для отмены.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for booking in bookings[:10]:  # Покажем только 10 последних
        text = f"#{booking[0]} | {booking[1]} | {booking[2]} | {booking[4]}"
        button = InlineKeyboardButton(
            text=text[:40] + "...",
            callback_data=f"admin_cancel_{booking[0]}"
        )
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите запись для отмены:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith('admin_cancel_'))
async def admin_confirm_cancel(callback: types.CallbackQuery):
    """Админ подтверждает отмену"""
    booking_id = int(callback.data.replace('admin_cancel_', ''))
    
    # Обновляем статус
    update_booking_status(booking_id, 'admin_cancelled', 'Отменено администратором')
    
    await callback.message.edit_text(f"✅ Запись #{booking_id} отменена администратором.")
    
    # Уведомляем водителя об отмене
    # TODO: уведомление водителю
    
    await callback.answer()

# ==================== ФУНКЦИИ УВЕДОМЛЕНИЙ ====================

async def notify_driver(driver_id, client_name, client_phone, booking_time, booking_id, from_admin=False):
    """Отправить уведомление водителю"""
    try:
        source = "Администратор" if from_admin else "Клиент"
        message = (
            f"🚗 <b>Новая запись!</b>\n\n"
            f"👤 Клиент: {client_name}\n"
            f"📞 Телефон: {client_phone}\n"
            f"⏰ Время: {booking_time}\n"
            f"🆔 Запись #{booking_id}\n"
            f"📝 Источник: {source}\n\n"
            f"Используйте кнопки ниже для подтверждения или отмены."
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"driver_confirm_{booking_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"driver_cancel_{booking_id}"
                )
            ]
        ])
        
        await bot.send_message(
            chat_id=driver_id,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        logging.info(f"Уведомление отправлено водителю {driver_id}")
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления водителю: {e}")

async def notify_admin(booking_id, driver_name, client_name, client_phone, booking_time):
    """Уведомить админа о новой записи"""
    try:
        message = (
            f"👑 <b>Новая запись в системе</b>\n\n"
            f"🆔 Запись #{booking_id}\n"
            f"🚗 Водитель: {driver_name}\n"
            f"👤 Клиент: {client_name}\n"
            f"📞 Телефон: {client_phone}\n"
            f"⏰ Время: {booking_time}"
        )
        
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка при уведомлении админа: {e}")

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================

async def health_check(request):
    return web.Response(text="I'm alive!")

async def webhook(request):
    return web.Response(text="OK")

async def start_bot():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_post('/webhook', webhook)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"🌐 Веб-сервер запущен на порту {port}")
    print("🚀 Бот с админ-панелью запущен!")
    
    await dp.start_polling(bot)

async def main():
    await start_bot()

if __name__ == "__main__":
    asyncio.run(main())
