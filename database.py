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
    employees = load_json("employees.json")

    # None означает, что сотрудник еще не привязал Telegram; такие записи не конфликтуют.
    for employee in employees:
        if employee.get("phone_number") == phone_number:
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


# Функции для работы со сменами
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
