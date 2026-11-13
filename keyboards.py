"""Функции построения клавиатур Telegram для меню и пошаговых диалогов."""

# keyboards.py
from telegram import ReplyKeyboardMarkup, KeyboardButton


BACK_BUTTON = "⬅️ Назад в меню"


def get_main_keyboard(has_admin_access=False, is_owner=False):
    """Создает главное меню; административные кнопки добавляет только при наличии прав."""
    buttons = [
        [KeyboardButton("Начать смену"), KeyboardButton("Закончить смену")],
        [KeyboardButton("График смен"), KeyboardButton("Мое время")]
    ]
    if has_admin_access:
        buttons.append([KeyboardButton("Редактировать"), KeyboardButton("Управление графиком")])
        buttons.append([KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_employee_management_keyboard(is_owner=False):
    """Создает меню управления сотрудниками с учетом роли владельца."""
    buttons = [
        [KeyboardButton("Добавить сотрудника"), KeyboardButton("Новая ссылка")],
        [KeyboardButton("Изменить данные"), KeyboardButton("Уволить сотрудника")],
        [KeyboardButton("Удалить сотрудника"), KeyboardButton("Удалить ошибочные записи")]
    ]
    if is_owner:
        buttons.append([KeyboardButton("Назначить/снять администратора")])
    buttons.append([KeyboardButton(BACK_BUTTON)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_dialog_keyboard():
    """Создает клавиатуру диалога с кнопкой возврата."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BACK_BUTTON)]],
        resize_keyboard=True
    )


def get_admin_keyboard():
    """Создает клавиатуру административного раздела."""
    buttons = [
        [KeyboardButton("Редактировать"), KeyboardButton("Управление графиком")],
        [KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")],
        [KeyboardButton("На главную")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_schedule_management_keyboard():
    """Создает меню добавления, изменения и удаления смен."""
    buttons = [
        [KeyboardButton("Добавить смену")],
        [KeyboardButton("Изменить смену"), KeyboardButton("Удалить смену")],
        [KeyboardButton("Удалить ошибочные смены")],
        [KeyboardButton(BACK_BUTTON)]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)
