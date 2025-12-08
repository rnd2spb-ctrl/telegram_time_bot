# utils.py
from datetime import datetime, timedelta
from database import get_schedule, get_user_shifts, get_all_shifts

def calculate_work_time(shifts):
    """Рассчитывает общее время работы из списка смен"""
    total_seconds = 0
    for shift in shifts:
        start = datetime.strptime(shift['shift_start'], '%Y-%m-%d %H:%M:%S')
        end = datetime.strptime(shift['shift_end'], '%Y-%m-%d %H:%M:%S')
        total_seconds += (end - start).total_seconds()
    
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return f"{hours}ч {minutes}м"

def format_schedule_message():
    """Форматирует график смен в читаемое сообщение"""
    schedule = get_schedule()
    if not schedule:
        return "График смен пока не составлен"
    
    # Группируем по датам
    schedule_by_date = {}
    for item in schedule:
        date = item['date']
        if date not in schedule_by_date:
            schedule_by_date[date] = []
        schedule_by_date[date].append(f"{item['employee_name']}: {item['shift_hours']}")
    
    # Форматируем сообщение
    message = "📅 График смен на неделю:\n\n"
    for date in sorted(schedule_by_date.keys()):
        message += f"📅 {date}:\n"
        for shift in schedule_by_date[date]:
            message += f"  • {shift}\n"
        message += "\n"
    
    return message

def generate_weekly_report():
    """Генерирует отчет за неделю"""
    shifts = get_all_shifts(7)  # Последние 7 дней
    return format_report(shifts, "неделю")

def generate_monthly_report():
    """Генерирует отчет за месяц"""
    shifts = get_all_shifts(30)  # Последние 30 дней
    return format_report(shifts, "месяц")

def format_report(shifts, period):
    """Форматирует отчет"""
    from database import get_employee_by_user_id
    
    # Группируем смены по сотрудникам
    user_shifts = {}
    for shift in shifts:
        user_id = shift['user_id']
        if user_id not in user_shifts:
            user_shifts[user_id] = []
        user_shifts[user_id].append(shift)
    
    # Считаем время для каждого сотрудника
    report = f"📊 Отчет за {period}:\n\n"
    for user_id, user_shift_list in user_shifts.items():
        employee = get_employee_by_user_id(user_id)
        if employee:
            total_time = calculate_work_time(user_shift_list)
            report += f"👤 {employee['first_name']} {employee['last_name']}: {total_time}\n"
    
    return report