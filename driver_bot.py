@dp.message(AdminStates.waiting_for_driver_car)
async def admin_add_driver_car(message: types.Message, state: FSMContext):
    car_info = message.text.strip()
    if car_info == '-':
        car_info = ""
    
    data = await state.get_data()
    
    success, name = add_driver_to_db(
        driver_id=data['driver_id'],
        full_name=data['full_name'],
        phone=data['phone'],
        car_info=car_info,
        added_by=message.from_user.id
    )
    
    if success:
        await message.answer(
            f"✅ Водитель {name} успешно добавлен!\n\n"
            f"ID: {data['driver_id']}\n"
            f"Телефон: {data['phone'] or 'не указан'}\n"
            f"Машина: {car_info or 'не указана'}",
            reply_markup=get_admin_keyboard()
        )
        
        # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ВОДИТЕЛЮ
        try:
            await bot.send_message(
                data['driver_id'],
                f"🚖 <b>Вас добавили как водителя в систему!</b>\n\n"
                f"👤 Ваше имя: {data['full_name']}\n"
                f"📞 Телефон: {data['phone'] or 'не указан'}\n"
                f"🚘 Машина: {car_info or 'не указана'}\n\n"
                f"📋 Напишите /start, чтобы начать работу и просматривать свои записи.",
                parse_mode="HTML"
            )
            logging.info(f"Уведомление отправлено водителю {data['driver_id']}")
        except Exception as e:
            # Если не удалось отправить уведомление (водитель не запускал бота)
            logging.error(f"Не удалось отправить уведомление водителю {data['driver_id']}: {e}")
            await message.answer(
                f"⚠️ Водитель добавлен, но НЕ удалось отправить ему уведомление.\n"
                f"Возможно, он еще не запускал бота. Попросите его написать /start"
            )
    else:
        await message.answer(
            f"❌ Водитель с ID {data['driver_id']} уже существует в системе.",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()