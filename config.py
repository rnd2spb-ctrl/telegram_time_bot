"""Загрузка и проверка настроек бота из переменных окружения и файла .env."""

# config.py
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _load_env_file():
    """Загружает переменные из .env, не перезаписывая уже заданные переменные окружения."""
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


def _parse_telegram_id(value, variable_name):
    """Проверяет Telegram ID из настроек и преобразует его в положительное целое число."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        telegram_id = int(value)
    except ValueError as exc:
        raise ValueError(f"{variable_name} должен содержать числовой Telegram ID.") from exc
    if telegram_id <= 0:
        raise ValueError(f"{variable_name} должен быть положительным числом.")
    return telegram_id


# The owner is assigned explicitly; the first person to message the bot is not made owner.
OWNER_ID = _parse_telegram_id(os.getenv("OWNER_ID"), "OWNER_ID")


def _parse_hourly_rate(value):
    """Проверяет почасовую ставку и преобразует ее в Decimal."""
    if value is None or not str(value).strip():
        raise ValueError("Укажите HOURLY_RATE в файле .env — почасовую ставку в рублях.")
    try:
        rate = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("HOURLY_RATE должен быть числом в рублях за час.")
    if not rate.is_finite() or rate < 0:
        raise ValueError("HOURLY_RATE должен быть конечным неотрицательным числом.")
    return rate


# Значение ставки обязательно задаётся в .env.
HOURLY_RATE = _parse_hourly_rate(os.getenv("HOURLY_RATE"))
