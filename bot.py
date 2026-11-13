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
ADD_EMPLOYEE, GET_NAME, GET_SCHEDULE_DATE, GET_SCHEDULE_EMPLOYEE, GET_SCHEDULE_TIME, EDIT_EMPLOYEE_SELECT, EDIT_EMPLOYEE_ROLE, MANAGE_EMPLOYEE_SELECT, MANAGE_EMPLOYEE_NAME, MANAGE_EMPLOYEE_CONFIRM, MANAGE_EMPLOYEE_PHONE = range(11)

MENU_BUTTONS = (
    "Начать смену", "Закончить смену", "График смен", "Мое время",
    "Редактировать", "Управление графиком",
    "Добавить сотрудника", "Список сотрудников", "Редактировать сотрудника",
    "Изменить данные", "Уволить сотрудника", "Удалить сотрудника",
    "Удалить ошибочные записи", "Назначить/снять администратора",
    "Отчет за неделю", "Отчет за месяц",
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


def audit_action(update, action, target_user_id=None, details=None):
    """Write a successful operation with the authenticated actor's identity and role."""
    user = update.effective_user
    if not user:
        return False
    return log_action(
        actor_user_id=user.id,
        actor_role=get_user_role(user.id),
        action=action,
        target_user_id=target_user_id,
        details=details
    )


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
            "Добро пожаловать! Поделитесь своим контактом или введите номер в формате +7XXXXXXXXXX.",
            reply_markup=get_phone_keyboard()
        )


async def complete_phone_identification(update: Update, phone_number: str):
    """Проверяет номер и привязывает сотрудника к Telegram-аккаунту."""
    user = update.effective_user
    employee = get_employee_by_phone(phone_number)

    if not employee:
        await update.message.reply_text(
            "Номер не найден. Проверьте его или обратитесь к владельцу.",
            reply_markup=get_phone_keyboard()
        )
        return

    if employee.get("status", "active") != "active" and user.id != config.OWNER_ID:
        await update.message.reply_text(
            "Доступ приостановлен. Обратитесь к владельцу.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    bound, reason, employee = bind_employee_to_user(phone_number, user.id)
    if not bound:
        messages = {
            "phone_already_bound": "Этот номер уже привязан к другому аккаунту. Обратитесь к владельцу.",
            "user_already_bound": "Ваш Telegram уже привязан к другой записи. Обратитесь к владельцу.",
            "not_found": "Номер не найден. Проверьте его или обратитесь к владельцу."
        }
        await update.message.reply_text(
            messages.get(reason, "Не удалось привязать номер. Обратитесь к владельцу."),
            reply_markup=ReplyKeyboardRemove()
        )
        return

    role = get_user_role(user.id)
    audit_action(
        update,
        "employee_linked",
        target_user_id=user.id,
        details={
            "employee_name": " ".join(
                part for part in (employee.get("last_name"), employee.get("first_name")) if part
            ),
            "phone_number": employee.get("phone_number")
        }
    )
    await update.message.reply_text(
        f"Добро пожаловать, {employee['first_name']}!",
        reply_markup=get_main_keyboard(is_admin_role(role), role == "owner")
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения собственного контакта сотрудника."""
    user = update.effective_user
    contact = update.message.contact

    if not contact or contact.user_id != user.id:
        await update.message.reply_text(
            "Отправьте свой контакт кнопкой «Поделиться контактом».",
            reply_markup=get_phone_keyboard()
        )
        return

    await complete_phone_identification(update, contact.phone_number)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню."""
    text = update.message.text
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)

    if role is None:
        phone = text.strip()
        if re.fullmatch(r"\+7\d{10}", phone):
            await complete_phone_identification(update, phone)
        else:
            await update.message.reply_text(
                "Поделитесь своим контактом или введите номер в формате +7XXXXXXXXXX.",
                reply_markup=get_phone_keyboard()
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
            active_shift = get_active_shift(user.id)
            audit_action(
                update,
                "shift_started",
                target_user_id=user.id,
                details={"shift_start": active_shift.get("shift_start") if active_shift else None}
            )
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
                if end_shift(user.id):
                    audit_action(
                        update,
                        "shift_ended",
                        target_user_id=user.id,
                        details={
                            "shift_start": active_shift.get("shift_start"),
                            "shift_end": now.strftime("%Y-%m-%d %H:%M:%S")
                        }
                    )
                    end_time = now.strftime("%H:%M")
                    await update.message.reply_text(f"✅ Смена завершена в {end_time}")
                else:
                    await update.message.reply_text("❌ Не удалось завершить смену. Попробуйте ещё раз.")

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

    elif text in {"Редактировать сотрудника", "Назначить/снять администратора"}:
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

    elif text == "Редактировать":
        if not await require_admin(update, context):
            return
        employees = get_all_employees()
        lines = ["Управление сотрудниками:"]
        for item in employees:
            if item.get("user_id") == config.OWNER_ID or (
                item.get("is_admin") is True and item.get("user_id") is None
            ):
                continue
            name = " ".join(part for part in (item.get("last_name"), item.get("first_name")) if part) or "Имя не указано"
            phone = item.get("phone_number") or "телефон не указан"
            if not isinstance(phone, str) or not re.fullmatch(r"\+?\d{7,15}", phone):
                lines.append(f"⚠️ Некорректная запись: {name} — {phone}")
                continue
            if item.get("status") == "dismissed":
                name = "☠️ " + name
            lines.append(f"{name} — {phone} — {item.get('status', 'не указан')}")
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=get_employee_management_keyboard(role == "owner")
        )

    elif text in {"Изменить данные", "Уволить сотрудника", "Удалить сотрудника", "Удалить ошибочные записи"}:
        if not await require_admin(update, context):
            return
        action = {
            "Изменить данные": "edit",
            "Уволить сотрудника": "dismiss",
            "Удалить сотрудника": "delete",
            "Удалить ошибочные записи": "delete_invalid"
        }[text]
        context.user_data["manage_employee_action"] = action
        if action == "delete_invalid":
            records = get_invalid_employee_records()
            if not records:
                await update.message.reply_text("Ошибочных записей нет.", reply_markup=get_employee_management_keyboard(role == "owner"))
                return ConversationHandler.END
            context.user_data["manage_employee_indexes"] = [x["index"] for x in records]
            context.user_data["manage_employee_invalid_records"] = records
            lines = ["Выберите ошибочную запись для удаления:"]
            lines.extend(f"{i}. {x['name']} — {x['phone']}" for i, x in enumerate(records, 1))
        else:
            employees = []
            for item in get_all_employees():
                phone = item.get("phone_number")
                if not isinstance(phone, str) or not re.fullmatch(r"\+?\d{7,15}", phone):
                    continue
                if item.get("user_id") == config.OWNER_ID or item.get("is_admin") is True:
                    continue
                if action in {"edit", "dismiss"} and item.get("status", "active") != "active":
                    continue
                employees.append(item)
            if not employees:
                await update.message.reply_text("Нет сотрудников, доступных для этой операции.", reply_markup=get_employee_management_keyboard(role == "owner"))
                return ConversationHandler.END
            context.user_data["manage_employee_phones"] = [x["phone_number"] for x in employees]
            lines = ["Выберите сотрудника, введя номер из списка:"]
            lines.extend(
                f"{i}. {' '.join(part for part in (x.get('last_name'), x.get('first_name')) if part) or 'Имя не указано'} — {x.get('phone_number')}"
                for i, x in enumerate(employees, 1)
            )
        await update.message.reply_text("\n".join(lines), reply_markup=get_dialog_keyboard())
        return MANAGE_EMPLOYEE_SELECT

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
        if not employee or employee.get("status", "active") != "active" or employee_id == config.OWNER_ID:
            result = "Сотрудник больше не доступен для изменения роли."
        else:
            old_role = employee.get("role", "employee")
            new_role = "employee" if old_role == "admin" else "admin"
            changed, reason = set_employee_role(employee_id, new_role)
            if changed:
                audit_action(
                    update,
                    "employee_role_changed",
                    target_user_id=employee_id,
                    details={"old_role": old_role, "new_role": new_role}
                )
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
        reply_markup=get_employee_management_keyboard(True)
    )
    return ConversationHandler.END


def clear_manage_employee_state(context):
    for key in ("manage_employee_action", "manage_employee_indexes", "manage_employee_invalid_records", "manage_employee_phones", "manage_employee_phone", "manage_employee_first_name", "manage_employee_last_name"):
        context.user_data.pop(key, None)


async def manage_employee_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    value = update.message.text.strip()
    if not value.isdigit():
        await update.message.reply_text("Введите номер из списка.")
        return MANAGE_EMPLOYEE_SELECT
    number = int(value)
    action = context.user_data.get("manage_employee_action")
    if action == "delete_invalid":
        indexes = context.user_data.get("manage_employee_indexes", [])
        if not 1 <= number <= len(indexes):
            await update.message.reply_text("Введите номер ошибочной записи из списка.")
            return MANAGE_EMPLOYEE_SELECT
        selected_index = indexes[number - 1]
        invalid_records = context.user_data.get("manage_employee_invalid_records", [])
        invalid_record = next(
            (item for item in invalid_records if item.get("index") == selected_index),
            {}
        )
        success, reason = delete_invalid_employee_record(selected_index)
        if success:
            audit_action(
                update,
                "invalid_employee_record_deleted",
                details={
                    "record_index": selected_index,
                    "employee_name": invalid_record.get("name"),
                    "phone_value": invalid_record.get("phone")
                }
            )
        clear_manage_employee_state(context)
        await update.message.reply_text(
            "✅ Ошибочная запись удалена." if success else "❌ Запись уже изменилась или не является ошибочной.",
            reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == "owner")
        )
        return ConversationHandler.END

    phones = context.user_data.get("manage_employee_phones", [])
    if not 1 <= number <= len(phones):
        await update.message.reply_text("Введите номер сотрудника из списка.")
        return MANAGE_EMPLOYEE_SELECT
    phone = phones[number - 1]
    employee = get_employee_by_phone(phone)
    if not employee or employee.get("user_id") == config.OWNER_ID or employee.get("is_admin") is True:
        clear_manage_employee_state(context)
        await update.message.reply_text("Сотрудник больше не доступен для изменения.")
        return ConversationHandler.END
    context.user_data["manage_employee_phone"] = phone

    if action == "edit":
        await update.message.reply_text(
            "Введите новое ФИО в формате «Фамилия Имя Отчество»:",
            reply_markup=get_dialog_keyboard()
        )
        return MANAGE_EMPLOYEE_NAME

    prompt = (
        "Уволить сотрудника? Запись и история смен сохранятся. Введите 1 для подтверждения или 2 для отмены."
        if action == "dismiss" else
        "Удалить запись сотрудника? Это возможно только при отсутствии истории смен. Введите 1 для подтверждения или 2 для отмены."
    )
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return MANAGE_EMPLOYEE_CONFIRM


async def manage_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    full_name = update.message.text.strip()
    if full_name in MENU_BUTTONS or len(full_name.split()) < 2:
        await update.message.reply_text("Введите фамилию и имя через пробел.")
        return MANAGE_EMPLOYEE_NAME
    parts = full_name.split()
    context.user_data["manage_employee_last_name"] = parts[0]
    context.user_data["manage_employee_first_name"] = " ".join(parts[1:])
    await update.message.reply_text(
        "Введите новый номер телефона в формате +7XXXXXXXXXX или отправьте «-», чтобы оставить прежний:"
    )
    return MANAGE_EMPLOYEE_PHONE


async def manage_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    phone = update.message.text.strip()
    if phone in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
    selected_employee = get_employee_by_phone(context.user_data.get("manage_employee_phone"))
    if not selected_employee or selected_employee.get("user_id") == config.OWNER_ID or selected_employee.get("is_admin") is True:
        clear_manage_employee_state(context)
        await update.message.reply_text("❌ Нельзя изменять запись владельца.")
        return ConversationHandler.END
    if phone != "-" and not re.fullmatch(r"\+?\d{7,15}", phone):
        await update.message.reply_text("Некорректный номер. Введите +7XXXXXXXXXX или «-».")
        return MANAGE_EMPLOYEE_PHONE
    old_name = " ".join(
        part for part in (selected_employee.get("last_name"), selected_employee.get("first_name")) if part
    )
    old_phone = selected_employee.get("phone_number")
    new_name = " ".join(
        part for part in (
            context.user_data.get("manage_employee_last_name"),
            context.user_data.get("manage_employee_first_name")
        ) if part
    )
    new_phone = old_phone if phone == "-" else phone
    success, reason = update_employee(
        context.user_data.get("manage_employee_phone"),
        first_name=context.user_data.get("manage_employee_first_name"),
        last_name=context.user_data.get("manage_employee_last_name"),
        new_phone=None if phone == "-" else phone
    )
    if success:
        audit_action(
            update,
            "employee_updated",
            target_user_id=selected_employee.get("user_id"),
            details={
                "old_name": old_name,
                "new_name": new_name,
                "old_phone": old_phone,
                "new_phone": new_phone
            }
        )
    messages = {
        "ok": "✅ Данные сотрудника обновлены.",
        "invalid_phone": "❌ Некорректный номер телефона.",
        "duplicate_phone": "❌ Такой номер уже используется.",
        "not_found": "❌ Сотрудник не найден."
    }
    clear_manage_employee_state(context)
    await update.message.reply_text(
        messages.get(reason, "❌ Не удалось обновить данные."),
        reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == "owner")
    )
    return ConversationHandler.END


async def manage_employee_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {"1", "2"}:
        await update.message.reply_text("Введите 1 для подтверждения или 2 для отмены.")
        return MANAGE_EMPLOYEE_CONFIRM
    action = context.user_data.get("manage_employee_action")
    phone = context.user_data.get("manage_employee_phone")
    selected_employee = get_employee_by_phone(phone) if phone else None
    if action in {"dismiss", "delete"} and (
        not selected_employee
        or selected_employee.get("user_id") == config.OWNER_ID
        or selected_employee.get("is_admin") is True
    ):
        clear_manage_employee_state(context)
        await update.message.reply_text(
            "❌ Нельзя изменять запись владельца.",
            reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == "owner")
        )
        return ConversationHandler.END
    if choice == "2":
        result = "Операция отменена."
    elif action == "dismiss":
        success, reason = dismiss_employee(phone, protected_user_id=config.OWNER_ID)
        if success:
            audit_action(
                update,
                "employee_dismissed",
                target_user_id=selected_employee.get("user_id") if selected_employee else None,
                details={
                    "employee_name": " ".join(
                        part for part in (selected_employee.get("last_name"), selected_employee.get("first_name")) if part
                    ) if selected_employee else None,
                    "phone_number": phone
                }
            )
        result = "☠️ Сотрудник уволен. Запись и история смен сохранены." if success else "❌ Не удалось уволить сотрудника."
    else:
        success, reason = delete_employee_without_history(phone, protected_user_id=config.OWNER_ID)
        if success:
            audit_action(
                update,
                "employee_deleted",
                target_user_id=selected_employee.get("user_id") if selected_employee else None,
                details={
                    "employee_name": " ".join(
                        part for part in (selected_employee.get("last_name"), selected_employee.get("first_name")) if part
                    ) if selected_employee else None,
                    "phone_number": phone
                }
            )
            result = "✅ Запись сотрудника удалена. Истории смен у него не было."
        elif reason == "has_history":
            result = "❌ У сотрудника есть история смен. Удаление запрещено — используйте увольнение."
        else:
            result = "❌ Не удалось удалить запись сотрудника."
    clear_manage_employee_state(context)
    await update.message.reply_text(
        result,
        reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == "owner")
    )
    return ConversationHandler.END


async def add_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первый шаг добавления сотрудника: номер телефона."""
    if not await require_admin(update, context):
        return ConversationHandler.END

    phone = update.message.text.strip()
    # Reject a menu label if a conversation filter unexpectedly routes it here.
    if phone in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
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
    # Reject a menu label instead of storing it as an employee name.
    if full_name in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
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
        audit_action(
            update,
            "employee_added",
            details={"employee_name": full_name, "phone_number": phone}
        )
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
    audit_action(
        update,
        "schedule_entry_added",
        details={
            "schedule_date": date,
            "employee_name": employee_name,
            "shift_time": shift_time
        }
    )
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
    clear_manage_employee_state(context)

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
        entry_points=[MessageHandler(filters.Regex(r"^(?:Редактировать сотрудника|Назначить/снять администратора)$"), button_handler)],
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

    employee_management_conv = ConversationHandler(
        entry_points=[MessageHandler(
            filters.Regex(r"^(?:Изменить данные|Уволить сотрудника|Удалить сотрудника|Удалить ошибочные записи)$"),
            button_handler
        )],
        states={
            MANAGE_EMPLOYEE_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_select)
            ],
            MANAGE_EMPLOYEE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_name)
            ],
            MANAGE_EMPLOYEE_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_phone)
            ],
            MANAGE_EMPLOYEE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_confirm)
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
    application.add_handler(employee_management_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()
