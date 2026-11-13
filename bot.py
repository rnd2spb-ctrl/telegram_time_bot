"""Точка входа и обработчики Telegram-бота: авторизация, сотрудники, смены и отчеты."""

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

# --- Состояние диалогов и общие подписи ---
def clear_schedule_state(context):
    """Очищает временные данные диалога управления расписанием."""
    for key in ('schedule_action', 'schedule_date', 'schedule_dates', 'schedule_employee', 'schedule_employee_options', 'schedule_start_time', 'schedule_entry_ids', 'schedule_selected_id', 'schedule_selected_ids', 'schedule_delete_employee_options', 'schedule_delete_employee', 'schedule_invalid_indices', 'schedule_invalid_records', 'schedule_invalid_selected_indices', 'schedule_invalid_selected_records'):
        context.user_data.pop(key, None)

MENU_BUTTONS = ('Начать смену', 'Закончить смену', 'График смен', 'Мое время', 'Редактировать', 'Управление графиком', 'Добавить смену', 'Изменить смену', 'Удалить смену', 'Удалить ошибочные смены', 'Добавить сотрудника', 'Список сотрудников', 'Редактировать сотрудника', 'Изменить данные', 'Уволить сотрудника', 'Удалить сотрудника', 'Новая ссылка', 'Удалить ошибочные записи', 'Назначить/снять администратора', 'Отчет за неделю', 'Отчет за месяц', 'На главную', '⬅️ Назад в меню')
MENU_BUTTON_PATTERN = '^(?:' + '|'.join((re.escape(label) for label in MENU_BUTTONS)) + ')$'


# --- Роли, авторизация и идентификация ---
def employee_prompt_label(employee):
    """Формирует короткую подпись сотрудника с отметкой администратора."""
    if not isinstance(employee, dict):
        return '\u0421\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a >'
    if (config.OWNER_ID is not None and employee.get('user_id') == config.OWNER_ID) or employee.get('role') == 'owner':
        return '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
    surname = str(employee.get('last_name') or '').strip()
    if not surname:
        full_name = ' '.join(str(employee.get(key) or '').strip() for key in ('first_name', 'name')).strip()
        surname = full_name.split()[0] if full_name else '\u0411\u0435\u0437 \u0444\u0430\u043c\u0438\u043b\u0438\u0438'
    role = employee.get('role') or 'employee'
    suffix = ' (\u0430\u0434\u043c\u0438\u043d)' if role == 'admin' else ''
    return f'{surname}{suffix} >'

def schedule_entry_prompt_label(entry):
    """Определяет подпись сотрудника для строки расписания."""
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
    """Разбирает номера, списки и включительные диапазоны; возвращает None при ошибке."""
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
    """Преобразует номера дней выбранного месяца в даты ISO YYYY-MM-DD."""
    reference_date = reference_date or date.today()
    days_in_month = calendar.monthrange(reference_date.year, reference_date.month)[1]
    days = parse_number_selection(value, days_in_month)
    if days is None:
        return None
    return [date(reference_date.year, reference_date.month, day).isoformat() for day in days]


def current_user_prompt_label(user, employee=None, role=None):
    """Формирует подпись текущего пользователя с учетом его роли."""
    role = role if role is not None else get_user_role(user.id)
    if role == 'owner':
        return '\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 >'
    employee = employee or get_employee_by_user_id(user.id)
    if employee:
        return employee_prompt_label(employee)
    return f'{getattr(user, "first_name", None) or "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c"} >'

def get_user_role(user_id):
    """Возвращает роль пользователя; неактивным и неизвестным пользователям доступ не дает."""

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
    """Проверяет, относится ли роль к владельцу или администратору."""
    return role in {'admin', 'owner'}

async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повторно проверяет административные права перед выполнением действия."""

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
    """Записывает действие текущего пользователя в журнал аудита."""

    user = update.effective_user
    if not user:
        return False
    return log_action(actor_user_id=user.id, actor_role=get_user_role(user.id), action=action, target_user_id=target_user_id, details=details)

LINK_METHOD_INVITE = 'invite'
NEED_INVITE_TEXT = 'Для доступа нужна ссылка-приглашение от администратора. Попросите выдать её и откройте ссылку.'
INVALID_INVITE_TEXT = 'Ссылка недействительна или устарела. Попросите администратора выдать новую.'
NEED_EMPLOYEE_RECORD_TEXT = 'Для учёта рабочего времени нужна запись сотрудника, привязанная к вашему Telegram. Добавьте её через «Редактировать» → «Добавить сотрудника» и войдите по выданной ссылке.'

def full_name_of(employee):
    """ФИО записи сотрудника в формате «Фамилия Имя»."""
    return ' '.join((part for part in (employee.get('last_name'), employee.get('first_name')) if part)) or 'Имя не указано'

def format_invite_link(bot_username, token):
    """Ссылка для входа; без username бота возвращает команду для ручного ввода."""
    if bot_username:
        return f'https://t.me/{bot_username}?start={token}'
    return f'/start {token}'

def invite_link_for(context, token):
    """Ссылка для входа по username бота из контекста."""
    return format_invite_link(getattr(getattr(context, 'bot', None), 'username', None), token)

def build_invite_message(full_name, link):
    """Сообщение админу со ссылкой для входа сотрудника."""
    return f'🔗 Ссылка для входа сотрудника {full_name} (действует {INVITE_TTL_HOURS} ч, сработает один раз):\n{link}\n\nПередайте её сотруднику лично или в закрытом чате: войдёт тот, кто откроет ссылку первым. Когда сотрудник войдёт, вы получите уведомление.'

def describe_link_state(employee):
    """Короткая пометка о входе сотрудника для списков; пустая, если вход не нужен."""
    if employee.get('status', 'active') != 'active':
        return ''
    state, expires = get_invite_state(employee)
    if state == 'pending':
        return f"⏳ ожидает входа (ссылка до {expires.strftime('%d.%m %H:%M')})"
    if state == 'expired':
        return '⚠️ ссылка истекла — выдайте новую'
    if state == 'none':
        return '⚠️ нет ссылки — выдайте новую'
    return ''

def get_link_notification_recipients(exclude_user_id=None, extra_user_ids=()):
    """Telegram ID владельца и активных администраторов, которых нужно уведомить.

    extra_user_ids — например, выдавший ссылку: он получает уведомление, пока
    остаётся владельцем или активным администратором.
    """
    recipients = []
    if config.OWNER_ID is not None:
        recipients.append(config.OWNER_ID)
    admin_ids = set()
    for item in get_all_employees():
        user_id = item.get('user_id')
        if user_id is not None and item.get('role') == 'admin' and (item.get('status', 'active') == 'active'):
            admin_ids.add(user_id)
            recipients.append(user_id)
    for user_id in extra_user_ids:
        if user_id is not None and (user_id == config.OWNER_ID or user_id in admin_ids):
            recipients.append(user_id)
    unique = []
    for user_id in recipients:
        if user_id != exclude_user_id and user_id not in unique:
            unique.append(user_id)
    return unique

def build_link_notification(user, employee):
    """Текст уведомления о том, кто вошёл по приглашению."""
    username = f'@{user.username}' if getattr(user, 'username', None) else 'нет username'
    telegram_name = ' '.join((part for part in (getattr(user, 'first_name', None), getattr(user, 'last_name', None)) if part)) or '—'
    issuer = '—'
    issuer_id = employee.get('invited_by')
    if issuer_id is not None:
        issuer_record = get_employee_by_user_id(issuer_id)
        issuer = f'{full_name_of(issuer_record)} (ID {issuer_id})' if issuer_record else f'ID {issuer_id}'
    invited_at = employee.get('invited_at')
    if invited_at:
        try:
            issuer += f", {datetime.fromisoformat(invited_at).strftime('%d.%m.%Y %H:%M')}"
        except ValueError:
            pass
    return '\n'.join([
        '🔔 Сотрудник вошёл в бота по ссылке-приглашению',
        f'Запись: {full_name_of(employee)}',
        f"Телефон: {employee.get('phone_number') or '—'}",
        f'Аккаунт Telegram: {telegram_name}, {username}, ID {user.id}',
        f'Ссылку выдал: {issuer}',
        f"Время входа: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        'Если это не тот человек, откройте «Редактировать» → «Уволить сотрудника», а затем выдайте настоящему сотруднику новую запись и ссылку.'
    ])

async def notify_admins_about_link(context, user, employee):
    """Отправляет владельцу и админам сообщение о новой привязке аккаунта.

    Ошибка доставки одному получателю (например, он не запускал бота) не влияет
    на вход сотрудника и на остальных получателей. Возвращает (доставлено, не доставлено).
    """
    text = build_link_notification(user, employee)
    recipients = get_link_notification_recipients(exclude_user_id=user.id, extra_user_ids=(employee.get('invited_by'),))
    delivered, failed = [], []
    for chat_id in recipients:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            delivered.append(chat_id)
        except Exception:
            failed.append(chat_id)
            logger.warning('Не удалось отправить уведомление о привязке пользователю %s', chat_id, exc_info=True)
    return delivered, failed

async def complete_invite_login(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    """Привязывает аккаунт к записи сотрудника по коду из ссылки-приглашения."""
    user = update.effective_user
    ok, reason, employee = redeem_invite(token, user.id)
    if not ok:
        text = 'Ваш Telegram уже привязан к другой записи. Обратитесь к владельцу.' if reason == 'user_already_bound' else INVALID_INVITE_TEXT
        await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())
        return
    role = get_user_role(user.id)
    delivered, failed = await notify_admins_about_link(context, user, employee)
    audit_action(update, 'employee_linked', target_user_id=user.id, details={'employee_name': full_name_of(employee), 'phone_number': employee.get('phone_number'), 'method': LINK_METHOD_INVITE, 'invited_by': employee.get('invited_by'), 'notified': delivered, 'notification_failed': failed})
    await update.message.reply_text(f'Добро пожаловать, {current_user_prompt_label(user, employee, role)}!', reply_markup=get_main_keyboard(is_admin_role(role), role == 'owner'))

async def contact_not_supported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Контакт больше не даёт вход: отвечаем, а не молчим."""
    if get_user_role(update.effective_user.id) is None:
        await update.message.reply_text(f'Контакт больше не используется для входа. {NEED_INVITE_TEXT}', reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text('Контакт больше не используется. Вход в бота выполняется только по ссылке-приглашению.')

# --- Команды и основное меню ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает /start и показывает меню; /start КОД выполняет вход по ссылке-приглашению."""
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)
    args = getattr(context, 'args', None)
    if args and employee is None:
        await complete_invite_login(update, context, args[0])
        return
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
    await update.message.reply_text(f'Добро пожаловать! {NEED_INVITE_TEXT}', reply_markup=ReplyKeyboardRemove())

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает Telegram ID, ФИО и роль; затем восстанавливает главное меню."""
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
        f'\u0412\u0430\u0448 \u043f\u0440\u043e\u0444\u0438\u043b\u044c: {label}\n\u0424\u0418\u041e: {full_name}\nTelegram ID: {user.id}\n\u0420\u043e\u043b\u044c: {role_name}',
        reply_markup=get_main_keyboard(is_admin_role(role), role == 'owner')
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает основные кнопки: смены, время, расписание, сотрудников и отчеты."""

    text = update.message.text
    user = update.effective_user
    employee = get_employee_by_user_id(user.id)
    role = get_user_role(user.id)
    if role is None:
        if employee:
            await update.message.reply_text('❌ Ваш доступ приостановлен. Обратитесь к владельцу бота.', reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(NEED_INVITE_TEXT, reply_markup=ReplyKeyboardRemove())
        return
    if text == 'Начать смену':
        if not employee and role != 'owner':
            await update.message.reply_text(NEED_EMPLOYEE_RECORD_TEXT)
            return
        active_shift = get_active_shift(user.id)
        if active_shift:
            await update.message.reply_text('У вас уже есть активная смена.')
            return
        start_shift(user.id, employee_name=current_user_prompt_label(user, employee, role))
        active_shift = get_active_shift(user.id)
        audit_action(update, 'shift_started', target_user_id=user.id, details={'shift_start': active_shift.get('shift_start') if active_shift else None})
        start_time = datetime.now().strftime('%H:%M')
        await update.message.reply_text(f'✅ Смена начата в {start_time}')
        return
    if text == 'Закончить смену':
        if not employee and role != 'owner':
            await update.message.reply_text(NEED_EMPLOYEE_RECORD_TEXT)
            return
        active_shift = get_active_shift(user.id)
        if not active_shift:
            await update.message.reply_text('У вас нет активной смены.')
            return
        start_time = datetime.strptime(active_shift['shift_start'], '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        if (now - start_time).total_seconds() < 3600:
            await update.message.reply_text('\u274c \u041d\u0435\u043b\u044c\u0437\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u044c \u0441\u043c\u0435\u043d\u0443 \u0440\u0430\u043d\u044c\u0448\u0435, \u0447\u0435\u043c \u0447\u0435\u0440\u0435\u0437 1 \u0447\u0430\u0441 \u043f\u043e\u0441\u043b\u0435 \u043d\u0430\u0447\u0430\u043b\u0430.')
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
        if not employee and role != 'owner':
            await update.message.reply_text(NEED_EMPLOYEE_RECORD_TEXT)
            return
        shifts = get_user_shifts(user.id, 7)
        total_time = calculate_work_time(shifts)
        salary = calculate_salary(shifts)
        await update.message.reply_text(f'⏱ Ваше время за последнюю неделю: {total_time}\n💵 Ваша зарплата: {salary} руб.')
        return
    if text == 'Добавить сотрудника':
        context.user_data['menu_parent'] = 'employees'
        if not await require_admin(update, context):
            return
        await update.message.reply_text('Введите номер телефона нового сотрудника в формате +79123456789:', reply_markup=get_dialog_keyboard())
        return ADD_EMPLOYEE
    if text == 'Управление графиком':
        context.user_data['menu_parent'] = 'main'
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
                line = f'\u2022 {employee_prompt_label(item)} \u2014 {phone} \u2014 {status}'
                link_state = describe_link_state(item)
                if link_state:
                    line += f' \u2014 {link_state}'
                lines.append(line)
            message = '\n'.join(lines)
        await update.message.reply_text(message, reply_markup=get_main_keyboard(True, role == 'owner'))
        return
    if text in {'Редактировать сотрудника', 'Назначить/снять администратора'}:
        context.user_data['menu_parent'] = 'employees'
        if role != 'owner':
            await update.message.reply_text('❌ Только владелец может изменять роли сотрудников.')
            return
        employees = [item for item in get_all_employees() if item.get('user_id') != config.OWNER_ID and item.get('role') != 'owner' and item.get('status', 'active') == 'active' and item.get('phone_number')]
        if not employees:
            await update.message.reply_text('Нет активных сотрудников, которым можно изменить роль.', reply_markup=get_employee_management_keyboard(True))
            return ConversationHandler.END
        context.user_data['editable_employee_phones'] = [item['phone_number'] for item in employees]
        lines = ['Выберите сотрудника, введя номер из списка:']
        for index, item in enumerate(employees, 1):
            name = ' '.join((part for part in (item.get('last_name'), item.get('first_name')) if part)) or 'Имя не указано'
            lines.append(f'{index}. {employee_prompt_label(item)}')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return EDIT_EMPLOYEE_SELECT
    if text == 'Редактировать':
        context.user_data['menu_parent'] = 'main'
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
            line = f'{status_marker}{employee_prompt_label(item)} — {phone} — {item.get('status', '\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d')}'
            link_state = describe_link_state(item)
            if link_state:
                line += f' — {link_state}'
            lines.append(line)
        await update.message.reply_text('\n'.join(lines), reply_markup=get_employee_management_keyboard(role == 'owner'))
        return
    if text in {'Изменить данные', 'Уволить сотрудника', 'Удалить сотрудника', 'Новая ссылка', 'Удалить ошибочные записи'}:
        context.user_data['menu_parent'] = 'employees'
        if not await require_admin(update, context):
            return
        action = {'Изменить данные': 'edit', 'Уволить сотрудника': 'dismiss', 'Удалить сотрудника': 'delete', 'Новая ссылка': 'invite', 'Удалить ошибочные записи': 'delete_invalid'}[text]
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
                if action in {'edit', 'dismiss', 'invite'}:
                    if item.get('status', 'active') != 'active':
                        continue
                if action == 'invite' and item.get('user_id') is not None:
                    continue
                if action == 'invite' and item.get('role') == 'admin' and role != 'owner':
                    continue
                employees.append(item)
            if not employees:
                empty_text = 'Все активные сотрудники уже вошли в бота: новая ссылка не нужна.' if action == 'invite' else 'Нет сотрудников, доступных для этой операции.'
                await update.message.reply_text(empty_text, reply_markup=get_employee_management_keyboard(role == 'owner'))
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
        await cancel(update, context)
        return ConversationHandler.END
    await start(update, context)

# --- Назначение ролей сотрудников ---
async def select_employee_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает владельцу список сотрудников для изменения их роли."""

    if get_user_role(update.effective_user.id) != 'owner':
        await update.message.reply_text('❌ Недостаточно прав. Операция отменена.')
        return ConversationHandler.END
    employee_phones = context.user_data.get('editable_employee_phones', [])
    value = update.message.text.strip()
    if not value.isdigit() or not 1 <= int(value) <= len(employee_phones):
        await update.message.reply_text('Введите номер сотрудника из списка или нажмите «Назад в меню».')
        return EDIT_EMPLOYEE_SELECT
    phone = employee_phones[int(value) - 1]
    employee = get_employee_by_phone(phone)
    if not employee or employee.get('status', 'active') != 'active' or employee.get('role') == 'owner' or employee.get('user_id') == config.OWNER_ID:
        await update.message.reply_text('Сотрудник больше не доступен для изменения.')
        context.user_data.pop('editable_employee_phones', None)
        return ConversationHandler.END
    context.user_data['editing_employee_phone'] = phone
    current_role = employee.get('role') or 'employee'
    if current_role == 'admin':
        prompt = 'У сотрудника сейчас роль администратора.\nВведите 1, чтобы снять права администратора, или 2, чтобы оставить без изменений.'
    else:
        prompt = 'У сотрудника сейчас роль обычного сотрудника.\nВведите 1, чтобы назначить администратором, или 2, чтобы оставить без изменений.'
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return EDIT_EMPLOYEE_ROLE

async def set_selected_employee_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет выбранную роль сотрудника после проверки прав владельца."""
    if get_user_role(update.effective_user.id) != 'owner':
        await update.message.reply_text('❌ Только владелец может изменять роли сотрудников.')
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice not in {'1', '2'}:
        await update.message.reply_text('Введите 1 или 2 либо нажмите «Назад в меню».')
        return EDIT_EMPLOYEE_ROLE
    if choice == '2':
        result = 'Изменения не внесены.'
    else:
        phone = context.user_data.get('editing_employee_phone')
        employee = get_employee_by_phone(phone) if phone else None
        if not employee or employee.get('status', 'active') != 'active' or employee.get('user_id') == config.OWNER_ID or employee.get('role') == 'owner':
            result = 'Сотрудник больше не доступен для изменения роли.'
        else:
            old_role = employee.get('role') or 'employee'
            new_role = 'employee' if old_role == 'admin' else 'admin'
            changed, reason = set_employee_role_by_phone(phone, new_role, protected_user_id=config.OWNER_ID)
            if changed:
                audit_action(update, 'employee_role_changed', target_user_id=employee.get('user_id'), details={'old_role': old_role, 'new_role': new_role, 'phone_number': phone})
            result = '✅ Роль успешно изменена: ' + ('администратор.' if new_role == 'admin' else 'обычный сотрудник.') if changed else '❌ Не удалось изменить роль: ' + reason
    context.user_data.pop('editing_employee_phone', None)
    context.user_data.pop('editable_employee_phones', None)
    await update.message.reply_text(result, reply_markup=get_employee_management_keyboard(True))
    return ConversationHandler.END

# --- Добавление и управление сотрудниками ---
def clear_manage_employee_state(context):
    """Удаляет временные данные диалога управления сотрудниками."""
    for key in ('manage_employee_action', 'manage_employee_indexes', 'manage_employee_invalid_records', 'manage_employee_phones', 'manage_employee_phone', 'manage_employee_first_name', 'manage_employee_last_name'):
        context.user_data.pop(key, None)

async def manage_employee_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет выбранную запись и запускает соответствующее действие над сотрудником."""
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
    if action == 'invite':
        is_owner = get_user_role(update.effective_user.id) == 'owner'
        keyboard = get_employee_management_keyboard(is_owner)
        if employee.get('role') == 'admin' and not is_owner:
            clear_manage_employee_state(context)
            await update.message.reply_text('❌ Только владелец может выдавать ссылки администраторам.', reply_markup=keyboard)
            return ConversationHandler.END
        token = create_invite(phone, issued_by=update.effective_user.id)
        clear_manage_employee_state(context)
        if token is None:
            await update.message.reply_text('❌ Ссылку выдать нельзя: сотрудник уже вошёл в бота или его доступ приостановлен.', reply_markup=keyboard)
            return ConversationHandler.END
        audit_action(update, 'invite_reissued', details={'employee_name': full_name_of(employee), 'phone_number': phone})
        await update.message.reply_text(build_invite_message(full_name_of(employee), invite_link_for(context, token)), reply_markup=keyboard)
        return ConversationHandler.END
    context.user_data['manage_employee_phone'] = phone
    if action == 'edit':
        await update.message.reply_text('Введите новое ФИО в формате «Фамилия Имя Отчество»:', reply_markup=get_dialog_keyboard())
        return MANAGE_EMPLOYEE_NAME
    prompt = 'Уволить сотрудника? Запись и история смен сохранятся. Введите 1 для подтверждения или 2 для отмены.' if action == 'dismiss' else 'Удалить запись сотрудника? Это возможно только при отсутствии истории смен. Введите 1 для подтверждения или 2 для отмены.'
    await update.message.reply_text(prompt, reply_markup=get_dialog_keyboard())
    return MANAGE_EMPLOYEE_CONFIRM

async def manage_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает и проверяет новое ФИО выбранного сотрудника."""
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
    """Принимает новый номер и проверяет, что он не занят другой записью."""
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
    """Подтверждает или отменяет изменение, увольнение либо удаление сотрудника."""
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
    """Первый шаг добавления сотрудника: проверяет и сохраняет номер телефона."""

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
    """Второй шаг добавления сотрудника: принимает ФИО и создает запись."""

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
        keyboard = get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner')
        token = create_invite(phone, issued_by=update.effective_user.id)
        if token:
            audit_action(update, 'invite_created', details={'employee_name': full_name, 'phone_number': phone})
            await update.message.reply_text(f'✅ Сотрудник {full_name} успешно добавлен!\n\n' + build_invite_message(full_name, invite_link_for(context, token)), reply_markup=keyboard)
        else:
            await update.message.reply_text(f'✅ Сотрудник {full_name} добавлен, но ссылку выдать не удалось. Откройте «Редактировать» → «Новая ссылка».', reply_markup=keyboard)
    else:
        await update.message.reply_text('❌ Сотрудник с таким номером телефона уже существует.', reply_markup=get_employee_management_keyboard(get_user_role(update.effective_user.id) == 'owner'))
    context.user_data.pop('new_employee_phone', None)
    return ConversationHandler.END

# --- Управление расписанием ---
def _schedule_employee_options(role):
    """Формирует список сотрудников, которым текущая роль может назначать смены."""

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
        options.append({'employee_id': employee_id, 'user_id': employee.get('user_id'), 'employee_name': name, 'employee_phone': phone, 'role': employee.get('role') or 'employee'})
        continue
    options.sort(key=(lambda item: item['employee_name'].casefold()))
    if role == 'owner' and config.OWNER_ID is not None:
        options.insert(0, {'employee_id': f'owner:{config.OWNER_ID}', 'user_id': config.OWNER_ID, 'employee_name': 'Владелец', 'employee_phone': None})
    return options

def _schedule_entry_label(entry):
    """Форматирует дату, сотрудника и время одной записи расписания."""
    try:
        date_label = datetime.strptime(entry['date'], '%Y-%m-%d').strftime('%d.%m.%Y')
    except (KeyError, TypeError, ValueError):
        date_label = entry.get('date', '\u0434\u0430\u0442\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430')
    return f'{date_label} {chr(0x2014)} {schedule_entry_prompt_label(entry)} {chr(0x2014)} {entry.get("start_time", "?")}{chr(0x2013)}{entry.get("end_time", "?")}'

def _manageable_schedule_entries(role):
    """Возвращает сортированный список смен, доступных текущей роли для управления."""
    entries = get_schedule()
    if role != 'owner':
        entries = [item for item in entries if item.get('user_id') != config.OWNER_ID if item.get('employee_id') != f'owner:{config.OWNER_ID}']
    return sorted(entries, key=(lambda item: (item.get('date', ''), item.get('start_time', ''), item.get('employee_name', ''))))

async def schedule_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор действия в меню управления расписанием."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    clear_schedule_state(context)
    role = get_user_role(update.effective_user.id)
    action = update.message.text.strip()
    if action == BACK_BUTTON:
        context.user_data['menu_parent'] = 'main'
        await cancel(update, context)
        return ConversationHandler.END
    context.user_data['menu_parent'] = 'schedule'
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
    if context.user_data['schedule_action'] == 'delete':
        current_month = datetime.now().strftime('%Y-%m-')
        entries = [item for item in entries if str(item.get('date', '')).startswith(current_month)]
        if not entries:
            await update.message.reply_text('В текущем месяце нет смен для удаления.', reply_markup=get_schedule_management_keyboard())
            return SCHEDULE_MENU
        employees_by_id = {}
        employee_records = get_all_employees()
        for entry in entries:
            employee_id = entry.get('employee_id')
            if not employee_id or employee_id in employees_by_id:
                continue
            phone_key = str(employee_id).removeprefix('phone:')
            employee_record = next((item for item in employee_records if normalize_phone(item.get('phone_number')) == phone_key), None)
            is_owner = employee_id == f'owner:{config.OWNER_ID}'
            employees_by_id[employee_id] = {
                'employee_id': employee_id, 'user_id': entry.get('user_id'),
                'employee_name': 'Владелец' if is_owner else (entry.get('employee_name') or 'Имя не указано'),
                'employee_phone': entry.get('employee_phone'),
                'role': 'owner' if is_owner else (employee_record or {}).get('role', 'employee')
            }
        options = sorted(employees_by_id.values(), key=lambda item: item['employee_name'].casefold())
        context.user_data['schedule_delete_employee_options'] = options
        lines = ['Выберите сотрудника, чьи смены нужно удалить:']
        for number, item in enumerate(options, 1):
            suffix = ' (админ)' if item.get('role') == 'admin' else ''
            phone_label = f' — {item["employee_phone"]}' if item.get('employee_phone') else ''
            lines.append(f'{number}. {item["employee_name"]}{suffix}{phone_label}')
        await update.message.reply_text('\
'.join(lines), reply_markup=get_dialog_keyboard())
        return GET_SCHEDULE_EMPLOYEE

    context.user_data['schedule_entry_ids'] = [item['id'] for item in entries]
    lines = ['Выберите смену, введя её номер:']
    lines.extend(
        f'{index}. {schedule_entry_prompt_label(item)} — {item.get("date")} — {item.get("start_time")}–{item.get("end_time")}'
        for index, item in enumerate(entries, 1)
    )
    await update.message.reply_text('\
'.join(lines), reply_markup=get_dialog_keyboard())
    return SCHEDULE_SELECT_ENTRY

async def schedule_select_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет выбор смены или набора смен для дальнейшего изменения/удаления."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    value = update.message.text.strip()
    entry_ids = context.user_data.get('schedule_entry_ids', [])
    action = context.user_data.get('schedule_action')
    if action == 'delete':
        selected_numbers = parse_number_selection(value, len(entry_ids))
        if selected_numbers is None:
            await update.message.reply_text('Некорректный выбор. Введите номера смен через запятую (например 1,3,5) или диапазон (например 1-5).')
            return SCHEDULE_SELECT_ENTRY
        selected_ids = [entry_ids[number - 1] for number in selected_numbers]
        entries = [get_schedule_entry(schedule_id) for schedule_id in selected_ids]
        if any(entry is None for entry in entries):
            clear_schedule_state(context)
            await update.message.reply_text('Одна или несколько смен уже недоступны. Обновите список смен.', reply_markup=get_schedule_management_keyboard())
            return SCHEDULE_MENU
        role = get_user_role(update.effective_user.id)
        if role != 'owner' and any(
            entry.get('user_id') == config.OWNER_ID or entry.get('employee_id') == f'owner:{config.OWNER_ID}'
            for entry in entries
        ):
            clear_schedule_state(context)
            await update.message.reply_text('❌ Администратор не может удалять смены владельца.', reply_markup=get_schedule_management_keyboard())
            return SCHEDULE_MENU
        context.user_data['schedule_selected_ids'] = selected_ids
        lines = [f'Будут удалены смены: {len(entries)}.']
        lines.extend(f'{number}. {_schedule_entry_label(entry)}' for number, entry in enumerate(entries, 1))
        lines.append('Введите 1 для подтверждения удаления или 2 для отмены.')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return SCHEDULE_DELETE_CONFIRM
    if not value.isdigit() or not 1 <= int(value) <= len(entry_ids):
        await update.message.reply_text('Введите номер смены из списка.')
        return SCHEDULE_SELECT_ENTRY
    schedule_id = entry_ids[int(value) - 1]
    entry = get_schedule_entry(schedule_id)
    role = get_user_role(update.effective_user.id)
    if not entry:
        clear_schedule_state(context)
        await update.message.reply_text('Выбранная смена уже недоступна. Обновите список смен.', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    is_owner_shift = entry.get('user_id') == config.OWNER_ID or entry.get('employee_id') == f'owner:{config.OWNER_ID}'
    if role != 'owner' and is_owner_shift:
        await update.message.reply_text('❌ Администратор не может изменять смены владельца.')
        clear_schedule_state(context)
        await update.message.reply_text('Выберите действие:', reply_markup=get_schedule_management_keyboard())
        return SCHEDULE_MENU
    context.user_data['schedule_selected_id'] = schedule_id
    context.user_data['schedule_selected_ids'] = [schedule_id]
    context.user_data['schedule_action'] = 'edit'
    await update.message.reply_text(f'Изменение смены:\n{_schedule_entry_label(entry)}\n\nВведите день/дни текущего месяца ({datetime.now():%m.%Y}), например 5 или 13,15,17 или диапазон 13-15:', reply_markup=get_dialog_keyboard())
    return GET_SCHEDULE_DATE

async def schedule_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждает удаление выбранных записей и фиксирует операцию в аудите."""
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
    schedule_ids = context.user_data.get('schedule_selected_ids') or [context.user_data.get('schedule_selected_id')]
    role = get_user_role(update.effective_user.id)
    success, reason, deleted = delete_schedule_entries(schedule_ids, actor_user_id=update.effective_user.id, actor_role=role, owner_user_id=config.OWNER_ID)
    if success:
        for item in deleted:
            audit_action(update, 'schedule_entry_deleted', target_user_id=item.get('user_id'), details={'schedule_id': item.get('id'), 'employee_id': item.get('employee_id'), 'employee_name': item.get('employee_name'), 'date': item.get('date'), 'start_time': item.get('start_time'), 'end_time': item.get('end_time')})
        message = f'✅ Удалено смен: {len(deleted)}.'
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
    """Проверяет введенные дни месяца и определяет следующий шаг диалога."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    date_text = update.message.text.strip()
    action = context.user_data.get('schedule_action')
    dates = parse_schedule_days(date_text, datetime.now().date())
    if dates is None:
        month = datetime.now()
        days_in_month = calendar.monthrange(month.year, month.month)[1]
        await update.message.reply_text(
            f'Некорректный список дней. Для месяца {month:%m.%Y} укажите числа от 1 до {days_in_month}: 13,15,17 или 13-15.'
        )
        return GET_SCHEDULE_DATE
    context.user_data['schedule_dates'] = dates
    context.user_data['schedule_date'] = dates[0]
    if action == 'delete':
        selected_employee = context.user_data.get('schedule_delete_employee') or {}
        employee_id = selected_employee.get('employee_id')
        matching = [entry for entry in _manageable_schedule_entries(get_user_role(update.effective_user.id))
                    if entry.get('employee_id') == employee_id and entry.get('date') in dates]
        if not matching:
            clear_schedule_state(context)
            await update.message.reply_text('У выбранного сотрудника нет смен на указанные дни.', reply_markup=get_schedule_management_keyboard())
            return SCHEDULE_MENU
        matching.sort(key=lambda item: (item.get('date', ''), item.get('start_time', '')))
        context.user_data['schedule_selected_ids'] = [item['id'] for item in matching]
        lines = [f'Будут удалены смены сотрудника {selected_employee.get("employee_name", "")}: {len(matching)}.']
        lines.extend(f'• {_schedule_entry_label(item)}' for item in matching)
        lines.append('Введите 1 для подтверждения или 2 для отмены.')
        await update.message.reply_text('\n'.join(lines), reply_markup=get_dialog_keyboard())
        return SCHEDULE_DELETE_CONFIRM

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
    """Обрабатывает выбор сотрудника для добавления, изменения или удаления смен."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    value = update.message.text.strip()
    if context.user_data.get('schedule_action') == 'delete':
        options = context.user_data.get('schedule_delete_employee_options', [])
        if not value.isdigit() or not 1 <= int(value) <= len(options):
            await update.message.reply_text('Введите номер сотрудника из списка.')
            return GET_SCHEDULE_EMPLOYEE
        context.user_data['schedule_delete_employee'] = dict(options[int(value) - 1])
        month = datetime.now()
        await update.message.reply_text(
            f'Введите день/дни текущего месяца ({month:%m.%Y}), например 5 или 13,15,17 или диапазон 13-15:',
            reply_markup=get_dialog_keyboard()
        )
        return GET_SCHEDULE_DATE

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
    await update.message.reply_text('Введите время начала: часы (9) или полный формат (09:30).', reply_markup=get_dialog_keyboard())
    return GET_SCHEDULE_TIME

def normalize_schedule_time(value, allow_24=False):
    """Проверяет часы и формат HH:MM/HH-MM; возвращает нормализованное HH:MM."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"\d{1,2}", value):
        hour = int(value)
        if 0 <= hour <= (24 if allow_24 else 23):
            return "24:00" if hour == 24 else f"{hour:02d}:00"
        return None
    if re.fullmatch(r"\d{1,2}[:-]\d{2}", value):
        hour, minute = map(int, re.split(r"[:-]", value))
        if minute > 59 or hour > 24 or (hour == 24 and (minute != 0 or not allow_24)):
            return None
        return f"{hour:02d}:{minute:02d}"
    return None


async def schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и сохраняет время начала смены."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    start_time = normalize_schedule_time(update.message.text, allow_24=False)
    if start_time is None:
        await update.message.reply_text('Некорректное время начала. Введите часы (например, 9) или время HH:MM (например, 09:30).')
        return GET_SCHEDULE_TIME
    context.user_data['schedule_start_time'] = start_time
    await update.message.reply_text('Введите время окончания: часы (15) или полный формат (15:30 / 24:00).', reply_markup=get_dialog_keyboard())
    return SCHEDULE_END_TIME

async def schedule_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет время окончания и сохраняет или обновляет смену."""
    if not await require_admin(update, context):
        return ConversationHandler.END
    start_time = context.user_data.get('schedule_start_time')
    end_time = normalize_schedule_time(update.message.text, allow_24=True)
    if end_time is None:
        await update.message.reply_text('Некорректное время окончания. Введите часы (например, 15) или время HH:MM (например, 15:30).')
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
        schedule_id = context.user_data.get('schedule_selected_id')
        success, reason, old_entry, first_new_entry = update_schedule_entry(
            schedule_id, dates[0], selected, start_time, end_time,
            actor_user_id=update.effective_user.id, actor_role=role,
            owner_user_id=config.OWNER_ID
        )
        if success:
            audit_action(update, 'schedule_entry_updated', target_user_id=first_new_entry.get('user_id'), details={
                'schedule_id': first_new_entry.get('id'), 'employee_id': first_new_entry.get('employee_id'),
                'employee_name': first_new_entry.get('employee_name'), 'old_date': old_entry.get('date'),
                'new_dates': dates, 'old_start_time': old_entry.get('start_time'),
                'new_start_time': first_new_entry.get('start_time'), 'old_end_time': old_entry.get('end_time'),
                'new_end_time': first_new_entry.get('end_time')
            })
            created = [first_new_entry]
            failures = []
            for date_value in dates[1:]:
                added_ok, add_reason, added_entry = add_to_schedule(
                    date_value, selected, start_time, end_time,
                    actor_user_id=update.effective_user.id, actor_role=role,
                    owner_user_id=config.OWNER_ID
                )
                if added_ok:
                    created.append(added_entry)
                    audit_action(update, 'schedule_entry_added', target_user_id=added_entry.get('user_id'), details={
                        'schedule_id': added_entry.get('id'), 'employee_id': added_entry.get('employee_id'),
                        'employee_name': added_entry.get('employee_name'), 'date': added_entry.get('date'),
                        'start_time': added_entry.get('start_time'), 'end_time': added_entry.get('end_time'),
                        'source_schedule_id': schedule_id
                    })
                else:
                    failures.append((date_value, add_reason))
            message_lines = [f'✅ Создано/изменено смен: {len(created)} из {len(dates)}.']
            if failures:
                message_lines.append('Не удалось добавить смены:')
                message_lines.extend(f'• {datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")}: {reason}' for day, reason in failures)
            message = '\n'.join(message_lines)
        else:
            messages = {'owner_protected': '❌ Администратор не может создавать или изменять смены владельца.', 'overlap': '❌ У этого сотрудника уже есть смена, пересекающаяся с указанным временем.', 'invalid_date': '❌ Некорректная дата.', 'invalid_time': '❌ Время окончания должно быть позже времени начала.', 'employee_not_available': '❌ Этот сотрудник больше не активен или его данные изменились.', 'invalid_schedule_data': '❌ Файл расписания повреждён или содержит старый формат. Данные не перезаписаны.', 'not_found': '❌ Запись расписания больше не найдена.', 'forbidden': '❌ Недостаточно прав для изменения расписания.'}
            message = messages.get(reason, '❌ Не удалось сохранить смену.')
    else:
        message = '\u274c \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c. \u041d\u0430\u0447\u043d\u0438\u0442\u0435 \u0437\u0430\u043d\u043e\u0432\u043e.'
    clear_schedule_state(context)
    await update.message.reply_text(message, reply_markup=get_schedule_management_keyboard())
    return SCHEDULE_MENU


# --- Очистка ошибочных записей расписания ---
async def schedule_invalid_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет выбор некорректных записей расписания для очистки."""
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
    """Подтверждает безопасное удаление выбранных некорректных записей."""
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

# --- Отмена диалогов и запуск приложения ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущий диалог, очищает его данные и возвращает в родительское меню."""
    role = get_user_role(update.effective_user.id)
    context.user_data.pop('new_employee_phone', None)
    clear_schedule_state(context)
    context.user_data.pop('editable_employee_phones', None)
    context.user_data.pop('editing_employee_phone', None)
    clear_manage_employee_state(context)
    parent = context.user_data.get('menu_parent')
    if parent == 'employees' and is_admin_role(role):
        markup = get_employee_management_keyboard(role == 'owner')
        message = 'Управление сотрудниками. Выберите действие:'
    elif parent == 'schedule' and is_admin_role(role):
        markup = get_schedule_management_keyboard()
        message = 'Управление расписанием. Выберите действие:'
    else:
        markup = get_main_keyboard(is_admin_role(role), role == 'owner')
        message = 'Главное меню:'
    await update.message.reply_text(message, reply_markup=markup)
    return ConversationHandler.END

def main():
    """Создает приложение Telegram, регистрирует обработчики и запускает polling."""

    if not config.BOT_TOKEN:
        raise RuntimeError('Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example и укажите новый токен, полученный у @BotFather.')
    if config.OWNER_ID is None:
        raise RuntimeError('Не назначен владелец. Укажите свой числовой Telegram ID в OWNER_ID файла .env.')
    application = Application.builder().token(config.BOT_TOKEN).build()
    add_employee_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^Добавить сотрудника$'), button_handler)], states={ADD_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_phone)], GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), add_employee_name)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    employee_edit_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^(?:Редактировать сотрудника|Назначить/снять администратора)$'), button_handler)], states={EDIT_EMPLOYEE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), select_employee_for_edit)], EDIT_EMPLOYEE_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), set_selected_employee_role)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    employee_management_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^(?:Изменить данные|Уволить сотрудника|Удалить сотрудника|Новая ссылка|Удалить ошибочные записи)$'), button_handler)], states={MANAGE_EMPLOYEE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_select)], MANAGE_EMPLOYEE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_name)], MANAGE_EMPLOYEE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_phone)], MANAGE_EMPLOYEE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), manage_employee_confirm)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    schedule_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^Управление графиком$'), button_handler)], states={SCHEDULE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_menu_action)], SCHEDULE_SELECT_ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_select_entry)], GET_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_date)], GET_SCHEDULE_EMPLOYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_employee)], GET_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_time)], SCHEDULE_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_end_time)], SCHEDULE_DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_delete_confirm)], SCHEDULE_DELETE_INVALID_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_invalid_select)], SCHEDULE_DELETE_INVALID_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN), schedule_invalid_confirm)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), cancel)])
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('whoami', whoami))
    application.add_handler(add_employee_conv)
    application.add_handler(employee_edit_conv)
    application.add_handler(employee_management_conv)
    application.add_handler(schedule_conv)
    application.add_handler(MessageHandler(filters.CONTACT, contact_not_supported))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    print('Бот запущен...')
    application.run_polling()

if __name__ == '__main__':
    main()
