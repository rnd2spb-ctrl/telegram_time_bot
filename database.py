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
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_json(filename, data):
    """Сохраняет данные в JSON файл"""
    ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Функции для работы с сотрудниками
def get_employee_by_phone(phone_number):
    employees = load_json("employees.json")
    for employee in employees:
        if employee.get('phone_number') == phone_number:
            return employee
    return None

def get_employee_by_user_id(user_id):
    employees = load_json("employees.json")
    for employee in employees:
        if employee.get('user_id') == user_id:
            return employee
    return None

def add_employee(user_id, phone_number, first_name, last_name, is_admin=False):
    employees = load_json("employees.json")
    
    # Проверяем нет ли уже такого сотрудника
    for emp in employees:
        if emp.get('user_id') == user_id or emp.get('phone_number') == phone_number:
            return False
            
    employee = {
        'user_id': user_id,
        'phone_number': phone_number,
        'first_name': first_name,
        'last_name': last_name,
        'is_admin': is_admin
    }
    employees.append(employee)
    save_json("employees.json", employees)
    return True

# Функции для работы со сменами
def get_active_shift(user_id):
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get('user_id') == user_id and shift.get('status') == 'active':
            return shift
    return None

def start_shift(user_id):
    shifts = load_json("shifts.json")
    shift = {
        'user_id': user_id,
        'shift_start': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'shift_end': None,
        'status': 'active'
    }
    shifts.append(shift)
    save_json("shifts.json", shifts)

def end_shift(user_id):
    shifts = load_json("shifts.json")
    for shift in shifts:
        if shift.get('user_id') == user_id and shift.get('status') == 'active':
            shift['shift_end'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            shift['status'] = 'closed'
            save_json("shifts.json", shifts)
            return True
    return False

# Функции для работы с графиком
def get_schedule():
    return load_json("schedule.json")

def add_to_schedule(date, employee_name, shift_hours):
    schedule = load_json("schedule.json")
    schedule.append({
        'date': date,
        'employee_name': employee_name,
        'shift_hours': shift_hours
    })
    save_json("schedule.json", schedule)

# Функции для отчетов
def get_user_shifts(user_id, days=30):
    shifts = load_json("shifts.json")
    user_shifts = []
    for shift in shifts:
        if shift.get('user_id') == user_id and shift.get('status') == 'closed':
            user_shifts.append(shift)
    return user_shifts[-days:]  # Последние N дней

def get_all_shifts(days=30):
    shifts = load_json("shifts.json")
    recent_shifts = []
    for shift in shifts:
        if shift.get('status') == 'closed':
            recent_shifts.append(shift)
    return recent_shifts[-days:]