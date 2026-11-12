# keyboards.py
from telegram import ReplyKeyboardMarkup, KeyboardButton


BACK_BUTTON = "⬅️ Назад в меню"


def get_main_keyboard(is_admin=False, is_owner=False):
    """Возвращает главное меню с кнопками."""
    buttons = [
        [KeyboardButton("Начать смену"), KeyboardButton("Закончить смену")],
        [KeyboardButton("График смен"), KeyboardButton("Мое время")]
    ]
    if is_admin:
        buttons.append([KeyboardButton("Добавить сотрудника"), KeyboardButton("Управление графиком")])
        buttons.append([KeyboardButton("Список сотрудников")])
        if is_owner:
            buttons.append([KeyboardButton("Редактировать сотрудника")])
        buttons.append([KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")])
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
        [KeyboardButton("Добавить сотрудника"), KeyboardButton("Управление графиком")],
        [KeyboardButton("Список сотрудников")],
        [KeyboardButton("Отчет за неделю"), KeyboardButton("Отчет за месяц")],
        [KeyboardButton("На главную")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)
