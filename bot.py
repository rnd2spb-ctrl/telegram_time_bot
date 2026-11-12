# bot.py
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

import config
from database import *
from keyboards import *
from utils import *


# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
ADD_EMPLOYEE, GET_NAME, GET_SCHEDULE_DATE, GET_SCHEDULE_EMPLOYEE, GET_SCHEDULE_TIME = range(5)


def get_user_role(user_id):
    """Возвращает owner/admin/employee либо None, если доступ запрещен."""
    if config.OWNER_ID is not None and user_id == config.OWNER_ID:
        return "owner"

    employee = get_employee_by_user_id(user_id)
    if employee and employee.get("status", "active") != "active":
        return None

    if user_id in config.ADMIN_IDS:
        return "admin"

    if employee:
        role = employee.get("role", "employee")
        if role == "employee":
            return role

    # Старый флаг is_admin намеренно не используется как источник полномочий:
    # он не подтверждает, кому именно принадлежит запись сотрудника.
    return None


def is_admin_role(role):
    return role in {"owner", "admin"}


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повторно проверяет права на каждом шаге административного диалога."""
    user = update.effective_user
    role = get_user_role(user.id) if user else None
    if not is_admin_role(role):
        if update.message:
            await update.message.reply_text(
                "❌ Недостаточно прав. Административное действие отменено."
            )
        context.user_data.pop("new_employee_phone", None)
        context.user_data.pop("schedule_date", None)
        context.user_data.pop("schedule_employee", None)
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)

    if is_admin_role(role):
        display_name = employee["first_name"] if employee else user.first_name
        await update.message.reply_text(
            f"Здравствуйте, {display_name}! Доступ администратора подтвержден.",
            reply_markup=get_main_keyboard(True)
        )
    elif employee and role == "employee":
        await update.message.reply_text(
            f"С возвращением, {employee['first_name']}!",
            reply_markup=get_main_keyboard(False)
        )
    elif employee:
        await update.message.reply_text(
            "❌ Ваш доступ приостановлен. Обратитесь к владельцу бота.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "Добро пожаловать! Для идентификации поделитесь, пожалуйста, своим номером телефона.",
            reply_markup=get_phone_keyboard()
        )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения собственного контакта сотрудника."""
    user = update.effective_user
    contact = update.message.contact

    if not contact or contact.user_id != user.id:
        await update.message.reply_text(
            "Пожалуйста, отправьте именно свой номер телефона через кнопку ниже.",
            reply_markup=get_phone_keyboard()
        )
        return

    employee = get_employee_by_phone(contact.phone_number)
    if not employee:
        await update.message.reply_text(
            "Извините, ваш номер телефона не найден в системе. Обратитесь к владельцу бота.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if employee.get("status", "active") != "active" and user.id != config.OWNER_ID:
        await update.message.reply_text(
            "❌ Ваш доступ приостановлен. Обратитесь к владельцу бота.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    bound, reason, employee = bind_employee_to_user(contact.phone_number, user.id)
    if not bound:
        messages = {
            "phone_already_bound": "Этот номер уже привязан к другому Telegram-аккаунту. Обратитесь к владельцу.",
            "user_already_bound": "Ваш Telegram-аккаунт уже привязан к другой записи сотрудника. Обратитесь к владельцу.",
            "not_found": "Сотрудник с таким номером не найден."
        }
        await update.message.reply_text(
            messages.get(reason, "Не удалось выполнить привязку. Обратитесь к владельцу."),
            reply_markup=ReplyKeyboardRemove()
        )
        return

    role = get_user_role(user.id)
    await update.message.reply_text(
        f"Добро пожаловать, {employee['first_name']}!",
        reply_markup=get_main_keyboard(is_admin_role(role))
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню."""
    text = update.message.text
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)

    if role is None:
        await update.message.reply_text(
            "Сначала пройдите идентификацию через /start или обратитесь к владельцу."
        )
        return

    if text == "Начать смену":
        if not employee:
            await update.message.reply_text("Для учета рабочего времени сначала привяжите свой номер телефона через /start.")
            return
        active_shift = get_active_shift(user.id)
        if active_shift:
            await update.message.reply_text("У вас уже есть активная смена.")
        else:
            start_shift(user.id)
            start_time = datetime.now().strftime("%H:%M")
            await update.message.reply_text(f"✅ Смена начата в {start_time}")

    elif text == "Закончить смену":
        if not employee:
            await update.message.reply_text("Для учета рабочего времени сначала привяжите свой номер телефона через /start.")
            return
        active_shift = get_active_shift(user.id)
        if not active_shift:
            await update.message.reply_text("У вас нет активной смены.")
        else:
            start_time = datetime.strptime(active_shift["shift_start"], "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            if (now - start_time).total_seconds() < 3600:
                await update.message.reply_text("❌ Нельзя закрыть смену раньше, чем через 1 час после начала.")
            else:
                end_shift(user.id)
                end_time = now.strftime("%H:%M")
                await update.message.reply_text(f"✅ Смена завершена в {end_time}")

    elif text == "График смен":
        schedule_message = format_schedule_message()
        await update.message.reply_text(schedule_message)

    elif text == "Мое время":
        if not employee:
            await update.message.reply_text("Для просмотра личного времени сначала привяжите свой номер телефона через /start.")
            return
        shifts = get_user_shifts(user.id, 7)
        total_time = calculate_work_time(shifts)
        await update.message.reply_text(f"⏱ Ваше время за последнюю неделю: {total_time}")

    elif text == "Добавить сотрудника":
        if not await require_admin(update, context):
            return
        await update.message.reply_text(
            "Введите номер телефона нового сотрудника в формате +79123456789:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADD_EMPLOYEE

    elif text == "Управление графиком":
        if not await require_admin(update, context):
            return
        await update.message.reply_text(
            "Введите дату для графика (в формате ДД.ММ.ГГГГ):",
            reply_markup=ReplyKeyboardRemove()
        )
        return GET_SCHEDULE_DATE

    elif text == "Отчет за неделю":
        if not await require_admin(update, context):
            return
        report = generate_weekly_report()
        await update.message.reply_text(report)

    elif text == "Отчет за месяц":
        if not await require_admin(update, context):
            return
        report = generate_monthly_report()
        await update.message.reply_text(report)

    elif text == "На главную":
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_keyboard(is_admin_role(role))
        )


async def add_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первый шаг добавления сотрудника: номер телефона."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    phone = update.message.text.strip()
    if not phone:
        await update.message.reply_text("Номер телефона не может быть пустым. Введите номер еще раз:")
        return ADD_EMPLOYEE

    context.user_data["new_employee_phone"] = phone
    await update.message.reply_text("Введите ФИО нового сотрудника:")
    return GET_NAME


async def add_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Второй шаг добавления сотрудника: ФИО."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    phone = context.user_data.get("new_employee_phone")
    full_name = update.message.text.strip()
    if not phone:
        await update.message.reply_text("Сценарий добавления сотрудника устарел. Начните заново.")
        return ConversationHandler.END
    if not full_name:
        await update.message.reply_text("ФИО не может быть пустым. Введите ФИО еще раз:")
        return GET_NAME

    name_parts = full_name.split()
    last_name = name_parts[0]
    first_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]

    if add_employee(None, phone, first_name, last_name):
        await update.message.reply_text(
            f"✅ Сотрудник {full_name} успешно добавлен!",
            reply_markup=get_main_keyboard(True)
        )
    else:
        await update.message.reply_text(
            "❌ Сотрудник с таким номером телефона уже существует.",
            reply_markup=get_main_keyboard(True)
        )

    context.user_data.pop("new_employee_phone", None)
    return ConversationHandler.END


async def schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первый шаг настройки графика: дата."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    context.user_data["schedule_date"] = update.message.text.strip()
    await update.message.reply_text("Введите ФИО сотрудника:")
    return GET_SCHEDULE_EMPLOYEE


async def schedule_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Второй шаг настройки графика: сотрудник."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    context.user_data["schedule_employee"] = update.message.text.strip()
    await update.message.reply_text("Введите время смены (например, 09:00-18:00):")
    return GET_SCHEDULE_TIME


async def schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Последний шаг настройки графика: время смены."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    date = context.user_data.get("schedule_date")
    employee_name = context.user_data.get("schedule_employee")
    shift_time = update.message.text.strip()

    if not date or not employee_name:
        await update.message.reply_text("Сценарий настройки графика устарел. Начните заново.")
        return ConversationHandler.END

    add_to_schedule(date, employee_name, shift_time)
    await update.message.reply_text(
        f"✅ Смена для {employee_name} на {date} добавлена в график!",
        reply_markup=get_main_keyboard(True)
    )
    context.user_data.pop("schedule_date", None)
    context.user_data.pop("schedule_employee", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена административного диалога."""
    role = get_user_role(update.effective_user.id)
    context.user_data.pop("new_employee_phone", None)
    context.user_data.pop("schedule_date", None)
    context.user_data.pop("schedule_employee", None)

    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=get_main_keyboard(is_admin_role(role))
    )
    return ConversationHandler.END


def main():
    """Запуск бота."""
    if not config.BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example "
            "и укажите новый токен, полученный у @BotFather."
        )
    if config.OWNER_ID is None:
        raise RuntimeError(
            "Не назначен владелец. Укажите свой числовой Telegram ID в OWNER_ID файла .env."
        )

    application = Application.builder().token(config.BOT_TOKEN).build()

    add_employee_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Добавить сотрудника$"), button_handler)],
        states={
            ADD_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_employee_phone)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_employee_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    schedule_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Управление графиком$"), button_handler)],
        states={
            GET_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_date)],
            GET_SCHEDULE_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_employee)],
            GET_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(add_employee_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()
