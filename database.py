# database.py
import json
import os
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
def get_schedule():
    return load_json("schedule.json")


def add_to_schedule(date, employee_name, shift_hours):
    schedule = load_json("schedule.json")
    schedule.append({
        "date": date,
        "employee_name": employee_name,
        "shift_hours": shift_hours
    })
    save_json("schedule.json", schedule)


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
