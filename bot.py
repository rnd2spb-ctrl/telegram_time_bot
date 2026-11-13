import logging
import calendar
import re
from datetime import date, datetime
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler)
import config
from database import *
from keyboards import *
from utils import *
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
ADD_EMPLOYEE, GET_NAME, GET_SCHEDULE_DATE, GET_SCHEDULE_EMPLOYEE, GET_SCHEDULE_TIME, EDIT_EMPLOYEE_SELECT, EDIT_EMPLOYEE_ROLE, MANAGE_EMPLOYEE_SELECT, MANAGE_EMPLOYEE_NAME, MANAGE_EMPLOYEE_CONFIRM, MANAGE_EMPLOYEE_PHONE = range(11)
SCHEDULE_MENU, SCHEDULE_SELECT_ENTRY, SCHEDULE_END_TIME, SCHEDULE_DELETE_CONFIRM = range(11, 15)
SCHEDULE_DELETE_INVALID_SELECT, SCHEDULE_DELETE_INVALID_CONFIRM = range(15, 17)

def clear_schedule_state(context):
    for key in ('schedule_action', 'schedule_date', 'schedule_dates', 'schedule_employee', 'schedule_employee_options', 'schedule_start_time', 'schedule_entry_ids', 'schedule_selected_id', 'schedule_invalid_indices', 'schedule_invalid_records', 'schedule_invalid_selected_indices', 'schedule_invalid_selected_records'):
        context.user_data.pop(key, None)

MENU_BUTTONS = ('Начать смену', 'Закончить смену', 'График смен', 'Мое время', 'Редактировать', 'Управление графиком', 'Добавить смену', 'Изменить смену', 'Удалить смену', 'Удалить ошибочные смены', 'Добавить сотрудника', 'Список сотрудников', 'Редактировать сотрудника', 'Изменить данные', 'Уволить сотрудника', 'Удалить сотрудника', 'Удалить ошибочные записи', 'Назначить/снять администратора', 'Отчет за неделю', 'Отчет за месяц', 'На главную', '⬅️ Назад в меню')
MENU_BUTTON_PATTERN = '^(?:' + '|'.join((re.escape(label) for label in MENU_BUTTONS)) + ')$'


def employee_prompt_label(employee):
    """Return surname, optional admin marker, and the prompt arrow."""
    if not isinstance(employee, dict):
        return '\u0421\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a >'
    if (config.OWNER_ID is not None and employee.get('user_id') == config.OWNER_ID) or employee.get('role') == 'owner':
        return '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
    surname = str(employee.get('last_name') or '').strip()
    if not surname:
        full_name = ' '.join(str(employee.get(key) or '').strip() for key in ('first_name', 'name')).strip()
        surname = full_name.split()[0] if full_name else '\u0411\u0435\u0437 \u0444\u0430\u043c\u0438\u043b\u0438\u0438'
    role = employee.get('role')
    if role is None:
        role = 'admin' if employee.get('is_admin') is True else 'employee'
    suffix = ' (\u0430\u0434\u043c\u0438\u043d)' if role == 'admin' else ''
    return f'{surname}{suffix} >'

def schedule_entry_prompt_label(entry):
    """Return the surname and role for a schedule row."""
    if entry.get('user_id') == config.OWNER_ID or entry.get('employee_id') == f'owner:{config.OWNER_ID}':
        return '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
    employee = get_employee_by_user_id(entry.get('user_id')) if entry.get('user_id') is not None else None
    if not employee and entry.get('employee_phone'):
        employee = get_employee_by_phone(entry.get('employee_phone'))
    if employee:
        return employee_prompt_label(employee)
    name = str(entry.get('employee_name') or '\u0411\u0435\u0437 \u0444\u0430\u043c\u0438\u043b\u0438\u0438').strip()
    return employee_prompt_label({'last_name': name.split()[0], 'role': 'employee'})


def parse_number_selection(value, max_number):
    """Parse comma-separated numbers and inclusive ranges, e.g. 1,3,5 or 1-4."""
    if not isinstance(value, str) or not value.strip() or not isinstance(max_number, int) or max_number < 1:
        return None
    selected = set()
    for raw_part in value.replace(';', ',').split(','):
        part = raw_part.strip()
        if not part:
            return None
        match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part)
        if match:
            first, last = map(int, match.groups())
            if first > last or first < 1 or last > max_number:
                return None
            selected.update(range(first, last + 1))
        elif part.isdigit():
            number = int(part)
            if not 1 <= number <= max_number:
                return None
            selected.add(number)
        else:
            return None
    return sorted(selected) if selected else None


def parse_schedule_days(value, reference_date=None):
    """Convert day numbers to ISO dates in the reference month."""
    reference_date = reference_date or date.today()
    days_in_month = calendar.monthrange(reference_date.year, reference_date.month)[1]
    days = parse_number_selection(value, days_in_month)
    if days is None:
        return None
    return [date(reference_date.year, reference_date.month, day).isoformat() for day in days]


def current_user_prompt_label(user, employee=None, role=None):
    """Return a compact current-user label including their role."""
    role = role if role is not None else get_user_role(user.id)
    if role == 'owner':
        return '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
    employee = employee or get_employee_by_user_id(user.id)
    if employee:
        return employee_prompt_label(employee)
    return f'{getattr(user, "first_name", None) or "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c"} >'

def get_user_role(user_id):
    '''Возвращает owner/admin/employee либо None, если доступ запрещен.'''

    if config.OWNER_ID is not None and user_id == config.OWNER_ID:
        return 'owner'
    employee = get_employee_by_user_id(user_id)
    if employee:
        if employee.get('status', 'active') != 'active':
            return
        role = employee.get('role', 'employee')
        if role in {'admin', 'employee'}:
            return role

def is_admin_role(role):
    return role in {'admin', 'owner'}

async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Повторно проверяет права на каждом шаге административного диалога.'''

    user = update.effective_user
    role = get_user_role(user.id) if user else None
    if not is_admin_role(role):
        if update.message:
            await update.message.reply_text('❌ Недостаточно прав. Административное действие отменено.')
        context.user_data.pop('new_employee_phone', None)
        clear_schedule_state(context)
        return False
    return True

def audit_action(update, action, target_user_id=None, details=None):
    """Write a successful operation with the authenticated actor's identity and role."""

    user = update.effective_user
    if not user:
        return False
    return log_action(actor_user_id=user.id, actor_role=get_user_role(user.id), action=action, target_user_id=target_user_id, details=details)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start."""
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)
    if is_admin_role(role):
        label = current_user_prompt_label(user, employee, role)
        await update.message.reply_text(
            f'\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435, {label} \u0414\u043e\u0441\u0442\u0443\u043f \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d.',
            reply_markup=get_main_keyboard(True, role == 'owner')
        )
        return
    if employee and role == 'employee':
        await update.message.reply_text(
            f'\u0421 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d\u0438\u0435\u043c, {current_user_prompt_label(user, employee, role)}',
            reply_markup=get_main_keyboard(False)
        )
        return
    if employee:
        await update.message.reply_text(
            '\u274c \u0412\u0430\u0448 \u0434\u043e\u0441\u0442\u0443\u043f \u043f\u0440\u0438\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d. \u041e\u0431\u0440\u0430\u0442\u0438\u0442\u0435\u0441\u044c \u043a \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443 \u0431\u043e\u0442\u0430.',
            reply_markup=ReplyKeyboardRemove()
        )
        return
    await update.message.reply_text(
        '\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c! \u041f\u043e\u0434\u0435\u043b\u0438\u0442\u0435\u0441\u044c \u0441\u0432\u043e\u0438\u043c \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u043e\u043c \u0438\u043b\u0438 \u0432\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435 +7XXXXXXXXXX.',
        reply_markup=get_phone_keyboard()
    )

async def complete_phone_identification(update: Update, phone_number: str):
    '''Проверяет номер и привязывает сотрудника к Telegram-аккаунту.'''

    user = update.effective_user
    employee = get_employee_by_phone(phone_number)
    if not employee:
        await update.message.reply_text('Номер не найден. Проверьте его или обратитесь к владельцу.', reply_markup=get_phone_keyboard())
        return
    if employee.get('status', 'active') != 'active' and user.id != config.OWNER_ID:
        await update.message.reply_text('Доступ приостановлен. Обратитесь к владельцу.', reply_markup=ReplyKeyboardRemove())
        return
    bound, reason, employee = bind_employee_to_user(phone_number, user.id)
    if not bound:
        messages = {'phone_already_bound': 'Этот номер уже привязан к другому аккаунту. Обратитесь к владельцу.', 'user_already_bound': 'Ваш Telegram уже привязан к другой записи. Обратитесь к владельцу.', 'not_found': 'Номер не найден. Проверьте его или обратитесь к владельцу.'}
        await update.message.reply_text(messages.get(reason, 'Не удалось привязать номер. Обратитесь к владельцу.'), reply_markup=ReplyKeyboardRemove())
        return
    role = get_user_role(user.id)
    audit_action(update, 'employee_linked', target_user_id=user.id, details={'employee_name': ' '.join((part for part in (employee.get('last_name'), employee.get('first_name')) if part)), 'phone_number': employee.get('phone_number')})
    await update.message.reply_text(f'\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c, {current_user_prompt_label(user, employee, role)}!', reply_markup=get_main_keyboard(is_admin_role(role), role == 'owner'))

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Telegram ID, full name, and current role."""
    user = update.effective_user
    if not user:
        await update.message.reply_text('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f Telegram.')
        return
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)
    role_names = {'owner': '\u0432\u043b\u0430\u0434\u0435\u043b\u0435\u0446', 'admin': '\u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440', 'employee': '\u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a'}
    first_name = employee.get('first_name') if employee else user.first_name
    last_name = employee.get('last_name') if employee else user.last_name
    full_name = ' '.join(part for part in (last_name, first_name) if part) or '\u0438\u043c\u044f \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e'
    role_name = role_names.get(role, '\u043d\u0435 \u0437\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d / \u0434\u043e\u0441\u0442\u0443\u043f \u043e\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u0435\u0442')
    label = current_user_prompt_label(user, employee, role)
    await update.message.reply_text(
        f'\u0412\u0430\u0448 \u043f\u0440\u043e\u0444\u0438\u043b\u044c: {label}\n\u0424\u0418\u041e: {full_name}\nTelegram ID: {user.id}\n\u0420\u043e\u043b\u044c: {role_name}'
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Обработчик получения собственного контакта сотрудника.'''

    user = update.effective_user
    contact = update.message.contact
    if not contact or contact.user_id != user.id:
        await update.message.reply_text('Отправьте свой контакт кнопкой «Поделиться контактом».', reply_markup=get_phone_keyboard())
        return
    await complete_phone_identification(update, contact.phone_number)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Обработчик нажатий на кнопки главного меню.'''

    text = update.message.text
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)
    if role is None:
        phone = text.strip()
        if re.fullmatch('\\+7\\d{10}', phone):
            await complete_phone_identification(update, phone)
            return
        await update.message.reply_text('Поделитесь своим контактом или введите номер в формате +7XXXXXXXXXX.', reply_markup=get_phone_keyboard())
        return
    if text == 'Начать смену':
        if not employee:
            await update.message.reply_text('Для учета рабочего времени сначала привяжите свой номер телефона через /start.')
            return
        active_shift = get_active_shift(user.id)
        if active_shift:
            await update.message.reply_text('У вас уже есть активная смена.')
            return
        start_shift(user.id)
        active_shift = get_active_shift(user.id)
        audit_action(update, 'shift_started', target_user_id=user.id, details={'shift_start': active_shift.get('shift_start') if active_shift else None})
        start_time = datetime.now().strftime('%H:%M')
        await update.message.reply_text(f'✅ Смена начата в {start_time}')
        return
    if text == 'Закончить смену':
        if not employee:
            await update.message.reply_text('Для учета рабочего времени сначала привяжите свой номер телефона через /start.')
            return
        active_shift = get_active_shift(user.id)
        if not active_shift:
            await update.message.reply_text('У вас нет активной смены.')
            return
        start_time = datetime.strptime(active_shift['shift_start'], '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        if (now - start_time).total_seconds() < 3600:
            await update.message.reply_text('❌ Нельзя закрыть смену раньше, чем через 1 час после начала.')
            return
        if end_shift(user.id):
            audit_action(update, 'shift_ended', target_user_id=user.id, details={'shift_start': active_shift.get('shift_start'), 'shift_end': now.strftime('%Y-%m-%d %H:%M:%S')})
            end_time = now.strftime('%H:%M')
            await update.message.reply_text(f'✅ Смена завершена в {end_time}')
            return
        await update.message.reply_text('❌ Не удалось завершить смену. Попробуйте ещё раз.')
        return
    if text == 'График смен':
        schedule_message = format_schedule_message()
        await update.message.reply_text(schedule_message)
        return
    if text == 'Мое время':
        if not employee:
            await update.message.reply_text('Для просмотра личного времени сначала привяжите свой номер телефона через /start.')
            return
        shifts = get_user_shifts(user.id, 7)
        total_time = calculate_work_time(shifts)
        await update.message.reply_text(f'⏱ Ваше время за последнюю неделю: {total_time}')
        return
    if text == 'Добавить сотрудника':
        if not await require_admin(update, context):
            return
        await update.message.reply_text('Введите номер телефона нового сотрудника в формате +79123456789:', reply_markup=get_dialog_keyboard())
        return ADD_EMPLOYEE
    if text == 'Управление графиком':
        if not await require_admin(update, context):
            return
        clear_schedule_state(context)
        await update.message.reply_text('Управление расписанием. Выберите действие:', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    if text == 'Список сотрудников':
        if not await require_admin(update, context):
            return
        employees = get_all_employees()
        if not employees:
            message = 'Список сотрудников пока пуст.'
        else:
            lines = ['👥 Список сотрудников:']
            for item in employees:
                full_name = ' '.join((part for part in (item.get('last_name'), item.get('first_name')) if part)) or 'Имя не указано'
                phone = item.get('phone_number') or 'телефон не указан'
                status = item.get('status', 'не указан')
                if not (isinstance(phone, str) and re.fullmatch('\\+?\\d{7,15}', phone)):
                    lines.append(f'⚠️ Некорректная запись: {full_name} — {phone}')
                    continue
                lines.append(f'\u2022 {employee_prompt_label(item)} \u2014 {phone} \u2014 {status}')
            message = '\n'.join(lines)
        await update.message.reply_text(message, reply_markup=get_main_keyboard(True, role == 'owner'))
        return
    if text in {'Редактировать сотрудника', 'Назначить/снять администратора'}:
        if role != 'owner':
            await update.message.reply_text('❌ Только владелец может изменять роли сотрудников.')
            return
        employees = [item for item in get_all_employees() if item.get('user_id') is not None if item.get('status', 'active') == 'active']
        if not employees:
            await update.message.reply_text('Нет активных зарегистрированных сотрудников.')
            return
        employees = [item for item in employees if item.get('user_id') != config.OWNER_ID]
        if not employees:
            await update.message.reply_text('Нет сотрудников, которым можно изменить роль.')
            return
        context.user_data['editable_employee_ids'] = [item['user_id'] for item in employees]
        lines = ['Выберите сотрудника, введя номер из списка:']
        for index, item in enumerate(employees, 1):
            name = ' '.join((part for part in (item.get('last_name'), item.get('first_name')) if part)) or 'Имя не указано'
            lines.append(f'{index}. {employee_prompt_label(item)}')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return EDIT_EMPLOYEE_SELECT
    if text == 'Редактировать':
        if not await require_admin(update, context):
            return
        employees = get_all_employees()
        lines = ['Управление сотрудниками:']
        for item in employees:
            if item.get('user_id') == config.OWNER_ID:
                continue
            name = ' '.join((part for part in (item.get('last_name'), item.get('first_name')) if part)) or 'Имя не указано'
            phone = item.get('phone_number') or 'телефон не указан'
            if not (isinstance(phone, str) and re.fullmatch('\\+?\\d{7,15}', phone)):
                lines.append(f'⚠️ Некорректная запись: {name} — {phone}')
                continue
            status_marker = '☠️ ' if item.get('status') == 'dismissed' else ''
            lines.append(f'{status_marker}{employee_prompt_label(item)} — {phone} — {item.get('status', '\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d')}')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_employee_management_keyboard(role == 'owner'))
        return
    if text in {'Изменить данные', 'Уволить сотрудника', 'Удалить сотрудника', 'Удалить ошибочные записи'}:
        if not await require_admin(update, context):
            return
        action = {'Изменить данные': 'edit', 'Уволить сотрудника': 'dismiss', 'Удалить сотрудника': 'delete', 'Удалить ошибочные записи': 'delete_invalid'}[text]
        context.user_data['manage_employee_action'] = action
        if action == 'delete_invalid':
            records = get_invalid_employee_records()
            if not records:
                await update.message.reply_text('Ошибочных записей нет.', reply_markup=get_employee_management_keyboard(role == 'owner'))
                return ConversationHandler.END
            context.user_data['manage_employee_indexes'] = [x['index'] for x in records]
            context.user_data['manage_employee_invalid_records'] = records
            lines = ['Выберите ошибочную запись для удаления:']
            lines.extend((f'{i}. {x['name']} — {x['phone']}' for i, x in enumerate(records, 1)))
        else:
            employees = []
            for item in get_all_employees():
                phone = item.get('phone_number')
                if not (isinstance(phone, str) and re.fullmatch('\\+?\\d{7,15}', phone)):
                    continue
                if item.get('user_id') == config.OWNER_ID:
                    continue
                if action in {'edit', 'dismiss'}:
                    if item.get('status', 'active') != 'active':
                        continue
                employees.append(item)
            if not employees:
                await update.message.reply_text('Нет сотрудников, доступных для этой операции.', reply_markup=get_employee_management_keyboard(role == 'owner'))
                return ConversationHandler.END
            context.user_data['manage_employee_phones'] = [x['phone_number'] for x in employees]
            lines = ['Выберите сотрудника, введя номер из списка:']
            lines.extend((f'{i}. {employee_prompt_label(x)} — {x.get('phone_number')}' for i, x in enumerate(employees, 1)))
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return MANAGE_EMPLOYEE_SELECT
    if text == 'Отчет за неделю':
        if not await require_admin(update, context):
            return
        report = generate_weekly_report()
        await update.message.reply_text(report, reply_markup=get_main_keyboard(True, role == 'owner'))
        return
    if text == 'Отчет за месяц':
        if not await require_admin(update, context):
            return
        report = generate_monthly_report()
        await update.message.reply_text(report, reply_markup=get_main_keyboard(True, role == 'owner'))
        return
    if text == 'На главную' or text == BACK_BUTTON:
        await update.message.reply_text('Главное меню:', reply_markup=get_main_keyboard(is_admin_role(role), role == 'owner'))

async def select_employee_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Выбор активного сотрудника владельцем для изменения роли.'''

    if get_user_role(update.effective_user.id) != 'owner':
        await update.message.reply_text('❌ Недостаточно прав. Операция отменена.')
        context.user_data.pop('editable_employee_ids', None)
        return ConversationHandler.END
    employee_ids = context.user_data.get('editable_employee_ids', [])
    value = update.message.text.strip()
    if not value.isdigit() or not 1 <= int(value) <= len(employee_ids):
        await update.message.reply_text('Введите номер сотрудника из списка или нажмите «Назад в меню».')
        return EDIT_EMPLOYEE_SELECT
    employee_id = employee_ids[int(value) - 1]
    employee = get_employee_by_user_id(employee_id)
    if not employee or employee.get('status', 'active') != 'active':
        await update.message.reply_text('Сотрудник больше не доступен для изменения.')
        context.user_data.pop('editable_employee_ids', None)
        return ConversationHandler.END
    context.user_data['editing_employee_id'] = employee_id
    current_role = employee.get('role', 'employee')
    if current_role == 'admin':
        prompt = 'У сотрудника сейчас роль администратора.\nВведите 1, чтобы снять права администратора, или 2, чтобы оставить без изменений.'
    else:
        prompt = 'У сотрудника сейчас роль обычного сотрудника.\nВведите 1, чтобы назначить администратором, или 2, чтобы оставить без изменений.'
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return EDIT_EMPLOYEE_ROLE

async def set_selected_employee_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_role(update.effective_user.id) != 'owner':
        await update.message.reply_text('❌ Только владелец может изменять роли сотрудников.')
        context.user_data.pop('editing_employee_id', None)
        context.user_data.pop('editable_employee_ids', None)
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {'1', '2'}:
        await update.message.reply_text('Введите 1 или 2 либо нажмите «Назад в меню».')
        return EDIT_EMPLOYEE_ROLE
    if choice == '2':
        result = 'Изменения не внесены.'
    else:
        employee_id = context.user_data.get('editing_employee_id')
        employee = get_employee_by_user_id(employee_id) if employee_id is not None else None
        if not employee or employee.get('status', 'active') != 'active' or employee_id == config.OWNER_ID:
            result = 'Сотрудник больше не доступен для изменения роли.'
        else:
            old_role = employee.get('role', 'employee')
            new_role = 'employee' if old_role == 'admin' else 'admin'
            changed, reason = set_employee_role(employee_id, new_role)
            if changed:
                audit_action(update, 'employee_role_changed', target_user_id=employee_id, details={'old_role': old_role, 'new_role': new_role})
            result = '✅ Роль успешно изменена: ' + ('администратор.' if new_role == 'admin' else 'обычный сотрудник.') if changed else '❌ Не удалось изменить роль: ' + reason
    context.user_data.pop('editing_employee_id', None)
    context.user_data.pop('editable_employee_ids', None)
    await update.message.reply_text(result, reply_markup=get_employee_management_keyboard(True))
    return ConversationHandler.END

def clear_manage_employee_state(context):
    for key in ('manage_employee_action', 'manage_employee_indexes', 'manage_employee_invalid_records', 'manage_employee_phones', 'manage_employee_phone', 'manage_employee_first_name', 'manage_employee_last_name'):
        context.user_data.pop(key, None)

async def manage_employee_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    value = update.message.text.strip()
    if not value.isdigit():
        await update.message.reply_text('Введите номер из списка.')
        return MANAGE_EMPLOYEE_SELECT
    number = int(value)
    action = context.user_data.get('manage_employee_action')
    if action == 'delete_invalid':
        indexes = context.user_data.get('manage_employee_indexes', [])
        if not 1 <= number <= len(indexes):
            await update.message.reply_text('Введите номер ошибочной записи из списка.')
            return MANAGE_EMPLOYEE_SELECT
        selected_index = indexes[number - 1]
        invalid_records = context.user_data.get('manage_employee_invalid_records', [])
        invalid_record = next((item for item in invalid_records if item.get('index') == selected_index), {})
        success, reason = delete_invalid_employee_record(selected_index)
        if success:
            audit_action(update, 'invalid_employee_record_deleted', details={'record_index': selected_index, 'employee_name': invalid_record.get('name'), 'phone_value': invalid_record.get('phone')})
        clear_manage_employee_state(context)
        await update.message.reply_text('✅ Ошибочная запись удалена.' if success else '❌ Запись уже изменилась или не является ошибочной.', reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner'))
        return ConversationHandler.END
    phones = context.user_data.get('manage_employee_phones', [])
    if not 1 <= number <= len(phones):
        await update.message.reply_text('Введите номер сотрудника из списка.')
        return MANAGE_EMPLOYEE_SELECT
    phone = phones[number - 1]
    employee = get_employee_by_phone(phone)
    if not employee or employee.get('user_id') == config.OWNER_ID:
        clear_manage_employee_state(context)
        await update.message.reply_text('Сотрудник больше не доступен для изменения.')
        return ConversationHandler.END
    context.user_data['manage_employee_phone'] = phone
    if action == 'edit':
        await update.message.reply_text('Введите новое ФИО в формате «Фамилия Имя Отчество»:', reply_markup=get_dialog_keyboard())
        return MANAGE_EMPLOYEE_NAME
    prompt = 'Уволить сотрудника? Запись и история смен сохранятся. Введите 1 для подтверждения или 2 для отмены.' if action == 'dismiss' else 'Удалить запись сотрудника? Это возможно только при отсутствии истории смен. Введите 1 для подтверждения или 2 для отмены.'
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return MANAGE_EMPLOYEE_CONFIRM

async def manage_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    full_name = update.message.text.strip()
    if full_name in MENU_BUTTONS or len(full_name.split()) < 2:
        await update.message.reply_text('Введите фамилию и имя через пробел.')
        return MANAGE_EMPLOYEE_NAME
    parts = full_name.split()
    context.user_data['manage_employee_last_name'] = parts[0]
    context.user_data['manage_employee_first_name'] = ' '.join(parts[1:])
    await update.message.reply_text('Введите новый номер телефона в формате +7XXXXXXXXXX или отправьте «-», чтобы оставить прежний:')
    return MANAGE_EMPLOYEE_PHONE

async def manage_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    phone = update.message.text.strip()
    if phone in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
    selected_employee = get_employee_by_phone(context.user_data.get('manage_employee_phone'))
    if not selected_employee or selected_employee.get('user_id') == config.OWNER_ID:
        clear_manage_employee_state(context)
        await update.message.reply_text('❌ Нельзя изменять запись владельца.')
        return ConversationHandler.END
    if phone != '-':
        if not re.fullmatch('\\+?\\d{7,15}', phone):
            await update.message.reply_text('Некорректный номер. Введите +7XXXXXXXXXX или «-».')
            return MANAGE_EMPLOYEE_PHONE
    old_name = ' '.join((part for part in (selected_employee.get('last_name'), selected_employee.get('first_name')) if part))
    old_phone = selected_employee.get('phone_number')
    new_name = ' '.join((part for part in (context.user_data.get('manage_employee_last_name'), context.user_data.get('manage_employee_first_name')) if part))
    new_phone = old_phone if phone == '-' else phone
    success, reason = update_employee(context.user_data.get('manage_employee_phone'), first_name=context.user_data.get('manage_employee_first_name'), last_name=context.user_data.get('manage_employee_last_name'), new_phone=None if phone == '-' else phone)
    if success:
        audit_action(update, 'employee_updated', target_user_id=selected_employee.get('user_id'), details={'old_name': old_name, 'new_name': new_name, 'old_phone': old_phone, 'new_phone': new_phone})
    messages = {'ok': '✅ Данные сотрудника обновлены.', 'invalid_phone': '❌ Некорректный номер телефона.', 'duplicate_phone': '❌ Такой номер уже используется.', 'not_found': '❌ Сотрудник не найден.'}
    clear_manage_employee_state(context)
    await update.message.reply_text(messages.get(reason, '❌ Не удалось обновить данные.'), reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner'))
    return ConversationHandler.END

async def manage_employee_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_manage_employee_state(context)
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {'1', '2'}:
        await update.message.reply_text('Введите 1 для подтверждения или 2 для отмены.')
        return MANAGE_EMPLOYEE_CONFIRM
    action = context.user_data.get('manage_employee_action')
    phone = context.user_data.get('manage_employee_phone')
    selected_employee = get_employee_by_phone(phone) if phone else None
    if action in {'delete', 'dismiss'} and (not selected_employee or selected_employee.get('user_id') == config.OWNER_ID):
        clear_manage_employee_state(context)
        await update.message.reply_text('❌ Нельзя изменять запись владельца.', reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner'))
        return ConversationHandler.END
    if choice == '2':
        result = 'Операция отменена.'
    elif action == 'dismiss':
        success, reason = dismiss_employee(phone, protected_user_id=config.OWNER_ID)
        if success:
            audit_action(update, 'employee_dismissed', target_user_id=selected_employee.get('user_id') if selected_employee else None, details={'employee_name': ' '.join((part for part in (selected_employee.get('last_name'), selected_employee.get('first_name')) if part)) if selected_employee else None, 'phone_number': phone})
        result = '☠️ Сотрудник уволен. Запись и история смен сохранены.' if success else '❌ Не удалось уволить сотрудника.'
    else:
        success, reason = delete_employee_without_history(phone, protected_user_id=config.OWNER_ID)
        if success:
            audit_action(update, 'employee_deleted', target_user_id=selected_employee.get('user_id') if selected_employee else None, details={'employee_name': ' '.join((part for part in (selected_employee.get('last_name'), selected_employee.get('first_name')) if part)) if selected_employee else None, 'phone_number': phone})
            result = '✅ Запись сотрудника удалена. Истории смен у него не было.'
        elif reason == 'has_history':
            result = '❌ У сотрудника есть история смен. Удаление запрещено — используйте увольнение.'
        elif reason == 'has_schedule':
            result = '❌ У сотрудника есть назначенные смены в расписании. Сначала измените или удалите их.'
        elif reason == 'schedule_data_invalid':
            result = '❌ Файл расписания повреждён или содержит записи старого формата. Удаление сотрудника отменено.'
        else:
            result = '❌ Не удалось удалить запись сотрудника.'
    clear_manage_employee_state(context)
    await update.message.reply_text(result, reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner'))
    return ConversationHandler.END

async def add_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Первый шаг добавления сотрудника: номер телефона.'''

    if not await require_admin(update, context):
        return ConversationHandler.END
    phone = update.message.text.strip()
    if phone in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
    if not re.fullmatch('\\+?\\d{7,15}', phone):
        await update.message.reply_text('Некорректный номер. Введите номер в формате +79123456789:', reply_markup=get_dialog_keyboard())
        return ADD_EMPLOYEE
    context.user_data['new_employee_phone'] = phone
    await update.message.reply_text('Введите ФИО нового сотрудника:', reply_markup=get_dialog_keyboard())
    return GET_NAME

async def add_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Второй шаг добавления сотрудника: ФИО.'''

    if not await require_admin(update, context):
        return ConversationHandler.END
    phone = context.user_data.get('new_employee_phone')
    full_name = update.message.text.strip()
    if full_name in MENU_BUTTONS:
        await cancel(update, context)
        return ConversationHandler.END
    if not phone:
        await update.message.reply_text('Сценарий добавления сотрудника устарел. Начните заново.')
        return ConversationHandler.END
    if not full_name:
        await update.message.reply_text('ФИО не может быть пустым. Введите ФИО еще раз:')
        return GET_NAME
    name_parts = full_name.split()
    last_name = name_parts[0]
    first_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]
    if add_employee(None, phone, first_name, last_name):
        audit_action(update, 'employee_added', details={'employee_name': full_name, 'phone_number': phone})
        await update.message.reply_text(f'✅ Сотрудник {full_name} успешно добавлен!', reply_markup=get_main_keyboard(True, get_user_role(update.effective_user.id) == 'owner'))
    else:
        await update.message.reply_text('❌ Сотрудник с таким номером телефона уже существует.', reply_markup=get_main_keyboard(True, get_user_role(update.effective_user.id) == 'owner'))
    context.user_data.pop('new_employee_phone', None)
    return ConversationHandler.END

def _schedule_employee_options(role):
    '''Возвращает активных сотрудников и, только владельцу, самого владельца.'''

    options = []
    seen = set()
    for employee in get_all_employees():
        phone = employee.get('phone_number')
        if not (isinstance(phone, str) and re.fullmatch('\\+?\\d{7,15}', phone)):
            continue
        if employee.get('status', 'active') != 'active':
            continue
        if employee.get('user_id') == config.OWNER_ID:
            continue
        normalized_phone = normalize_phone(phone)
        employee_id = f'phone:{normalized_phone}'
        if employee_id in seen:
            continue
        seen.add(employee_id)
        name = ' '.join((part for part in (employee.get('last_name'), employee.get('first_name')) if part)) or 'Имя не указано'
        options.append({'employee_id': employee_id, 'user_id': employee.get('user_id'), 'employee_name': name, 'employee_phone': phone, 'role': employee.get('role', 'admin' if employee.get('is_admin') is True else 'employee')})
        continue
    options.sort(key=(lambda item: item['employee_name'].casefold()))
    if role == 'owner' and config.OWNER_ID is not None:
        options.insert(0, {'employee_id': f'owner:{config.OWNER_ID}', 'user_id': config.OWNER_ID, 'employee_name': 'Владелец', 'employee_phone': None})
    return options

def _schedule_entry_label(entry):
    try:
        date_label = datetime.strptime(entry['date'], '%Y-%m-%d').strftime('%d.%m.%Y')
    except (KeyError, TypeError, ValueError):
        date_label = entry.get('date', '\u0434\u0430\u0442\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430')
    return f'{date_label} {chr(0x2014)} {schedule_entry_prompt_label(entry)} {chr(0x2014)} {entry.get("start_time", "?")}{chr(0x2013)}{entry.get("end_time", "?")}'

def _manageable_schedule_entries(role):
    entries = get_schedule()
    if role != 'owner':
        entries = [item for item in entries if item.get('user_id') != config.OWNER_ID if item.get('employee_id') != f'owner:{config.OWNER_ID}']
    return sorted(entries, key=(lambda item: (item.get('date', ''), item.get('start_time', ''), item.get('employee_name', ''))))

async def schedule_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Choose adding, editing, deleting, or cleaning schedule entries."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    clear_schedule_state(context)
    role = get_user_role(update.effective_user.id)
    action = update.message.text.strip()
    if action == '\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443':
        context.user_data['schedule_action'] = 'add'
        month = datetime.now()
        await update.message.reply_text(
            f'\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0434\u043d\u0438 \u0442\u0435\u043a\u0443\u0449\u0435\u0433\u043e \u043c\u0435\u0441\u044f\u0446\u0430 ({month:%m.%Y}): \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 13,15,17 \u0438\u043b\u0438 \u0434\u0438\u0430\u043f\u0430\u0437\u043e\u043d 13-15. \u041c\u043e\u0436\u043d\u043e \u043a\u043e\u043c\u0431\u0438\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c: 13-15,18,21-22.',
            reply_markup=get_dialog_keyboard()
        )
        return GET_SCHEDULE_DATE
    if action == '\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0441\u043c\u0435\u043d\u044b':
        records = get_invalid_schedule_records(actor_role=role, owner_user_id=config.OWNER_ID)
        if records is None:
            await update.message.reply_text(
                '\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0442\u044c \u0444\u0430\u0439\u043b \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f. \u041e\u043d \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d \u0438\u043b\u0438 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 JSON; \u0444\u0430\u0439\u043b \u043d\u0435 \u0438\u0437\u043c\u0435\u043d\u0451\u043d.',
                reply_markup=get_schedule_management_keyboard()
            )
            return SCHEDULE_MENU
        if not records:
            await update.message.reply_text('\u041e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0445 \u0441\u043c\u0435\u043d \u043d\u0435\u0442.', reply_markup=get_schedule_management_keyboard())
            return SCHEDULE_MENU
        context.user_data['schedule_invalid_indices'] = [item['index'] for item in records]
        context.user_data['schedule_invalid_records'] = records
        lines = ['\u041e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0437\u0430\u043f\u0438\u0441\u0438 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f:']
        lines.extend(
            f'{number}. {item["name"]} — {item["date"]} — {item["start_time"]}–{item["end_time"]}'
            for number, item in enumerate(records, 1)
        )
        lines.append('\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440\u0430 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f: 1,3,5 \u0438\u043b\u0438 \u0434\u0438\u0430\u043f\u0430\u0437\u043e\u043d 1-4. \u041c\u043e\u0436\u043d\u043e \u043a\u043e\u043c\u0431\u0438\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c.')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return SCHEDULE_DELETE_INVALID_SELECT
    if action in MENU_BUTTONS and action not in {
        '\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443', '\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443',
        '\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443', '\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0441\u043c\u0435\u043d\u044b'
    }:
        await cancel(update, context)
        return ConversationHandler.END
    if action not in {'\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443', '\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443'}:
        await update.message.reply_text('\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u043a\u043d\u043e\u043f\u043a\u043e\u0439 \u043c\u0435\u043d\u044e.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    context.user_data['schedule_action'] = 'edit' if action == '\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443' else 'delete'
    entries = _manageable_schedule_entries(role)
    if not entries:
        await update.message.reply_text('\u041d\u0435\u0442 \u0441\u043c\u0435\u043d, \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0445 \u0434\u043b\u044f \u044d\u0442\u043e\u0439 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    context.user_data['schedule_entry_ids'] = [item['id'] for item in entries]
    lines = ['\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043c\u0435\u043d\u0443, \u0432\u0432\u0435\u0434\u044f \u0435\u0451 \u043d\u043e\u043c\u0435\u0440:']
    lines.extend(
        f'{index}. {schedule_entry_prompt_label(item)} \u2014 {item.get("date")} \u2014 {item.get("start_time")}\u2013{item.get("end_time")}'
        for index, item in enumerate(entries, 1)
    )
    await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
    return SCHEDULE_SELECT_ENTRY

async def schedule_select_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    value = update.message.text.strip()
    entry_ids = context.user_data.get('schedule_entry_ids', [])
    if not value.isdigit() or not 1 <= int(value) <= len(entry_ids):
        await update.message.reply_text('Введите номер смены из списка.')
        return SCHEDULE_SELECT_ENTRY
    schedule_id = entry_ids[int(value) - 1]
    entry = get_schedule_entry(schedule_id)
    role = get_user_role(update.effective_user.id)
    if not entry:
        clear_schedule_state(context)
        await update.message.reply_text('Эта запись уже недоступна. Откройте управление расписанием заново.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    is_owner_shift = entry.get('user_id') == config.OWNER_ID or entry.get('employee_id') == f'owner:{config.OWNER_ID}'
    if role != 'owner' and is_owner_shift:
        await update.message.reply_text('❌ Администратор не может изменять смены владельца.')
        clear_schedule_state(context)
        await update.message.reply_text('Выберите другое действие:', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    context.user_data['schedule_selected_id'] = schedule_id
    if context.user_data.get('schedule_action') == 'delete':
        await update.message.reply_text(f'Удалить эту смену?\n{_schedule_entry_label(entry)}\n\nВведите 1 для подтверждения или 2 для отмены.', reply_markup=get_dialog_keyboard())
        return SCHEDULE_DELETE_CONFIRM
    context.user_data['schedule_action'] = 'edit'
    await update.message.reply_text(f'Изменение смены:\n{_schedule_entry_label(entry)}\n\nВведите новую дату в формате ДД.ММ.ГГГГ:', reply_markup=get_dialog_keyboard())
    return GET_SCHEDULE_DATE

async def schedule_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {'1', '2'}:
        await update.message.reply_text('Введите 1 для удаления или 2 для отмены.')
        return SCHEDULE_DELETE_CONFIRM
    if choice == '2':
        clear_schedule_state(context)
        await update.message.reply_text('Удаление отменено.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    schedule_id = context.user_data.get('schedule_selected_id')
    role = get_user_role(update.effective_user.id)
    success, reason, deleted = delete_schedule_entry(schedule_id, actor_user_id=update.effective_user.id, actor_role=role, owner_user_id=config.OWNER_ID)
    if success:
        audit_action(update, 'schedule_entry_deleted', target_user_id=deleted.get('user_id'), details={'schedule_id': deleted.get('id'), 'employee_id': deleted.get('employee_id'), 'employee_name': deleted.get('employee_name'), 'date': deleted.get('date'), 'start_time': deleted.get('start_time'), 'end_time': deleted.get('end_time')})
        message = '✅ Смена удалена из расписания.'
    elif reason == 'owner_protected':
        message = '❌ Администратор не может удалять смены владельца.'
    elif reason == 'invalid_schedule_data':
        message = '❌ Файл расписания повреждён или содержит записи старого формата. Старый формат не поддерживается; данные не перезаписаны.'
    else:
        message = '❌ Не удалось удалить смену. Возможно, запись уже была изменена.'
    clear_schedule_state(context)
    await update.message.reply_text(message, reply_markup=get_schedule_management_keyboard())
    return SCHEDULE_MENU

async def schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    date_text = update.message.text.strip()
    action = context.user_data.get('schedule_action')
    if action == 'add':
        dates = parse_schedule_days(date_text, datetime.now().date())
        if dates is None:
            month = datetime.now()
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            await update.message.reply_text(
                f'\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u0434\u043d\u0435\u0439. \u0414\u043b\u044f \u043c\u0435\u0441\u044f\u0446\u0430 {month:%m.%Y} \u0443\u043a\u0430\u0436\u0438\u0442\u0435 \u0447\u0438\u0441\u043b\u0430 \u043e\u0442 1 \u0434\u043e {days_in_month}: 13,15,17 \u0438\u043b\u0438 13-15.'
            )
            return GET_SCHEDULE_DATE
        context.user_data['schedule_dates'] = dates
        context.user_data['schedule_date'] = dates[0]
    else:
        try:
            parsed_date = datetime.strptime(date_text, '%d.%m.%Y').date()
            if parsed_date.strftime('%d.%m.%Y') != date_text:
                raise ValueError
        except ValueError:
            await update.message.reply_text('\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u0434\u0430\u0442\u0430. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u0444\u043e\u0440\u043c\u0430\u0442 \u0414\u0414.\u041c\u041c.\u0413\u0413\u0413\u0413, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 13.11.2026.')
            return GET_SCHEDULE_DATE
        context.user_data['schedule_date'] = parsed_date.isoformat()
        context.user_data['schedule_dates'] = [parsed_date.isoformat()]
    role = get_user_role(update.effective_user.id)
    options = _schedule_employee_options(role)
    if not options:
        clear_schedule_state(context)
        await update.message.reply_text('\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u043e\u0432 \u0441 \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u043c\u0438 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430\u043c\u0438 \u0434\u043b\u044f \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f \u0441\u043c\u0435\u043d.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    context.user_data['schedule_employee_options'] = options
    lines = ['\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u0430, \u0432\u0432\u0435\u0434\u044f \u043d\u043e\u043c\u0435\u0440:']
    for index, item in enumerate(options, 1):
        if item.get('role') == 'owner':
            label = '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
        else:
            surname = item.get('employee_name', '\u0411\u0435\u0437 \u0444\u0430\u043c\u0438\u043b\u0438\u0438').split()[0]
            suffix = ' (\u0430\u0434\u043c\u0438\u043d)' if item.get('role') == 'admin' else ''
            label = f'{surname}{suffix} >'
        phone_label = '' if item.get('employee_phone') is None else f' \u2014 {item["employee_phone"]}'
        lines.append(f'{index}. {label}{phone_label}')
    await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
    return GET_SCHEDULE_EMPLOYEE

async def schedule_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    value = update.message.text.strip()
    options = context.user_data.get('schedule_employee_options', [])
    if not value.isdigit() or not 1 <= int(value) <= len(options):
        await update.message.reply_text('Введите номер сотрудника из списка.')
        return GET_SCHEDULE_EMPLOYEE
    selected = dict(options[int(value) - 1])
    role = get_user_role(update.effective_user.id)
    if role != 'owner' and selected.get('user_id') == config.OWNER_ID:
        await update.message.reply_text('❌ Администратор не может назначать или изменять смены владельца.')
        return GET_SCHEDULE_EMPLOYEE
    context.user_data['schedule_employee'] = selected
    await update.message.reply_text('Введите время начала смены с точностью до часа, например 09:00:', reply_markup=get_dialog_keyboard())
    return GET_SCHEDULE_TIME

async def schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    start_time = update.message.text.strip()
    if schedule_time_to_minutes(start_time, allow_24=False) is None:
        await update.message.reply_text('Некорректное время начала. Допускаются только целые часы от 00:00 до 23:00.')
        return GET_SCHEDULE_TIME
    context.user_data['schedule_start_time'] = start_time
    await update.message.reply_text('Введите время окончания смены (например, 18:00 или 24:00):', reply_markup=get_dialog_keyboard())
    return SCHEDULE_END_TIME

async def schedule_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return ConversationHandler.END
    start_time = context.user_data.get('schedule_start_time')
    end_time = update.message.text.strip()
    if schedule_time_to_minutes(end_time, allow_24=True) is None:
        await update.message.reply_text('\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e\u0435 \u0432\u0440\u0435\u043c\u044f \u043e\u043a\u043e\u043d\u0447\u0430\u043d\u0438\u044f. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u0446\u0435\u043b\u044b\u0439 \u0447\u0430\u0441 \u043e\u0442 01:00 \u0434\u043e 24:00.')
        return SCHEDULE_END_TIME
    selected = context.user_data.get('schedule_employee')
    dates = context.user_data.get('schedule_dates') or ([context.user_data.get('schedule_date')] if context.user_data.get('schedule_date') else [])
    if not start_time or not dates or not all(dates) or not selected:
        clear_schedule_state(context)
        await update.message.reply_text('\u0421\u0446\u0435\u043d\u0430\u0440\u0438\u0439 \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c \u0443\u0441\u0442\u0430\u0440\u0435\u043b. \u041d\u0430\u0447\u043d\u0438\u0442\u0435 \u0437\u0430\u043d\u043e\u0432\u043e.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    role = get_user_role(update.effective_user.id)
    action = context.user_data.get('schedule_action')
    if action == 'add':
        added = []
        failures = []
        for date_value in dates:
            success, reason, entry = add_to_schedule(
                date_value, selected, start_time, end_time,
                actor_user_id=update.effective_user.id, actor_role=role,
                owner_user_id=config.OWNER_ID
            )
            if success:
                added.append(entry)
                audit_action(update, 'schedule_entry_added', target_user_id=entry.get('user_id'), details={
                    'schedule_id': entry.get('id'), 'employee_id': entry.get('employee_id'),
                    'employee_name': entry.get('employee_name'), 'date': entry.get('date'),
                    'start_time': entry.get('start_time'), 'end_time': entry.get('end_time'),
                    'batch_size': len(dates)
                })
            else:
                failures.append((date_value, reason))
        lines = [f'\u2705 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0441\u043c\u0435\u043d: {len(added)} \u0438\u0437 {len(dates)}.']
        if failures:
            lines.append('\u041d\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u044b:')
            messages = {
                'owner_protected': '\u043d\u0435\u043b\u044c\u0437\u044f \u043d\u0430\u0437\u043d\u0430\u0447\u0430\u0442\u044c \u0441\u043c\u0435\u043d\u0443 \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430',
                'overlap': '\u043f\u0435\u0440\u0435\u0441\u0435\u0447\u0435\u043d\u0438\u0435 \u0441 \u0434\u0440\u0443\u0433\u043e\u0439 \u0441\u043c\u0435\u043d\u043e\u0439',
                'invalid_date': '\u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u0434\u0430\u0442\u0430',
                'invalid_time': '\u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e\u0435 \u0432\u0440\u0435\u043c\u044f',
                'employee_not_available': '\u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435 \u0430\u043a\u0442\u0438\u0432\u0435\u043d \u0438\u043b\u0438 \u0435\u0433\u043e \u0434\u0430\u043d\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0438\u0441\u044c',
                'invalid_schedule_data': '\u0444\u0430\u0439\u043b \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d \u0438\u043b\u0438 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u0441\u0442\u0430\u0440\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442; \u0434\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0438\u0441\u0430\u043d\u044b',
                'forbidden': '\u043d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043f\u0440\u0430\u0432'
            }
            lines.extend(
                f'\u2022 {datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")}: {messages.get(reason, reason)}'
                for day, reason in failures
            )
        message = '\n'.join(lines)
    elif action == 'edit':
        date_value = dates[0]
        success, reason, old_entry, new_entry = update_schedule_entry(
            context.user_data.get('schedule_selected_id'), date_value, selected,
            start_time, end_time, actor_user_id=update.effective_user.id,
            actor_role=role, owner_user_id=config.OWNER_ID
        )
        if success:
            audit_action(update, 'schedule_entry_updated', target_user_id=new_entry.get('user_id'), details={
                'schedule_id': new_entry.get('id'), 'employee_id': new_entry.get('employee_id'),
                'employee_name': new_entry.get('employee_name'), 'old_date': old_entry.get('date'),
                'new_date': new_entry.get('date'), 'old_start_time': old_entry.get('start_time'),
                'new_start_time': new_entry.get('start_time'), 'old_end_time': old_entry.get('end_time'),
                'new_end_time': new_entry.get('end_time')
            })
            message = '\u2705 \u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.'
        else:
            messages = {'owner_protected': '\u274c \u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440 \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u0432\u0430\u0442\u044c \u0438\u043b\u0438 \u0438\u0437\u043c\u0435\u043d\u044f\u0442\u044c \u0441\u043c\u0435\u043d\u044b \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430.', 'overlap': '\u274c \u0423 \u044d\u0442\u043e\u0433\u043e \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u0430 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0441\u043c\u0435\u043d\u0430, \u043f\u0435\u0440\u0435\u0441\u0435\u043a\u0430\u044e\u0449\u0430\u044f\u0441\u044f \u0441 \u0443\u043a\u0430\u0437\u0430\u043d\u043d\u044b\u043c \u0432\u0440\u0435\u043c\u0435\u043d\u0435\u043c.', 'invalid_date': '\u274c \u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u0434\u0430\u0442\u0430.', 'invalid_time': '\u274c \u0412\u0440\u0435\u043c\u044f \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u0443\u043a\u0430\u0437\u0430\u043d\u043e \u0446\u0435\u043b\u044b\u043c\u0438 \u0447\u0430\u0441\u0430\u043c\u0438, \u0430 \u043e\u043a\u043e\u043d\u0447\u0430\u043d\u0438\u0435 \u2014 \u043f\u043e\u0437\u0436\u0435 \u043d\u0430\u0447\u0430\u043b\u0430.', 'employee_not_available': '\u274c \u042d\u0442\u043e\u0442 \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435 \u0430\u043a\u0442\u0438\u0432\u0435\u043d \u0438\u043b\u0438 \u0435\u0433\u043e \u0434\u0430\u043d\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0438\u0441\u044c.', 'invalid_schedule_data': '\u274c \u0424\u0430\u0439\u043b \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d \u0438\u043b\u0438 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u0441\u0442\u0430\u0440\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. \u0414\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0438\u0441\u0430\u043d\u044b.', 'not_found': '\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430.', 'forbidden': '\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043f\u0440\u0430\u0432 \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f.'}
            message = messages.get(reason, '\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443.')
    else:
        message = '\u274c \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c. \u041d\u0430\u0447\u043d\u0438\u0442\u0435 \u0437\u0430\u043d\u043e\u0432\u043e.'
    clear_schedule_state(context)
    await update.message.reply_text(message, reply_markup=get_schedule_management_keyboard())
    return SCHEDULE_MENU


async def schedule_invalid_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_schedule_state(context)
        return ConversationHandler.END
    records = context.user_data.get('schedule_invalid_records', [])
    selected_numbers = parse_number_selection(update.message.text.strip(), len(records))
    if selected_numbers is None:
        await update.message.reply_text('\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0432\u044b\u0431\u043e\u0440. \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440\u0430, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 1,3,5, \u0438\u043b\u0438 \u0434\u0438\u0430\u043f\u0430\u0437\u043e\u043d 1-4.')
        return SCHEDULE_DELETE_INVALID_SELECT
    selected_records = [records[number - 1] for number in selected_numbers]
    context.user_data['schedule_invalid_selected_indices'] = [item['index'] for item in selected_records]
    context.user_data['schedule_invalid_selected_records'] = selected_records
    lines = [f'\u0411\u0443\u0434\u0443\u0442 \u0443\u0434\u0430\u043b\u0435\u043d\u044b \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0437\u0430\u043f\u0438\u0441\u0438: {len(selected_records)}.']
    lines.extend(f'\u2022 {item["name"]} \u2014 {item["date"]} \u2014 {item["start_time"]}\u2013{item["end_time"]}' for item in selected_records)
    lines.append('\u0412\u0432\u0435\u0434\u0438\u0442\u0435 1 \u0434\u043b\u044f \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f \u0438\u043b\u0438 2 \u0434\u043b\u044f \u043e\u0442\u043c\u0435\u043d\u044b.')
    await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
    return SCHEDULE_DELETE_INVALID_CONFIRM


async def schedule_invalid_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        clear_schedule_state(context)
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {'1', '2'}:
        await update.message.reply_text('\u0412\u0432\u0435\u0434\u0438\u0442\u0435 1 \u0434\u043b\u044f \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f \u0438\u043b\u0438 2 \u0434\u043b\u044f \u043e\u0442\u043c\u0435\u043d\u044b.')
        return SCHEDULE_DELETE_INVALID_CONFIRM
    if choice == '2':
        clear_schedule_state(context)
        await update.message.reply_text('\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0445 \u0441\u043c\u0435\u043d \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    indices = context.user_data.get('schedule_invalid_selected_indices', [])
    selected_records = context.user_data.get('schedule_invalid_selected_records', [])
    role = get_user_role(update.effective_user.id)
    success, reason, deleted = delete_invalid_schedule_records(
        indices, actor_user_id=update.effective_user.id, actor_role=role,
        owner_user_id=config.OWNER_ID
    )
    if success:
        audit_action(update, 'invalid_schedule_records_deleted', details={
            'deleted_count': len(deleted),
            'records': [{'index': item.get('index'), 'name': item.get('name'), 'date': item.get('date')} for item in selected_records]
        })
        message = f'\u2705 \u0423\u0434\u0430\u043b\u0435\u043d\u043e \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0445 \u0441\u043c\u0435\u043d: {len(deleted)}.'
    else:
        errors = {
            'forbidden': '\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043f\u0440\u0430\u0432 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f \u0437\u0430\u043f\u0438\u0441\u0435\u0439.',
            'owner_protected': '\u0421\u0440\u0435\u0434\u0438 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0445 \u0437\u0430\u043f\u0438\u0441\u0435\u0439 \u0435\u0441\u0442\u044c \u0441\u043c\u0435\u043d\u0430 \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430. \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.',
            'not_invalid': '\u041e\u0434\u043d\u0430 \u0438\u0437 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0445 \u0437\u0430\u043f\u0438\u0441\u0435\u0439 \u0443\u0436\u0435 \u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e\u0439. \u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e.',
            'invalid_schedule_file': '\u0424\u0430\u0439\u043b \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d \u0438\u043b\u0438 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 JSON. \u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e.',
            'invalid_selection': '\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u044b \u0437\u0430\u043f\u0438\u0441\u0438 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f.',
            'not_found': '\u0412\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0435 \u0437\u0430\u043f\u0438\u0441\u0438 \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u044b. \u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e.'
        }
        message = '\u274c ' + errors.get(reason, '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0437\u0430\u043f\u0438\u0441\u0438. \u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e.')
    clear_schedule_state(context)
    await update.message.reply_text(message, reply_markup=get_schedule_management_keyboard())
    return SCHEDULE_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Отмена административного диалога.'''

    role = get_user_role(update.effective_user.id)
    context.user_data.pop('new_employee_phone', None)
    clear_schedule_state(context)
    context.user_data.pop('editable_employee_ids', None)
    context.user_data.pop('editing_employee_id', None)
    clear_manage_employee_state(context)
    await update.message.reply_text('Текущая операция отменена. Выберите нужный пункт меню ещё раз.', reply_markup=get_main_keyboard(is_admin_role(role), role == 'owner'))
    return ConversationHandler.END

def main():
    '''Запуск бота.'''

    if not config.BOT_TOKEN:
        raise RuntimeError('Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example и укажите новый токен, полученный у @BotFather.')
    if config.OWNER_ID is None:
        raise RuntimeError('Не назначен владелец. Укажите свой числовой Telegram ID в OWNER_ID файла .env.')
    application = Application.builder().token(config.BOT_TOKEN).build()
    add_employee_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^Добавить сотрудника$'), button_handler)], states={ADD_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_phone)], GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_name)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    employee_edit_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^(?:Редактировать сотрудника|Назначить/снять администратора)$'), button_handler)], states={EDIT_EMPLOYEE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), select_employee_for_edit)], EDIT_EMPLOYEE_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), set_selected_employee_role)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    employee_management_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^(?:Изменить данные|Уволить сотрудника|Удалить сотрудника|Удалить ошибочные записи)$'), button_handler)], states={MANAGE_EMPLOYEE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_select)], MANAGE_EMPLOYEE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_name)], MANAGE_EMPLOYEE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_phone)], MANAGE_EMPLOYEE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_confirm)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    schedule_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^Управление графиком$'), button_handler)], states={SCHEDULE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_menu_action)], SCHEDULE_SELECT_ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_select_entry)], GET_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_date)], GET_SCHEDULE_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_employee)], GET_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_time)], SCHEDULE_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_end_time)], SCHEDULE_DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_delete_confirm)], SCHEDULE_DELETE_INVALID_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_invalid_select)], SCHEDULE_DELETE_INVALID_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_invalid_confirm)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('whoami', whoami))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(add_employee_conv)
    application.add_handler(employee_edit_conv)
    application.add_handler(employee_management_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    print('Бот запущен...')
    application.run_polling()

if __name__ == '__main__':
    main()
