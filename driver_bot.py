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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8687705334:AAFLQxeDPtf8FUa35mts2icQNBJUdPJ5kEY")

# ID администратора (ваш Telegram ID)
ADMIN_ID = 993913729

# Словарь будет заполняться из базы данных
DRIVERS = {}
DRIVER_NAMES = {}

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
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  driver_id INTEGER UNIQUE,
                  full_name TEXT,
                  phone TEXT,
                  car_info TEXT,
                  added_by INTEGER,
                  added_date TEXT)''')
    
    # Таблица клиентов
    c.execute('''CREATE TABLE IF NOT EXISTS clients
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER UNIQUE,
                  full_name TEXT,
                  phone TEXT,
                  first_seen TEXT)''')
    
    # Таблица записей
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  driver_id INTEGER,
                  client_id INTEGER,
                  client_full_name TEXT,
                  client_phone TEXT,
                  booking_date TEXT,
                  booking_time TEXT,
                  booking_datetime TEXT,
                  created_at TEXT,
                  status TEXT DEFAULT 'active',
                  created_by TEXT DEFAULT 'client',
                  notes TEXT,
                  FOREIGN KEY (driver_id) REFERENCES drivers (driver_id),
                  FOREIGN KEY (client_id) REFERENCES clients (user_id))''')
    
    # Таблица для заблокированных слотов
    c.execute('''CREATE TABLE IF NOT EXISTS booked_slots
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  driver_id INTEGER,
                  slot_date TEXT,
                  slot_time TEXT,
                  booking_id INTEGER,
                  UNIQUE(driver_id, slot_date, slot_time))''')
    
    conn.commit()
    conn.close()

def load_drivers():
    """Загружает список водителей из базы в память"""
    global DRIVERS, DRIVER_NAMES
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT driver_id, full_name FROM drivers")
    drivers = c.fetchall()
    conn.close()
    
    DRIVERS = {name: driver_id for driver_id, name in drivers}
    DRIVER_NAMES = {driver_id: name for driver_id, name in drivers}

def add_driver_to_db(driver_id, full_name, phone="", car_info="", added_by=0):
    """Добавляет водителя в базу"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        c.execute("""INSERT INTO drivers (driver_id, full_name, phone, car_info, added_by, added_date)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (driver_id, full_name, phone, car_info, added_by, now))
        conn.commit()
        load_drivers()  # Обновляем глобальные словари
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def delete_driver_from_db(driver_id):
    """Удаляет водителя из базы"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("DELETE FROM drivers WHERE driver_id = ?", (driver_id,))
    c.execute("DELETE FROM booked_slots WHERE driver_id = ?", (driver_id,))
    # Записи остаются для истории, но статус меняем
    c.execute("UPDATE bookings SET status = 'driver_deleted' WHERE driver_id = ?", (driver_id,))
    conn.commit()
    deleted = c.rowcount > 0
    conn.close()
    if deleted:
        load_drivers()  # Обновляем глобальные словари
    return deleted

def get_all_drivers():
    """Получает список всех водителей из базы"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT driver_id, full_name, phone, car_info, added_date FROM drivers ORDER BY full_name")
    drivers = c.fetchall()
    conn.close()
    return drivers

# Инициализируем базу и загружаем водителей
init_db()
load_drivers()

# ==================== СОСТОЯНИЯ ДЛЯ АДМИНА ====================

class AdminStates(StatesGroup):
    waiting_for_driver_id = State()
    waiting_for_driver_name = State()
    waiting_for_driver_phone = State()
    waiting_for_driver_car = State()
    waiting_for_driver_delete = State()

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАТАМИ ====================

def get_next_workdays(count=5):
    """Возвращает список следующих рабочих дней (пн-пт)"""
    workdays = []
    current_date = datetime.now().date()
    
    while len(workdays) < count:
        if current_date.weekday() < 5:  # 0-4: пн-пт
            workdays.append(current_date)
        current_date += timedelta(days=1)
    
    return workdays

def get_time_slots():
    """Возвращает список временных слотов 09:00-17:30"""
    slots = []
    start_time = datetime.strptime("09:00", "%H:%M")
    end_time = datetime.strptime("17:30", "%H:%M")
    
    current = start_time
    while current <= end_time:
        time_str = current.strftime("%H:%M")
        next_time = (current + timedelta(minutes=30)).strftime("%H:%M")
        slots.append(f"{time_str}-{next_time}")
        current += timedelta(minutes=30)
    
    return slots

def is_slot_available(driver_id, date, time_slot):
    """Проверяет, свободен ли слот"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT id FROM booked_slots WHERE driver_id = ? AND slot_date = ? AND slot_time = ?",
              (driver_id, date.strftime("%Y-%m-%d"), time_slot))
    result = c.fetchone()
    conn.close()
    return result is None

def book_slot(driver_id, date, time_slot, booking_id):
    """Помечает слот как занятый"""
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO booked_slots (driver_id, slot_date, slot_time, booking_id) VALUES (?, ?, ?, ?)",
                  (driver_id, date.strftime("%Y-%m-%d"), time_slot, booking_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

# ==================== СОСТОЯНИЯ ДЛЯ КЛИЕНТОВ ====================

class ClientBooking(StatesGroup):
    entering_full_name = State()
    choosing_driver = State()
    choosing_date = State()
    choosing_time = State()

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard():
    """Клавиатура для клиентов"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="🚗 Записаться к водителю")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_driver_keyboard():
    """Клавиатура для водителей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои записи")],
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
            [KeyboardButton(text="🚗 Управление водителями")],
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_driver_management_keyboard():
    """Клавиатура для управления водителями"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Список водителей")],
            [KeyboardButton(text="➕ Добавить водителя")],
            [KeyboardButton(text="❌ Удалить водителя")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

# ==================== ОБРАБОТЧИКИ ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало работы с ботом"""
    user_id = message.from_user.id
    
    # Определяем роль
    if user_id == ADMIN_ID:
        text = "👑 Добро пожаловать, Администратор!"
        keyboard = get_admin_keyboard()
        await message.answer(text, reply_markup=keyboard)
        
    elif user_id in DRIVER_NAMES:
        driver_name = DRIVER_NAMES[user_id]
        text = f"🚖 Добро пожаловать, {driver_name}!"
        keyboard = get_driver_keyboard()
        await message.answer(text, reply_markup=keyboard)
        
    else:
        # Проверяем, есть ли клиент в базе
        conn = sqlite3.connect('drivers.db')
        c = conn.cursor()
        c.execute("SELECT full_name FROM clients WHERE user_id = ?", (user_id,))
        client = c.fetchone()
        conn.close()
        
        if client:
            text = f"👋 С возвращением, {client[0]}!"
            keyboard = get_main_keyboard()
            await message.answer(text, reply_markup=keyboard)
        else:
            text = "👋 Добро пожаловать! Введите вашу Фамилию Имя Отчество:"
            await message.answer(text)
            await state.set_state(ClientBooking.entering_full_name)

# ==================== ОБРАБОТЧИКИ ДЛЯ КЛИЕНТОВ ====================

@dp.message(ClientBooking.entering_full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    """Сохранение ФИО нового клиента"""
    full_name = message.text.strip()
    
    if len(full_name.split()) < 2:
        await message.answer("Пожалуйста, введите фамилию, имя и отчество полностью")
        return
    
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        c.execute("INSERT INTO clients (user_id, full_name, first_seen) VALUES (?, ?, ?)",
                  (message.from_user.id, full_name, now))
        conn.commit()
    except sqlite3.IntegrityError:
        c.execute("UPDATE clients SET full_name = ? WHERE user_id = ?",
                  (full_name, message.from_user.id))
        conn.commit()
    finally:
        conn.close()
    
    await message.answer(f"✅ Спасибо, {full_name}!", reply_markup=get_main_keyboard())
    await state.clear()

@dp.message(lambda message: message.text == "🚗 Записаться к водителю")
async def start_booking(message: types.Message, state: FSMContext):
    """Начало процесса записи"""
    if not DRIVERS:
        await message.answer("😔 Пока нет доступных водителей.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for name, driver_id in DRIVERS.items():
        button = InlineKeyboardButton(text=name, callback_data=f"book_driver_{driver_id}")
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите водителя:", reply_markup=keyboard)
    await state.set_state(ClientBooking.choosing_driver)

@dp.callback_query(ClientBooking.choosing_driver)
async def process_driver_choice(callback: types.CallbackQuery, state: FSMContext):
    """Выбор водителя"""
    driver_id = int(callback.data.replace("book_driver_", ""))
    await state.update_data(driver_id=driver_id)
    
    workdays = get_next_workdays(5)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for date in workdays:
        date_str = date.strftime("%d.%m.%Y")
        weekday = ["Пн", "Вт", "Ср", "Чт", "Пт"][date.weekday()]
        button = InlineKeyboardButton(
            text=f"{date_str} ({weekday})",
            callback_data=f"book_date_{date.strftime('%Y-%m-%d')}"
        )
        keyboard.inline_keyboard.append([button])
    
    await callback.message.answer("Выберите дату:", reply_markup=keyboard)
    await callback.answer()
    await state.set_state(ClientBooking.choosing_date)

@dp.callback_query(ClientBooking.choosing_date)
async def process_date_choice(callback: types.CallbackQuery, state: FSMContext):
    """Выбор даты"""
    date_str = callback.data.replace("book_date_", "")
    booking_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    await state.update_data(booking_date=booking_date)
    
    data = await state.get_data()
    driver_id = data['driver_id']
    
    all_slots = get_time_slots()
    available_slots = []
    
    for slot in all_slots:
        if is_slot_available(driver_id, booking_date, slot):
            available_slots.append(slot)
    
    if not available_slots:
        await callback.message.answer("😔 На эту дату все слоты заняты.")
        # Показываем даты заново
        workdays = get_next_workdays(5)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for date in workdays:
            date_str = date.strftime("%d.%m.%Y")
            weekday = ["Пн", "Вт", "Ср", "Чт", "Пт"][date.weekday()]
            button = InlineKeyboardButton(
                text=f"{date_str} ({weekday})",
                callback_data=f"book_date_{date.strftime('%Y-%m-%d')}"
            )
            keyboard.inline_keyboard.append([button])
        await callback.message.answer("Выберите другую дату:", reply_markup=keyboard)
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for slot in available_slots:
        button = InlineKeyboardButton(text=slot, callback_data=f"book_time_{slot}")
        keyboard.inline_keyboard.append([button])
    
    await callback.message.answer("Выберите время:", reply_markup=keyboard)
    await callback.answer()
    await state.set_state(ClientBooking.choosing_time)

@dp.callback_query(ClientBooking.choosing_time)
async def process_time_choice(callback: types.CallbackQuery, state: FSMContext):
    """Выбор времени и создание записи"""
    time_slot = callback.data.replace("book_time_", "")
    data = await state.get_data()
    
    driver_id = data['driver_id']
    booking_date = data['booking_date']
    client_id = callback.from_user.id
    
    # Получаем ФИО клиента
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("SELECT full_name FROM clients WHERE user_id = ?", (client_id,))
    client = c.fetchone()
    conn.close()
    
    if not client:
        await callback.message.answer("Ошибка: не найдены ваши данные. Начните с /start")
        await state.clear()
        return
    
    client_full_name = client[0]
    
    # Проверяем, не занят ли слот
    if not is_slot_available(driver_id, booking_date, time_slot):
        await callback.message.answer("😔 Это время только что заняли. Выберите другое.")
        await state.set_state(ClientBooking.choosing_time)
        await callback.answer()
        return
    
    # Создаем запись
    booking_datetime_str = f"{booking_date.strftime('%Y-%m-%d')} {time_slot.split('-')[0]}"
    booking_datetime = datetime.strptime(booking_datetime_str, "%Y-%m-%d %H:%M")
    
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("""INSERT INTO bookings 
                 (driver_id, client_id, client_full_name, booking_date, booking_time, 
                  booking_datetime, created_at, status)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (driver_id, client_id, client_full_name, 
               booking_date.strftime("%Y-%m-%d"), time_slot,
               booking_datetime.strftime("%Y-%m-%d %H:%M:%S"),
               now, 'active'))
    
    booking_id = c.lastrowid
    
    # Помечаем слот
    c.execute("INSERT INTO booked_slots (driver_id, slot_date, slot_time, booking_id) VALUES (?, ?, ?, ?)",
              (driver_id, booking_date.strftime("%Y-%m-%d"), time_slot, booking_id))
    
    conn.commit()
    conn.close()
    
    # Сообщение клиенту
    driver_name = DRIVER_NAMES.get(driver_id, "Водитель")
    display_date = booking_date.strftime("%d.%m.%Y")
    
    await callback.message.answer(
        f"✅ Вы записаны!\n\n"
        f"🚗 {driver_name}\n"
        f"📅 {display_date}\n"
        f"⏰ {time_slot}",
        reply_markup=get_main_keyboard()
    )
    
    # Уведомление водителю
    try:
        name_parts = client_full_name.split()
        short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}." if len(name_parts) >= 3 else client_full_name
        
        await bot.send_message(
            driver_id,
            f"🚗 <b>Новая запись!</b>\n\n"
            f"К вам записался: <b>{short_name}</b>\n"
            f"📅 Дата: {display_date}\n"
            f"⏰ Время: {time_slot}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления водителя: {e}")
    
    # Уведомление админу
    try:
        await bot.send_message(
            ADMIN_ID,
            f"👑 <b>Новая запись</b>\n\n"
            f"🚗 Водитель: {driver_name}\n"
            f"👤 Клиент: {client_full_name}\n"
            f"📅 Дата: {display_date}\n"
            f"⏰ Время: {time_slot}"
        )
    except:
        pass
    
    await callback.answer()
    await state.clear()

@dp.message(lambda message: message.text == "📋 Мои записи")
async def client_my_bookings(message: types.Message):
    """Клиент смотрит свои записи"""
    user_id = message.from_user.id
    
    # Для админа показываем все записи
    if user_id == ADMIN_ID:
        conn = sqlite3.connect('drivers.db')
        c = conn.cursor()
        c.execute("""SELECT b.id, d.full_name, b.client_full_name, b.booking_date, b.booking_time, b.status
                     FROM bookings b
                     LEFT JOIN drivers d ON b.driver_id = d.driver_id
                     WHERE b.status = 'active'
                     ORDER BY b.booking_datetime""")
        bookings = c.fetchall()
        conn.close()
        
        if not bookings:
            await message.answer("Нет активных записей.")
            return
        
        text = "📋 <b>Все активные записи:</b>\n\n"
        for b in bookings:
            text += f"#{b[0]} {b[1]} — {b[2]}\n{b[3]} {b[4]}\n\n"
        
        await message.answer(text, parse_mode="HTML")
        return
    
    # Для водителя - его записи
    if user_id in DRIVER_NAMES:
        conn = sqlite3.connect('drivers.db')
        c = conn.cursor()
        c.execute("""SELECT b.id, b.client_full_name, b.booking_date, b.booking_time, b.status
                     FROM bookings b
                     WHERE b.driver_id = ? AND b.status = 'active'
                     ORDER BY b.booking_datetime""", (user_id,))
        bookings = c.fetchall()
        conn.close()
        
        if not bookings:
            await message.answer("У вас нет активных записей.")
            return
        
        text = "📋 <b>Ваши записи:</b>\n\n"
        for b in bookings:
            text += f"👤 {b[1]}\n📅 {b[2]} {b[3]}\n\n"
        
        await message.answer(text, parse_mode="HTML")
        return
    
    # Для обычного клиента - его записи
    conn = sqlite3.connect('drivers.db')
    c = conn.cursor()
    c.execute("""SELECT d.full_name, b.booking_date, b.booking_time
                 FROM bookings b
                 LEFT JOIN drivers d ON b.driver_id = d.driver_id
                 WHERE b.client_id = ? AND b.status = 'active'
                 ORDER BY b.booking_datetime""", (user_id,))
    bookings = c.fetchall()
    conn.close()
    
    if not bookings:
        await message.answer("У вас нет активных записей.")
        return
    
    text = "📋 <b>Ваши записи:</b>\n\n"
    for b in bookings:
        text += f"🚗 {b[0]}\n📅 {b[1]} {b[2]}\n\n"
    
    await message.answer(text, parse_mode="HTML")

# ==================== АДМИН: УПРАВЛЕНИЕ ВОДИТЕЛЯМИ ====================

@dp.message(lambda message: message.text == "🚗 Управление водителями" and message.from_user.id == ADMIN_ID)
async def admin_driver_management(message: types.Message):
    """Меню управления водителями"""
    await message.answer("Управление водителями:", reply_markup=get_driver_management_keyboard())

@dp.message(lambda message: message.text == "📋 Список водителей" and message.from_user.id == ADMIN_ID)
async def admin_list_drivers(message: types.Message):
    """Показать список всех водителей"""
    drivers = get_all_drivers()
    
    if not drivers:
        await message.answer("В системе нет водителей.")
        return
    
    text = "🚗 <b>Список водителей:</b>\n\n"
    for d in drivers:
        text += f"🆔 {d[0]}\n👤 {d[1]}\n📞 {d[2] or 'не указан'}\n🚘 {d[3] or 'не указана'}\n📅 Добавлен: {d[4]}\n\n"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(lambda message: message.text == "➕ Добавить водителя" and message.from_user.id == ADMIN_ID)
async def admin_add_driver_start(message: types.Message, state: FSMContext):
    """Начать добавление водителя"""
    await message.answer(
        "Добавление нового водителя.\n\n"
        "1️⃣ Отправьте Telegram ID водителя.\n"
        "(Узнать ID можно через @userinfobot)"
    )
    await state.set_state(AdminStates.waiting_for_driver_id)

@dp.message(AdminStates.waiting_for_driver_id)
async def admin_add_driver_id(message: types.Message, state: FSMContext):
    """Получить ID водителя"""
    try:
        driver_id = int(message.text.strip())
        await state.update_data(driver_id=driver_id)
        await message.answer("2️⃣ Введите полное имя водителя (Фамилия И.О.):")
        await state.set_state(AdminStates.waiting_for_driver_name)
    except ValueError:
        await message.answer("❌ ID должен быть числом. Попробуйте еще раз.")

@dp.message(AdminStates.waiting_for_driver_name)
async def admin_add_driver_name(message: types.Message, state: FSMContext):
    """Получить имя водителя"""
    await state.update_data(full_name=message.text.strip())
    await message.answer("3️⃣ Введите телефон водителя (или отправьте '-' если нет):")
    await state.set_state(AdminStates.waiting_for_driver_phone)

@dp.message(AdminStates.waiting_for_driver_phone)
async def admin_add_driver_phone(message: types.Message, state: FSMContext):
    """Получить телефон водителя"""
    phone = message.text.strip()
    if phone == '-':
        phone = ""
    await state.update_data(phone=phone)
    await message.answer("4️⃣ Введите информацию о машине (или '-' если нет):")
    await state.set_state(AdminStates.waiting_for_driver_car)

@dp.message(AdminStates.waiting_for_driver_car)
async def admin_add_driver_car(message: types.Message, state: FSMContext):
    """Получить информацию о машине и сохранить"""
    car_info = message.text.strip()
    if car_info == '-':
        car_info = ""
    
    data = await state.get_data()
    
    success = add_driver_to_db(
        driver_id=data['driver_id'],
        full_name=data['full_name'],
        phone=data['phone'],
        car_info=car_info,
        added_by=message.from_user.id
    )
    
    if success:
        await message.answer(
            f"✅ Водитель успешно добавлен!\n\n"
            f"👤 {data['full_name']}\n"
            f"🆔 {data['driver_id']}",
            reply_markup=get_admin_keyboard()
        )
        
        # Отправляем приветствие новому водителю
        try:
            await bot.send_message(
                data['driver_id'],
                f"🚖 Вас добавили как водителя в систему!\n"
                f"Ваше имя: {data['full_name']}\n"
                f"Напишите /start для начала работы."
            )
        except:
            pass
    else:
        await message.answer(
            "❌ Водитель с таким ID уже существует в системе.",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()

@dp.message(lambda message: message.text == "❌ Удалить водителя" and message.from_user.id == ADMIN_ID)
async def admin_delete_driver_start(message: types.Message, state: FSMContext):
    """Начать удаление водителя"""
    drivers = get_all_drivers()
    
    if not drivers:
        await message.answer("В системе нет водителей для удаления.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for d in drivers:
        button = InlineKeyboardButton(
            text=f"{d[1]} (ID: {d[0]})",
            callback_data=f"del_driver_{d[0]}"
        )
        keyboard.inline_keyboard.append([button])
    
    await message.answer("Выберите водителя для удаления:", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_driver_delete)

@dp.callback_query(AdminStates.waiting_for_driver_delete)
async def admin_delete_driver_confirm(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение удаления водителя"""
    driver_id = int(callback.data.replace("del_driver_", ""))
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_{driver_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_del")]
    ])
    
    await callback.message.answer(
        f"⚠️ Вы уверены, что хотите удалить водителя?\n"
        f"Все его будущие слоты будут освобождены, но история записей сохранится.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_del_"))
async def admin_delete_driver_execute(callback: types.CallbackQuery, state: FSMContext):
    """Исполнение удаления водителя"""
    driver_id = int(callback.data.replace("confirm_del_", ""))
    
    # Получаем имя для уведомления
    driver_name = DRIVER_NAMES.get(driver_id, "Водитель")
    
    success = delete_driver_from_db(driver_id)
    
    if success:
        await callback.message.edit_text(
            f"✅ Водитель {driver_name} удален из системы."
        )
        
        # Уведомляем водителя
        try:
            await bot.send_message(
                driver_id,
                "❌ Вы были удалены из системы водителей.\n"
                "По вопросам обратитесь к администратору."
            )
        except:
            pass
    else:
        await callback.message.edit_text("❌ Ошибка при удалении водителя.")
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "cancel_del")
async def admin_delete_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена удаления"""
    await callback.message.edit_text("❌ Удаление отменено.")
    await state.clear()

@dp.message(lambda message: message.text == "🔙 Назад" and message.from_user.id == ADMIN_ID)
async def admin_back_to_main(message: types.Message):
    """Вернуться в главное меню админа"""
    await message.answer("Главное меню:", reply_markup=get_admin_keyboard())

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
    print(f"🚀 Бот запущен! Админ ID: {ADMIN_ID}")
    print(f"👥 Водителей в базе: {len(DRIVERS)}")
    
    await dp.start_polling(bot)

async def main():
    await start_bot()

if __name__ == "__main__":
    asyncio.run(main())
