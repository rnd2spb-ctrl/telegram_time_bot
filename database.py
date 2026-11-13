"""Работа с JSON-хранилищем: сотрудники, учет смен, расписание и журнал аудита."""

# database.py
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

DATA_DIR = "data"

# Срок действия ссылки-приглашения и допустимый вид кода из ссылки /start КОД.
INVITE_TTL_HOURS = 72
_INVITE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


# --- Общие функции JSON-хранилища ---
def ensure_data_dir():
    """Создает каталог data, если он еще не существует."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def load_json(filename):
    """Загружает JSON-файл из data; при отсутствии или ошибке возвращает пустой список."""
    ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_json(filename, data):
    """Сохраняет данные в JSON с UTF-8 и читаемым форматированием."""
    ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Функции для работы с сотрудниками
# --- Сотрудники и привязка Telegram ---
def normalize_phone(phone_number):
    """Оставляет в номере только цифры для сравнения разных форматов."""
    return "".join(char for char in str(phone_number or "") if char.isdigit())


def get_employee_by_phone(phone_number):
    """Находит сотрудника по нормализованному номеру телефона."""
    employees = load_json("employees.json")
    normalized_phone = normalize_phone(phone_number)
    for employee in employees:
        if normalize_phone(employee.get("phone_number")) == normalized_phone:
            return employee
    return None


def get_employee_by_user_id(user_id):
    """Находит сотрудника по привязанному Telegram ID."""
    employees = load_json("employees.json")
    for employee in employees:
        if employee.get("user_id") == user_id:
            return employee
    return None


def get_all_employees():
    """Возвращает все записи сотрудников, включая неактивные."""
    return load_json("employees.json")


def set_employee_role_by_phone(phone_number, role, protected_user_id=None):
    """Назначает или снимает роль администратора у активной записи по телефону."""
    if role not in {"employee", "admin"}:
        return False, "invalid_role"
    normalized_phone = normalize_phone(phone_number)
    if not normalized_phone:
        return False, "invalid_phone"

    employees = load_json("employees.json")
    employee = next(
        (item for item in employees
         if normalize_phone(item.get("phone_number")) == normalized_phone),
        None
    )
    if employee is None:
        return False, "not_found"
    if employee.get("user_id") == protected_user_id and protected_user_id is not None:
        return False, "protected"
    if employee.get("role") == "owner":
        return False, "protected"
    if employee.get("status", "active") != "active":
        return False, "inactive"

    current_role = employee.get("role") or "employee"
    if current_role == role:
        return False, "unchanged"

    employee["role"] = role
    save_json("employees.json", employees)
    return True, "ok"


# Привязка Telegram-аккаунта к записи сотрудника выполняется только по
# одноразовому приглашению (create_invite / redeem_invite). Номер телефона
# не доказывает владение записью, поэтому привязки по одному номеру нет.
def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invite(phone_number, issued_by=None):
    """Создаёт одноразовый код входа для активной записи без привязки.

    В employees.json хранятся только хеш кода, срок действия и Telegram ID
    выдавшего. Повторный вызов заменяет прежний код. Возвращает сам код
    (его нужно сразу показать админу) или None, если выдать код нельзя.
    """
    normalized_phone = normalize_phone(phone_number)
    if not normalized_phone:
        return None
    employees = load_json("employees.json")
    target = next(
        (item for item in employees
         if normalize_phone(item.get("phone_number")) == normalized_phone),
        None
    )
    if target is None or target.get("user_id") is not None:
        return None
    if target.get("status", "active") != "active":
        return None

    now = datetime.now()
    token = secrets.token_urlsafe(16)
    target["invite_hash"] = _hash_token(token)
    target["invite_expires"] = (
        now + timedelta(hours=INVITE_TTL_HOURS)
    ).isoformat(timespec="seconds")
    target["invite_issued_by"] = issued_by
    target["invite_issued_at"] = now.isoformat(timespec="seconds")
    save_json("employees.json", employees)
    return token


def redeem_invite(token, user_id):
    """Привязывает Telegram-аккаунт к записи по коду из ссылки-приглашения.

    Возвращает (успех, причина, запись). Причина "invalid" одинакова для
    неверного, использованного и просроченного кода и для приостановленной
    записи, чтобы по ответам нельзя было подбирать коды. После успешного
    входа код удаляется, а кто и когда его выдал остаётся в записи
    (invited_by, invited_at). Расписание синхронизируется с Telegram ID так
    же, как раньше при привязке.
    """
    if not isinstance(token, str) or not _INVITE_TOKEN_RE.fullmatch(token):
        return False, "invalid", None

    employees = load_json("employees.json")
    digest = _hash_token(token)
    target = next(
        (item for item in employees
         if isinstance(item.get("invite_hash"), str)
         and hmac.compare_digest(item["invite_hash"], digest)),
        None
    )
    if target is None or target.get("user_id") is not None:
        return False, "invalid", None
    if target.get("status", "active") != "active":
        return False, "invalid", None
    try:
        expired = datetime.fromisoformat(target["invite_expires"]) < datetime.now()
    except (KeyError, TypeError, ValueError):
        expired = True
    if expired:
        return False, "invalid", None

    if any(item is not target and item.get("user_id") == user_id for item in employees):
        return False, "user_already_bound", None

    now = datetime.now().isoformat(timespec="seconds")
    target["user_id"] = user_id
    target["invited_by"] = target.pop("invite_issued_by", None)
    target["invited_at"] = target.pop("invite_issued_at", None)
    target.pop("invite_hash", None)
    target.pop("invite_expires", None)
    target.setdefault("role", "employee")
    target.setdefault("status", "active")
    if not target.get("registered_at"):
        target["registered_at"] = now
    save_json("employees.json", employees)
    _sync_schedule_employee(normalize_phone(target.get("phone_number")), target)
    return True, "ok", dict(target)


def get_invite_state(employee):
    """Состояние входа записи: ("linked"|"pending"|"expired"|"none", срок или None)."""
    if employee.get("user_id") is not None:
        return "linked", None
    if not employee.get("invite_hash"):
        return "none", None
    try:
        expires = datetime.fromisoformat(employee["invite_expires"])
    except (KeyError, TypeError, ValueError):
        return "expired", None
    return ("expired" if expires < datetime.now() else "pending"), expires


def get_unlinked_employees():
    """Активные записи без привязанного Telegram-аккаунта."""
    return [
        item for item in load_json("employees.json")
        if item.get("user_id") is None and item.get("status", "active") == "active"
    ]


def add_employee(user_id, phone_number, first_name, last_name):
    """Создает новую запись сотрудника с постоянным employee_id и статусом."""
    normalized_phone = normalize_phone(phone_number)
    if not normalized_phone.isdigit() or not 7 <= len(normalized_phone) <= 15:
        return False
    if not str(first_name or "").strip() or not str(last_name or "").strip():
        return False

    employees = load_json("employees.json")

    # None means the employee has not linked a Telegram account yet.
    for employee in employees:
        if normalize_phone(employee.get("phone_number")) == normalized_phone:
            return False
        if user_id is not None and employee.get("user_id") == user_id:
            return False

    employee = {
        "user_id": user_id,
        "phone_number": phone_number,
        "first_name": first_name,
        "last_name": last_name,
        "role": "employee",
        "status": "active",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "registered_at": None
    }
    employees.append(employee)
    save_json("employees.json", employees)
    return True





def update_employee(phone_number, first_name=None, last_name=None, new_phone=None):
    """Изменяет ФИО и/или телефон сотрудника, синхронизируя связанные записи расписания."""
    employees = load_json("employees.json")
    target = next(
        (item for item in employees
         if normalize_phone(item.get("phone_number")) == normalize_phone(phone_number)),
        None
    )
    if target is None:
        return False, "not_found"
    old_phone_key = normalize_phone(target.get("phone_number"))
    if new_phone is not None:
        import re
        if not isinstance(new_phone, str) or not re.fullmatch(r"\+?\d{7,15}", new_phone):
            return False, "invalid_phone"
        normalized = normalize_phone(new_phone)
        if any(
            item is not target and normalize_phone(item.get("phone_number")) == normalized
            for item in employees
        ):
            return False, "duplicate_phone"
        target["phone_number"] = new_phone
    if first_name is not None:
        target["first_name"] = first_name.strip()
    if last_name is not None:
        target["last_name"] = last_name.strip()
    save_json("employees.json", employees)
    _sync_schedule_employee(old_phone_key, target)
    return True, "ok"


def dismiss_employee(phone_number, protected_user_id=None):
    """Переводит сотрудника в статус dismissed без удаления истории."""
    employees = load_json("employees.json")
    target = next(
        (item for item in employees
         if normalize_phone(item.get("phone_number")) == normalize_phone(phone_number)),
        None
    )
    if target is None:
        return False, "not_found"
    if protected_user_id is not None and target.get("user_id") == protected_user_id:
        return False, "protected"
    target["status"] = "dismissed"
    save_json("employees.json", employees)
    return True, "ok"


def delete_employee_without_history(phone_number, protected_user_id=None):
    """Удаляет сотрудника только при отсутствии связанной истории смен."""
    employees = load_json("employees.json")
    target = next(
        (item for item in employees
         if normalize_phone(item.get("phone_number")) == normalize_phone(phone_number)),
        None
    )
    if target is None:
        return False, "not_found"
    user_id = target.get("user_id")
    if protected_user_id is not None and user_id == protected_user_id:
        return False, "protected"
    if user_id is not None:
        shifts = load_json("shifts.json")
        if any(shift.get("user_id") == user_id for shift in shifts):
            return False, "has_history"
    schedule = _read_schedule_records()
    if schedule is None:
        return False, "schedule_data_invalid"
    employee_id = f"phone:{normalize_phone(target.get('phone_number'))}"
    if any(item.get("employee_id") == employee_id for item in schedule):
        return False, "has_schedule"
    employees.remove(target)
    save_json("employees.json", employees)
    return True, "ok"


def get_invalid_employee_records():
    """Находит ошибочные записи сотрудников и возвращает их исходные индексы."""
    import re
    result = []
    for index, employee in enumerate(get_all_employees()):
        phone = employee.get("phone_number")
        valid = isinstance(phone, str) and re.fullmatch(r"\+?\d{7,15}", phone)
        if not valid and employee.get("user_id") is None:
            name = " ".join(
                part for part in (employee.get("last_name"), employee.get("first_name")) if part
            ) or "Имя не указано"
            result.append({"index": index, "name": name, "phone": str(phone)})
    return result


def delete_invalid_employee_record(index):
    """Удаляет одну выбранную некорректную запись по исходному индексу."""
    import re
    employees = load_json("employees.json")
    if not isinstance(index, int) or not 0 <= index < len(employees):
        return False, "not_found"
    target = employees[index]
    phone = target.get("phone_number")
    valid = isinstance(phone, str) and re.fullmatch(r"\+?\d{7,15}", phone)
    if valid or target.get("user_id") is not None:
        return False, "not_invalid"
    employees.pop(index)
    save_json("employees.json", employees)
    return True, "ok"



# --- Журнал аудита ---
def log_action(actor_user_id, actor_role, action, target_user_id=None, details=None):
    """Добавляет запись о действии пользователя в data/audit_log.json."""
    import logging

    if not isinstance(action, str) or not action.strip():
        return False
    if details is not None and not isinstance(details, dict):
        return False

    filepath = os.path.join(DATA_DIR, "audit_log.json")
    temporary_path = filepath + ".tmp"

    try:
        ensure_data_dir()
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as file:
                entries = json.load(file)
            if not isinstance(entries, list):
                raise ValueError("audit_log.json must contain a JSON array")
        else:
            entries = []

        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "actor_user_id": actor_user_id,
            "actor_role": actor_role or "unknown",
            "action": action.strip(),
            "target_user_id": target_user_id,
            "details": details or {}
        }
        entries.append(entry)

        # Write to a temporary file first so an interrupted write does not truncate the log.
        with open(temporary_path, "w", encoding="utf-8") as file:
            json.dump(entries, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary_path, filepath)
        return True
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logging.getLogger(__name__).exception("Failed to write audit log entry: %s", action)
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        return False


def get_audit_log():
    """Возвращает журнал аудита без изменения файла."""
    filepath = os.path.join(DATA_DIR, "audit_log.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            entries = json.load(file)
        return entries if isinstance(entries, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# --- Фактические рабочие смены ---
def get_active_shift(user_id):
    """Находит открытую смену пользователя."""
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get("user_id") == user_id and shift.get("status") == "active":
            return shift
    return None


def start_shift(user_id, employee_name=None):
    """Создает новую активную смену с текущим временем начала."""
    shifts = load_json("shifts.json")
    shift = {
        "user_id": user_id,
        "shift_start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "shift_end": None,
        "status": "active"
    }
    if employee_name:
        shift["employee_name"] = str(employee_name).strip()
    shifts.append(shift)
    save_json("shifts.json", shifts)



def end_shift(user_id):
    """Закрывает активную смену пользователя и сохраняет время окончания."""
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get("user_id") == user_id and shift.get("status") == "active":
            shift["shift_end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            shift["status"] = "closed"
            save_json("shifts.json", shifts)
            return True
    return False


# Функции для работы с графиком
# --- Проверка и обслуживание расписания ---
def _schedule_entry_is_valid(entry):
    """Проверяет обязательные поля и типы записи расписания новой структуры."""
    if not isinstance(entry, dict):
        return False
    required = {
        "id", "date", "employee_id", "user_id", "employee_name",
        "employee_phone", "start_time", "end_time",
        "created_by_user_id", "created_by_role", "created_at"
    }
    if not required.issubset(entry):
        return False
    if not isinstance(entry.get("id"), str) or not entry["id"]:
        return False
    employee_id = entry.get("employee_id")
    if not isinstance(employee_id, str) or not employee_id:
        return False
    if not isinstance(entry.get("employee_name"), str) or not entry["employee_name"].strip():
        return False
    if entry.get("user_id") is not None and not isinstance(entry["user_id"], int):
        return False
    phone = entry.get("employee_phone")
    if employee_id.startswith("phone:"):
        if not isinstance(phone, str) or not re.fullmatch(r"\+?\d{7,15}", phone):
            return False
        if employee_id != f"phone:{normalize_phone(phone)}":
            return False
    elif re.fullmatch(r"owner:\d+", employee_id):
        if not isinstance(entry.get("user_id"), int):
            return False
        if employee_id != f"owner:{entry['user_id']}" or phone is not None:
            return False
    else:
        return False
    if not isinstance(entry.get("created_by_user_id"), int) or entry["created_by_user_id"] <= 0:
        return False
    if not isinstance(entry.get("created_at"), str) or not entry["created_at"]:
        return False
    if entry.get("created_by_role") not in {"owner", "admin"}:
        return False
    if not _valid_schedule_date(entry.get("date")):
        return False
    start_minutes = schedule_time_to_minutes(entry.get("start_time"), allow_24=False)
    end_minutes = schedule_time_to_minutes(entry.get("end_time"), allow_24=True)
    return start_minutes is not None and end_minutes is not None and start_minutes < end_minutes


def _valid_schedule_date(value):
    """Проверяет строгий формат даты YYYY-MM-DD и существование даты."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def schedule_time_to_minutes(value, allow_24=False):
    """Проверяет время HH:MM и переводит его в минуты от начала суток."""
    if not isinstance(value, str):
        return None
    if value == "24:00":
        return 24 * 60 if allow_24 else None
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        return None
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _read_schedule_records():
    """Читает расписание и отклоняет файл, если записи не соответствуют новой структуре."""
    filepath = os.path.join(DATA_DIR, "schedule.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            schedule = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(schedule, list):
        return None
    if any(not _schedule_entry_is_valid(item) for item in schedule):
        return None
    identifiers = [item["id"] for item in schedule]
    if len(identifiers) != len(set(identifiers)):
        return None
    return schedule



def get_invalid_schedule_records(actor_role="owner", owner_user_id=None):
    """Возвращает некорректные записи расписания с исходными индексами."""
    filepath = os.path.join(DATA_DIR, "schedule.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            schedule = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(schedule, list):
        return None

    result = []
    for index, item in enumerate(schedule):
        if _schedule_entry_is_valid(item):
            continue
        if actor_role != "owner" and isinstance(item, dict):
            is_owner_record = (
                item.get("user_id") == owner_user_id
                or item.get("employee_id") == f"owner:{owner_user_id}"
            )
            if is_owner_record:
                continue
        if isinstance(item, dict):
            name = item.get("employee_name") or "Имя не указано"
            date_value = item.get("date") or "дата не указана"
            start = item.get("start_time") or "?"
            end = item.get("end_time") or "?"
        else:
            name, date_value, start, end = "Некорректная запись", "—", "?", "?"
        result.append({
            "index": index,
            "name": str(name),
            "date": str(date_value),
            "start_time": str(start),
            "end_time": str(end)
        })
    return result


def delete_invalid_schedule_records(indices, actor_user_id, actor_role, owner_user_id=None):
    """Удаляет только выбранные ошибочные записи; корректные записи и смены владельца защищены."""
    if not _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
        return False, "forbidden", []
    if not isinstance(indices, (list, tuple)) or not indices:
        return False, "invalid_selection", []

    filepath = os.path.join(DATA_DIR, "schedule.json")
    if not os.path.exists(filepath):
        return False, "not_found", []
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            schedule = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False, "invalid_schedule_file", []
    if not isinstance(schedule, list):
        return False, "invalid_schedule_file", []

    if any(not isinstance(i, int) or isinstance(i, bool) for i in indices):
        return False, "invalid_selection", []
    selected = sorted(set(indices))
    if any(not 0 <= i < len(schedule) for i in selected):
        return False, "not_found", []

    deleted = []
    for index in selected:
        item = schedule[index]
        if _schedule_entry_is_valid(item):
            return False, "not_invalid", []
        if actor_role != "owner" and isinstance(item, dict):
            if (
                item.get("user_id") == owner_user_id
                or item.get("employee_id") == f"owner:{owner_user_id}"
            ):
                return False, "owner_protected", []
        deleted.append(item)

    for index in reversed(selected):
        schedule.pop(index)
    save_json("schedule.json", schedule)
    return True, "ok", deleted

def _schedule_target_is_owner(employee, owner_user_id):
    """Определяет, принадлежит ли запись расписания владельцу."""
    if owner_user_id is None or not isinstance(employee, dict):
        return False
    return (
        employee.get("user_id") == owner_user_id
        or employee.get("employee_id") == f"owner:{owner_user_id}"
    )


def _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
    """Проверяет полномочия инициатора операции с расписанием."""
    if not isinstance(actor_user_id, int) or actor_user_id <= 0 or owner_user_id is None:
        return False
    if actor_role == "owner":
        return actor_user_id == owner_user_id
    if actor_role == "admin" and actor_user_id != owner_user_id:
        actor = get_employee_by_user_id(actor_user_id)
        return bool(
            actor
            and actor.get("status", "active") == "active"
            and actor.get("role") == "admin"
        )
    return False


def _schedule_employee_is_valid(employee, owner_user_id):
    """Проверяет идентификатор, имя, телефон и статус назначаемого сотрудника."""
    if not isinstance(employee, dict):
        return False
    employee_id = employee.get("employee_id")
    employee_name = employee.get("employee_name")
    user_id = employee.get("user_id")
    phone = employee.get("employee_phone")

    if not isinstance(employee_name, str) or not employee_name.strip():
        return False
    if employee_id == f"owner:{owner_user_id}" and owner_user_id is not None:
        return user_id == owner_user_id and phone is None

    if not isinstance(phone, str) or not re.fullmatch(r"\+?\d{7,15}", phone):
        return False
    normalized_phone = normalize_phone(phone)
    if employee_id != f"phone:{normalized_phone}":
        return False

    target = next(
        (item for item in get_all_employees()
         if normalize_phone(item.get("phone_number")) == normalized_phone),
        None
    )
    if target is None or target.get("status", "active") != "active":
        return False
    expected_name = " ".join(
        part for part in (target.get("last_name"), target.get("first_name")) if part
    ) or "Имя не указано"
    return target.get("user_id") == user_id and employee_name == expected_name


def _schedule_has_overlap(schedule, date_value, employee_id, start_time, end_time, exclude_id=None):
    """Проверяет пересечение времени новой смены с другими сменами сотрудника в этот день."""
    start_minutes = schedule_time_to_minutes(start_time, allow_24=False)
    end_minutes = schedule_time_to_minutes(end_time, allow_24=True)
    for item in schedule:
        if item.get("id") == exclude_id:
            continue
        if item.get("date") != date_value or item.get("employee_id") != employee_id:
            continue
        other_start = schedule_time_to_minutes(item.get("start_time"), allow_24=False)
        other_end = schedule_time_to_minutes(item.get("end_time"), allow_24=True)
        if other_start is None or other_end is None:
            continue
        if start_minutes < other_end and other_start < end_minutes:
            return True
    return False


# --- Чтение и изменение расписания ---
def _sync_schedule_employee(old_phone_key, employee):
    """Обновляет данные сотрудника в расписании после изменения его ФИО или телефона."""
    schedule = _read_schedule_records()
    if not schedule:
        return
    old_employee_id = f"phone:{old_phone_key}"
    new_phone = employee.get("phone_number")
    new_employee_id = f"phone:{normalize_phone(new_phone)}"
    employee_name = " ".join(
        part for part in (employee.get("last_name"), employee.get("first_name")) if part
    ) or "Имя не указано"
    changed = False
    for item in schedule:
        if item.get("employee_id") != old_employee_id:
            continue
        item["employee_id"] = new_employee_id
        item["employee_phone"] = new_phone
        item["user_id"] = employee.get("user_id")
        item["employee_name"] = employee_name
        item["updated_at"] = datetime.now().isoformat(timespec="seconds")
        changed = True
    if changed:
        save_json("schedule.json", schedule)


def get_schedule():
    """Возвращает проверенные записи расписания новой структуры."""
    schedule = _read_schedule_records()
    return schedule if schedule is not None else []


def get_schedule_entry(schedule_id):
    """Находит запись расписания по уникальному ID."""
    return next(
        (item for item in get_schedule() if item.get("id") == schedule_id),
        None
    )


def add_to_schedule(date_value, employee, start_time, end_time,
                    actor_user_id, actor_role, owner_user_id=None):
    """Проверяет права, сотрудника, дату и время, затем добавляет смену."""
    if not _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
        return False, "forbidden", None
    if not _valid_schedule_date(date_value):
        return False, "invalid_date", None
    start_minutes = schedule_time_to_minutes(start_time, allow_24=False)
    end_minutes = schedule_time_to_minutes(end_time, allow_24=True)
    if start_minutes is None or end_minutes is None or start_minutes >= end_minutes:
        return False, "invalid_time", None
    if not _schedule_employee_is_valid(employee, owner_user_id):
        return False, "employee_not_available", None
    if actor_role != "owner" and _schedule_target_is_owner(employee, owner_user_id):
        return False, "owner_protected", None

    schedule = _read_schedule_records()
    if schedule is None:
        return False, "invalid_schedule_data", None
    if _schedule_has_overlap(
        schedule, date_value, employee["employee_id"], start_time, end_time
    ):
        return False, "overlap", None

    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": uuid.uuid4().hex,
        "date": date_value,
        "employee_id": employee["employee_id"],
        "user_id": employee.get("user_id"),
        "employee_name": employee["employee_name"].strip(),
        "employee_phone": employee.get("employee_phone"),
        "start_time": start_time,
        "end_time": end_time,
        "created_by_user_id": actor_user_id,
        "created_by_role": actor_role,
        "created_at": now
    }
    schedule.append(entry)
    save_json("schedule.json", schedule)
    return True, "ok", entry


def update_schedule_entry(schedule_id, date_value, employee, start_time, end_time,
                          actor_user_id, actor_role, owner_user_id=None):
    """Проверяет права и ограничения, затем изменяет выбранную смену."""
    if not _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
        return False, "forbidden", None, None
    schedule = _read_schedule_records()
    if schedule is None:
        return False, "invalid_schedule_data", None, None
    current = next((item for item in schedule if item.get("id") == schedule_id), None)
    if current is None:
        return False, "not_found", None, None
    if actor_role != "owner" and _schedule_target_is_owner(current, owner_user_id):
        return False, "owner_protected", current, None
    if actor_role != "owner" and _schedule_target_is_owner(employee, owner_user_id):
        return False, "owner_protected", current, None
    if not _valid_schedule_date(date_value):
        return False, "invalid_date", current, None
    start_minutes = schedule_time_to_minutes(start_time, allow_24=False)
    end_minutes = schedule_time_to_minutes(end_time, allow_24=True)
    if start_minutes is None or end_minutes is None or start_minutes >= end_minutes:
        return False, "invalid_time", current, None
    if not _schedule_employee_is_valid(employee, owner_user_id):
        return False, "employee_not_available", current, None
    if _schedule_has_overlap(
        schedule, date_value, employee["employee_id"], start_time, end_time,
        exclude_id=schedule_id
    ):
        return False, "overlap", current, None

    old_entry = dict(current)
    current.update({
        "date": date_value,
        "employee_id": employee["employee_id"],
        "user_id": employee.get("user_id"),
        "employee_name": employee["employee_name"].strip(),
        "employee_phone": employee.get("employee_phone"),
        "start_time": start_time,
        "end_time": end_time,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "updated_by_user_id": actor_user_id,
        "updated_by_role": actor_role
    })
    save_json("schedule.json", schedule)
    return True, "ok", old_entry, dict(current)


def delete_schedule_entry(schedule_id, actor_user_id, actor_role, owner_user_id=None):
    """Удаляет одну смену после проверки прав и защиты смен владельца."""
    if not _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
        return False, "forbidden", None
    schedule = _read_schedule_records()
    if schedule is None:
        return False, "invalid_schedule_data", None
    current = next((item for item in schedule if item.get("id") == schedule_id), None)
    if current is None:
        return False, "not_found", None
    if actor_role != "owner" and _schedule_target_is_owner(current, owner_user_id):
        return False, "owner_protected", current
    schedule.remove(current)
    save_json("schedule.json", schedule)
    return True, "ok", dict(current)


def delete_schedule_entries(schedule_ids, actor_user_id, actor_role, owner_user_id=None):
    """Атомарно удаляет выбранные смены после проверки всех ID и прав."""
    if not _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
        return False, "forbidden", []
    if not isinstance(schedule_ids, (list, tuple)) or not schedule_ids:
        return False, "invalid_selection", []
    if len(set(schedule_ids)) != len(schedule_ids):
        return False, "invalid_selection", []
    schedule = _read_schedule_records()
    if schedule is None:
        return False, "invalid_schedule_data", []
    by_id = {item.get("id"): item for item in schedule}
    if any(schedule_id not in by_id for schedule_id in schedule_ids):
        return False, "not_found", []
    selected = [by_id[schedule_id] for schedule_id in schedule_ids]
    if actor_role != "owner" and any(_schedule_target_is_owner(item, owner_user_id) for item in selected):
        return False, "owner_protected", []
    selected_ids = set(schedule_ids)
    remaining = [item for item in schedule if item.get("id") not in selected_ids]
    save_json("schedule.json", remaining)
    return True, "ok", [dict(item) for item in selected]



# Функции для отчетов
# --- Выборка смен для расчетов ---
def get_user_shifts(user_id, days=30):
    """Возвращает недавние закрытые смены пользователя и его текущую активную смену."""
    shifts = load_json("shifts.json")
    now = datetime.now()
    cutoff = now - timedelta(days=max(0, int(days)))
    user_shifts = []
    for shift in shifts:
        if shift.get("user_id") != user_id:
            continue
        if shift.get("status") not in ("closed", "active"):
            continue
        try:
            started_at = datetime.strptime(shift["shift_start"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        if cutoff <= started_at <= now:
            user_shifts.append(shift)
    return user_shifts


def get_all_shifts(days=30):
    """Возвращает последние закрытые смены всех пользователей."""
    shifts = load_json("shifts.json")
    recent_shifts = []
    for shift in shifts:
        if shift.get("status") == "closed":
            recent_shifts.append(shift)
    return recent_shifts[-days:]
