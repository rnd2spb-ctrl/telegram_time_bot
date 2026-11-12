# bot.py
import logging
import re
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
ADD_EMPLOYEE, GET_NAME, GET_SCHEDULE_DATE, GET_SCHEDULE_EMPLOYEE, GET_SCHEDULE_TIME, EDIT_EMPLOYEE_SELECT, EDIT_EMPLOYEE_ROLE = range(7)

MENU_BUTTONS = (
    "Начать смену", "Закончить смену", "График смен", "Мое время",
    "Добавить сотрудника", "Управление графиком", "Список сотрудников",
    "Редактировать сотрудника", "Отчет за неделю", "Отчет за месяц",
    "На главную", "⬅️ Назад в меню"
)
MENU_BUTTON_PATTERN = r"^(?:" + "|".join(re.escape(label) for label in MENU_BUTTONS) + r")$"


def get_user_role(user_id):
    """Возвращает owner/admin/employee либо None, если доступ запрещен."""
    if config.OWNER_ID is not None and user_id == config.OWNER_ID:
        return "owner"

    employee = get_employee_by_user_id(user_id)
    if employee:
        if employee.get("status", "active") != "active":
            return None
        role = employee.get("role", "employee")
        if role in {"admin", "employee"}:
            return role
        return None

    # Совместимость со старыми ADMIN_IDS для аккаунтов без записи сотрудника.
    if user_id in config.ADMIN_IDS:
        return "admin"

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
            reply_markup=get_main_keyboard(True, role == "owner")
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
        reply_markup=get_main_keyboard(is_admin_role(role), role == "owner")
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
            reply_markup=get_dialog_keyboard()
        )
        return ADD_EMPLOYEE

    elif text == "Управление графиком":
        if not await require_admin(update, context):
            return
        await update.message.reply_text(
            "Введите дату для графика (в формате ДД.ММ.ГГГГ):",
            reply_markup=get_dialog_keyboard()
        )
        return GET_SCHEDULE_DATE

    elif text == "Список сотрудников":
        if not await require_admin(update, context):
            return
        employees = get_all_employees()
        if not employees:
            message = "Список сотрудников пока пуст."
        else:
            lines = ["👥 Список сотрудников:"]
            for item in employees:
                full_name = " ".join(
                    part for part in (item.get("last_name"), item.get("first_name")) if part
                ) or "Имя не указано"
                phone = item.get("phone_number") or "телефон не указан"
                status = item.get("status", "не указан")
                role_name = item.get("role", "employee")
                if not isinstance(phone, str) or not re.fullmatch(r"\+?\d{7,15}", phone):
                    lines.append(f"⚠️ Некорректная запись: {full_name} — {phone}")
                    continue
                lines.append(f"• {full_name} — {phone} — {status} — {role_name}")
            message = "\n".join(lines)
        await update.message.reply_text(
            message,
            reply_markup=get_main_keyboard(True, role == "owner")
        )

    elif text == "Редактировать сотрудника":
        if role != "owner":
            await update.message.reply_text("❌ Только владелец может изменять роли сотрудников.")
            return
        employees = [
            item for item in get_all_employees()
            if item.get("user_id") is not None and item.get("status", "active") == "active"
        ]
        if not employees:
            await update.message.reply_text("Нет активных зарегистрированных сотрудников.")
            return
        employees = [
            item for item in employees
            if item.get("user_id") != config.OWNER_ID
        ]
        if not employees:
            await update.message.reply_text("Нет сотрудников, которым можно изменить роль.")
            return
        context.user_data["editable_employee_ids"] = [
            item["user_id"] for item in employees
        ]
        lines = ["Выберите сотрудника, введя номер из списка:"]
        for index, item in enumerate(employees, 1):
            name = " ".join(part for part in (
                item.get("last_name"), item.get("first_name")
            ) if part) or "Имя не указано"
            lines.append(f"{index}. {name} — {item.get('role', 'employee')}")
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=get_dialog_keyboard()
        )
        return EDIT_EMPLOYEE_SELECT

    elif text == "Отчет за неделю":
        if not await require_admin(update, context):
            return
        report = generate_weekly_report()
        await update.message.reply_text(
            report,
            reply_markup=get_main_keyboard(True, role == "owner")
        )

    elif text == "Отчет за месяц":
        if not await require_admin(update, context):
            return
        report = generate_monthly_report()
        await update.message.reply_text(
            report,
            reply_markup=get_main_keyboard(True, role == "owner")
        )

    elif text == "На главную" or text == BACK_BUTTON:
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_keyboard(is_admin_role(role), role == "owner")
        )


async def select_employee_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор активного сотрудника владельцем для изменения роли."""
    if get_user_role(update.effective_user.id) != "owner":
        await update.message.reply_text("❌ Недостаточно прав. Операция отменена.")
        context.user_data.pop("editable_employee_ids", None)
        return ConversationHandler.END

    employee_ids = context.user_data.get("editable_employee_ids", [])
    value = update.message.text.strip()
    if not value.isdigit() or not 1 <= int(value) <= len(employee_ids):
        await update.message.reply_text(
            "Введите номер сотрудника из списка или нажмите «Назад в меню»."
        )
        return EDIT_EMPLOYEE_SELECT

    employee_id = employee_ids[int(value) - 1]
    employee = get_employee_by_user_id(employee_id)
    if not employee or employee.get("status", "active") != "active":
        await update.message.reply_text("Сотрудник больше не доступен для изменения.")
        context.user_data.pop("editable_employee_ids", None)
        return ConversationHandler.END

    context.user_data["editing_employee_id"] = employee_id
    current_role = employee.get("role", "employee")
    if current_role == "admin":
        prompt = (
            "У сотрудника сейчас роль администратора.\n"
            "Введите 1, чтобы снять права администратора, или 2, чтобы оставить без изменений."
        )
    else:
        prompt = (
            "У сотрудника сейчас роль обычного сотрудника.\n"
            "Введите 1, чтобы назначить администратором, или 2, чтобы оставить без изменений."
        )
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return EDIT_EMPLOYEE_ROLE


async def set_selected_employee_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Назначает или снимает роль admin; доступна исключительно владельцу."""
    if get_user_role(update.effective_user.id) != "owner":
        await update.message.reply_text("❌ Только владелец может изменять роли сотрудников.")
        context.user_data.pop("editing_employee_id", None)
        context.user_data.pop("editable_employee_ids", None)
        return ConversationHandler.END

    choice = update.message.text.strip()
    if choice not in {"1", "2"}:
        await update.message.reply_text("Введите 1 или 2 либо нажмите «Назад в меню».")
        return EDIT_EMPLOYEE_ROLE
    if choice == "2":
        result = "Изменения не внесены."
    else:
        employee_id = context.user_data.get("editing_employee_id")
        employee = get_employee_by_user_id(employee_id) if employee_id is not None else None
        if not employee or employee.get("status", "active") != "active":
            result = "Сотрудник больше не активен. Роль не изменена."
        else:
            new_role = "employee" if employee.get("role", "employee") == "admin" else "admin"
            changed, reason = set_employee_role(employee_id, new_role)
            result = (
                "✅ Роль успешно изменена: " +
                ("администратор." if new_role == "admin" else "обычный сотрудник.")
                if changed else
                "❌ Не удалось изменить роль: " + reason
            )

    context.user_data.pop("editing_employee_id", None)
    context.user_data.pop("editable_employee_ids", None)
    await update.message.reply_text(
        result,
        reply_markup=get_main_keyboard(True, True)
    )
    return ConversationHandler.END


async def add_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первый шаг добавления сотрудника: номер телефона."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    phone = update.message.text.strip()
    if not re.fullmatch(r"\+?\d{7,15}", phone):
        await update.message.reply_text(
            "Некорректный номер. Введите номер в формате +79123456789:",
            reply_markup=get_dialog_keyboard()
        )
        return ADD_EMPLOYEE

    context.user_data["new_employee_phone"] = phone
    await update.message.reply_text(
        "Введите ФИО нового сотрудника:",
        reply_markup=get_dialog_keyboard()
    )
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
            reply_markup=get_main_keyboard(True, get_user_role(update.effective_user.id) == "owner")
        )
    else:
        await update.message.reply_text(
            "❌ Сотрудник с таким номером телефона уже существует.",
            reply_markup=get_main_keyboard(True, get_user_role(update.effective_user.id) == "owner")
        )

    context.user_data.pop("new_employee_phone", None)
    return ConversationHandler.END


async def schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первый шаг настройки графика: дата."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    context.user_data["schedule_date"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите ФИО сотрудника:",
        reply_markup=get_dialog_keyboard()
    )
    return GET_SCHEDULE_EMPLOYEE


async def schedule_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Второй шаг настройки графика: сотрудник."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    context.user_data["schedule_employee"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите время смены (например, 09:00-18:00):",
        reply_markup=get_dialog_keyboard()
    )
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
        reply_markup=get_main_keyboard(True, get_user_role(update.effective_user.id) == "owner")
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
    context.user_data.pop("editable_employee_ids", None)
    context.user_data.pop("editing_employee_id", None)

    await update.message.reply_text(
        "Текущая операция отменена. Выберите нужный пункт меню ещё раз.",
        reply_markup=get_main_keyboard(is_admin_role(role), role == "owner")
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
            ADD_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_phone)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_name)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel),
        ]
    )

    employee_edit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Редактировать сотрудника$"), button_handler)],
        states={
            EDIT_EMPLOYEE_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), select_employee_for_edit)
            ],
            EDIT_EMPLOYEE_ROLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), set_selected_employee_role)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel),
        ]
    )

    schedule_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Управление графиком$"), button_handler)],
        states={
            GET_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_date)],
            GET_SCHEDULE_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_employee)],
            GET_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_time)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel),
        ]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(add_employee_conv)
    application.add_handler(employee_edit_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()
