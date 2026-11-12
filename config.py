# config.py
import os
from pathlib import Path


def _load_env_file():
    """Минимальная загрузка KEY=VALUE из .env без внешних зависимостей."""
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


def _parse_admin_ids(value):
    admin_ids = set()
    for item in (value or "").replace(";", ",").split(","):
        item = item.strip()
        if item:
            admin_ids.add(_parse_telegram_id(item, "ADMIN_IDS"))
    return admin_ids


# Первоначальный владелец назначается явно через OWNER_ID.
# Первый пользователь, открывший бота, владельцем не становится.
OWNER_ID = _parse_telegram_id(os.getenv("OWNER_ID"), "OWNER_ID")

# Дополнительные администраторы, назначенные владельцем через конфигурацию.
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS"))
