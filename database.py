# database.py
import json
import os
import re
import uuid
from datetime import datetime

DATA_DIR = "data"


def ensure_data_dir():
    """Создает папку для данных если её нет"""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def load_json(filename):
    """Загружает данные из JSON файла"""
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
    """Сохраняет данные в JSON файл"""
    ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Функции для работы с сотрудниками
def normalize_phone(phone_number):
    """Оставляет в номере только цифры для сравнения разных форматов записи."""
    return "".join(char for char in str(phone_number or "") if char.isdigit())


def get_employee_by_phone(phone_number):
    employees = load_json("employees.json")
    normalized_phone = normalize_phone(phone_number)
    for employee in employees:
        if normalize_phone(employee.get("phone_number")) == normalized_phone:
            return employee
    return None


def get_employee_by_user_id(user_id):
    employees = load_json("employees.json")
    for employee in employees:
        if employee.get("user_id") == user_id:
            return employee
    return None


def get_all_employees():
    """Возвращает все записи сотрудников, включая неактивные."""
    return load_json("employees.json")


def set_employee_role(user_id, role):
    """Меняет роль привязанного активного сотрудника без изменения остальных данных."""
    if role not in {"employee", "admin"}:
        return False, "invalid_role"

    employees = load_json("employees.json")
    employee = next(
        (item for item in employees if item.get("user_id") == user_id),
        None
    )
    if employee is None:
        return False, "not_found"
    if employee.get("status", "active") != "active":
        return False, "inactive"
    if employee.get("role", "employee") == role:
        return False, "unchanged"

    employee["role"] = role
    save_json("employees.json", employees)
    return True, "ok"


def bind_employee_to_user(phone_number, user_id):
    """Безопасно привязывает сотрудника к Telegram ID без перезаписи чужой привязки."""
    employees = load_json("employees.json")
    normalized_phone = normalize_phone(phone_number)
    target = next(
        (employee for employee in employees
         if normalize_phone(employee.get("phone_number")) == normalized_phone),
        None
    )
    if target is None:
        return False, "not_found", None

    if target.get("user_id") not in (None, user_id):
        return False, "phone_already_bound", None

    for employee in employees:
        if employee is not target and employee.get("user_id") == user_id:
            return False, "user_already_bound", None

    target["user_id"] = user_id
    target.setdefault("role", "employee")
    target.setdefault("status", "active")
    target.setdefault("registered_at", datetime.now().isoformat(timespec="seconds"))
    save_json("employees.json", employees)
    _sync_schedule_employee(normalized_phone, target)
    return True, "ok", target


def add_employee(user_id, phone_number, first_name, last_name):
    """Add an employee only with a valid phone number and non-empty name."""
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
    """Обновляет ФИО сотрудника, не меняя роль, статус и историю смен."""
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
    """Увольняет сотрудника, сохраняя запись и историю смен."""
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
    """Удаляет запись только при отсутствии смен; историю никогда не удаляет."""
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
    """Возвращает только ошибочные непривязанные записи."""
    import re
    result = []
    for index, employee in enumerate(get_all_employees()):
        phone = employee.get("phone_number")
        valid = isinstance(phone, str) and re.fullmatch(r"\+?\d{7,15}", phone)
        if not valid and employee.get("user_id") is None and employee.get("is_admin") is not True:
            name = " ".join(
                part for part in (employee.get("last_name"), employee.get("first_name")) if part
            ) or "Имя не указано"
            result.append({"index": index, "name": name, "phone": str(phone)})
    return result


def delete_invalid_employee_record(index):
    """Удаляет только ошибочную непривязанную запись; реальные записи защищены."""
    import re
    employees = load_json("employees.json")
    if not isinstance(index, int) or not 0 <= index < len(employees):
        return False, "not_found"
    target = employees[index]
    phone = target.get("phone_number")
    valid = isinstance(phone, str) and re.fullmatch(r"\+?\d{7,15}", phone)
    if valid or target.get("user_id") is not None or target.get("is_admin") is True:
        return False, "not_invalid"
    employees.pop(index)
    save_json("employees.json", employees)
    return True, "ok"



def log_action(actor_user_id, actor_role, action, target_user_id=None, details=None):
    """Safely append one successful operation to data/audit_log.json."""
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
    """Return audit entries without modifying the file."""
    filepath = os.path.join(DATA_DIR, "audit_log.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            entries = json.load(file)
        return entries if isinstance(entries, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def get_active_shift(user_id):
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get("user_id") == user_id and shift.get("status") == "active":
            return shift
    return None


def start_shift(user_id):
    shifts = load_json("shifts.json")
    shift = {
        "user_id": user_id,
        "shift_start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "shift_end": None,
        "status": "active"
    }
    shifts.append(shift)
    save_json("shifts.json", shifts)


def end_shift(user_id):
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get("user_id") == user_id and shift.get("status") == "active":
            shift["shift_end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            shift["status"] = "closed"
            save_json("shifts.json", shifts)
            return True
    return False


# Функции для работы с графиком
def _schedule_entry_is_valid(entry):
    """Проверяет запись расписания только по новой строгой структуре."""
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
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def schedule_time_to_minutes(value, allow_24=False):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):00|24:00", value):
        return None
    if value == "24:00" and not allow_24:
        return None
    if value == "24:00":
        return 24 * 60
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _read_schedule_records():
    """Читает только записи новой структуры; старый формат не преобразуется."""
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
    """Возвращает ошибочные записи расписания с исходными индексами; файл не меняет."""
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
    """Удаляет выбранные ошибочные записи; валидные и смены владельца защищены."""
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
    if owner_user_id is None or not isinstance(employee, dict):
        return False
    return (
        employee.get("user_id") == owner_user_id
        or employee.get("employee_id") == f"owner:{owner_user_id}"
    )


def _schedule_actor_is_valid(actor_user_id, actor_role, owner_user_id):
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


def _sync_schedule_employee(old_phone_key, employee):
    """Сохраняет связь расписания с сотрудником при изменении его номера/ФИО."""
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
    """Возвращает записи только нового формата; старые записи не интерпретируются."""
    schedule = _read_schedule_records()
    return schedule if schedule is not None else []


def get_schedule_entry(schedule_id):
    return next(
        (item for item in get_schedule() if item.get("id") == schedule_id),
        None
    )


def add_to_schedule(date_value, employee, start_time, end_time,
                    actor_user_id, actor_role, owner_user_id=None):
    """Добавляет проверенную смену в расписание."""
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
    """Изменяет назначение/время записи с повторной проверкой прав и пересечений."""
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
    """Удаляет запись расписания; администратору запрещено менять смены владельца."""
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


# Функции для отчетов
def get_user_shifts(user_id, days=30):
    shifts = load_json("shifts.json")
    user_shifts = []
    for shift in shifts:
        if shift.get("user_id") == user_id and shift.get("status") == "closed":
            user_shifts.append(shift)
    return user_shifts[-days:]


def get_all_shifts(days=30):
    shifts = load_json("shifts.json")
    recent_shifts = []
    for shift in shifts:
        if shift.get("status") == "closed":
            recent_shifts.append(shift)
    return recent_shifts[-days:]
