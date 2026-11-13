# keyboards.py
from telegram import ReplyKeyboardMarkup, KeyboardButton


BACK_BUTTON = "⬅️ Назад в меню"


def get_main_keyboard(is_admin=False, is_owner=False):
    """Главное меню."""
    buttons = [
        [KeyboardButton("Начать смену"), KeyboardButton("Закончить смену")],
        [KeyboardButton("График смен"), KeyboardButton("Мое время")]
    ]
    if is_admin:
        buttons.append([KeyboardButton("Редактировать"), KeyboardButton("Управление графиком")])
        buttons.append([KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_employee_management_keyboard(is_owner=False):
    """Меню управления сотрудниками."""
    buttons = [
        [KeyboardButton("Добавить сотрудника")],
        [KeyboardButton("Изменить данные"), KeyboardButton("Уволить сотрудника")],
        [KeyboardButton("Удалить сотрудника"), KeyboardButton("Удалить ошибочные записи")]
    ]
    if is_owner:
        buttons.append([KeyboardButton("Назначить/снять администратора")])
    buttons.append([KeyboardButton(BACK_BUTTON)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_dialog_keyboard():
    """Клавиатура административного диалога с возможностью возврата."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BACK_BUTTON)]],
        resize_keyboard=True
    )


def get_phone_keyboard():
    """Клавиатура для запроса номера телефона."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_admin_keyboard():
    """Админская клавиатура."""
    buttons = [
        [KeyboardButton("Редактировать"), KeyboardButton("Управление графиком")],
        [KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")],
        [KeyboardButton("На главную")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_schedule_management_keyboard():
    """Меню добавления, изменения и удаления запланированных смен."""
    buttons = [
        [KeyboardButton("Добавить смену")],
        [KeyboardButton("Изменить смену"), KeyboardButton("Удалить смену")],
        [KeyboardButton("Удалить ошибочные смены")],
        [KeyboardButton(BACK_BUTTON)]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)
