# bot.py
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

import config
from database import *
from keyboards import *
from utils import *

import os
TOKEN = os.getenv('8443205937:AAGT7LoftJw-arONG58CcwDWXueuwbxYLps')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
ADD_EMPLOYEE, GET_NAME, GET_SCHEDULE_DATE, GET_SCHEDULE_EMPLOYEE, GET_SCHEDULE_TIME = range(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Проверяем, есть ли сотрудник с таким user_id
    employee = get_employee_by_user_id(user.id)
    
    if employee:
        # Если сотрудник уже есть в базе
        await update.message.reply_text(
            f"С возвращением, {employee['first_name']}!",
            reply_markup=get_main_keyboard(employee.get('is_admin', False))
        )
    else:
        # Если сотрудника нет, просим номер телефона
        await update.message.reply_text(
            "Добро пожаловать! Для идентификации поделитесь, пожалуйста, своим номером телефона.",
            reply_markup=get_phone_keyboard()
        )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения контакта"""
    user = update.effective_user
    contact = update.message.contact
    
    if contact and contact.user_id == user.id:
        # Ищем сотрудника по номеру телефона
        employee = get_employee_by_phone(contact.phone_number)
        
        if employee:
            # Обновляем user_id у сотрудника (на случай смены аккаунта)
            employee['user_id'] = user.id
            # Сохраняем обновленные данные
            employees = load_json("employees.json")
            for emp in employees:
                if emp['phone_number'] == contact.phone_number:
                    emp['user_id'] = user.id
            save_json("employees.json", employees)
            
            await update.message.reply_text(
                f"Добро пожаловать, {employee['first_name']}!",
                reply_markup=get_main_keyboard(employee.get('is_admin', False))
            )
        else:
            await update.message.reply_text(
                "Извините, ваш номер телефона не найден в системе. Обратитесь к администратору.",
                reply_markup=ReplyKeyboardRemove()
            )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    text = update.message.text
    user = update.effective_user
    
    # Получаем информацию о сотруднике
    employee = get_employee_by_user_id(user.id)
    if not employee:
        await update.message.reply_text("Сначала пройдите идентификацию через /start")
        return

    if text == "Начать смену":
        # Проверяем, нет ли уже активной смены
        active_shift = get_active_shift(user.id)
        if active_shift:
            await update.message.reply_text("У вас уже есть активная смена.")
        else:
            start_shift(user.id)
            start_time = datetime.now().strftime('%H:%M')
            await update.message.reply_text(f"✅ Смена начата в {start_time}")

    elif text == "Закончить смену":
        active_shift = get_active_shift(user.id)
        if not active_shift:
            await update.message.reply_text("У вас нет активной смены.")
        else:
            # Проверяем, прошло ли минимальное время (1 час)
            start_time = datetime.strptime(active_shift['shift_start'], '%Y-%m-%d %H:%M:%S')
            now = datetime.now()
            if (now - start_time).total_seconds() < 3600:  # 1 час в секундах
                await update.message.reply_text("❌ Нельзя закрыть смену раньше, чем через 1 час после начала.")
            else:
                end_shift(user.id)
                end_time = now.strftime('%H:%M')
                await update.message.reply_text(f"✅ Смена завершена в {end_time}")

    elif text == "График смен":
        schedule_message = format_schedule_message()
        await update.message.reply_text(schedule_message)

    elif text == "Мое время":
        shifts = get_user_shifts(user.id, 7)  # За последние 7 дней
        total_time = calculate_work_time(shifts)
        await update.message.reply_text(f"⏱ Ваше время за последнюю неделю: {total_time}")

    # Админские функции
    elif text == "Добавить сотрудника":
        if not employee.get('is_admin'):
            await update.message.reply_text("❌ У вас нет прав для этого действия.")
            return
        await update.message.reply_text(
            "Введите номер телефона нового сотрудника в формате +79123456789:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADD_EMPLOYEE

    elif text == "Управление графиком":
        if not employee.get('is_admin'):
            await update.message.reply_text("❌ У вас нет прав для этого действия.")
            return
        await update.message.reply_text(
            "Введите дату для графика (в формате ДД.ММ.ГГГГ):",
            reply_markup=ReplyKeyboardRemove()
        )
        return GET_SCHEDULE_DATE

    elif text == "Отчет за неделю":
        if not employee.get('is_admin'):
            await update.message.reply_text("❌ У вас нет прав для этого действия.")
            return
        report = generate_weekly_report()
        await update.message.reply_text(report)

    elif text == "Отчет за месяц":
        if not employee.get('is_admin'):
            await update.message.reply_text("❌ У вас нет прав для этого действия.")
            return
        report = generate_monthly_report()
        await update.message.reply_text(report)

    elif text == "На главную":
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_keyboard(employee.get('is_admin', False))
        )

async def add_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода номера телефона для нового сотрудника"""
    phone = update.message.text
    context.user_data['new_employee_phone'] = phone
    
    await update.message.reply_text("Введите ФИО нового сотрудника:")
    return GET_NAME

async def add_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода ФИО нового сотрудника"""
    full_name = update.message.text
    phone = context.user_data['new_employee_phone']
    
    # Простое разделение ФИО (можно улучшить)
    name_parts = full_name.split()
    first_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]
    last_name = name_parts[0] if name_parts else ""
    
    # Добавляем сотрудника (пока без user_id, он добавится при первом входе)
    if add_employee(None, phone, first_name, last_name):
        await update.message.reply_text(
            f"✅ Сотрудник {full_name} успешно добавлен!",
            reply_markup=get_main_keyboard(True)
        )
    else:
        await update.message.reply_text(
            "❌ Ошибка: сотрудник с таким номером уже существует.",
            reply_markup=get_main_keyboard(True)
        )
    
    return ConversationHandler.END

async def schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода даты для графика"""
    date = update.message.text
    context.user_data['schedule_date'] = date
    
    await update.message.reply_text("Введите ФИО сотрудника:")
    return GET_SCHEDULE_EMPLOYEE

async def schedule_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода сотрудника для графика"""
    employee_name = update.message.text
    context.user_data['schedule_employee'] = employee_name
    
    await update.message.reply_text("Введите время смены (например, 09:00-18:00):")
    return GET_SCHEDULE_TIME

async def schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода времени смены"""
    time = update.message.text
    date = context.user_data['schedule_date']
    employee_name = context.user_data['schedule_employee']
    
    add_to_schedule(date, employee_name, time)
    
    await update.message.reply_text(
        f"✅ Смена для {employee_name} на {date} добавлена в график!",
        reply_markup=get_main_keyboard(True)
    )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога"""
    employee = get_employee_by_user_id(update.effective_user.id)
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=get_main_keyboard(employee.get('is_admin', False))
    )
    return ConversationHandler.END

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(config.BOT_TOKEN).build()
    
    # Обработчик для добавления сотрудника (админ)
    add_employee_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Добавить сотрудника$"), button_handler)],
        states={
            ADD_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_employee_phone)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_employee_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Обработчик для управления графиком (админ)
    schedule_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Управление графиком$"), button_handler)],
        states={
            GET_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_date)],
            GET_SCHEDULE_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_employee)],
            GET_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(add_employee_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    
    # Запускаем бота
    print("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
