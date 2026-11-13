# utils.py
from datetime import date, datetime, timedelta

from database import get_schedule, get_all_shifts


def calculate_work_time(shifts):
    """Рассчитывает общее время работы из списка закрытых смен."""
    total_seconds = 0
    for shift in shifts:
        try:
            start = datetime.strptime(shift["shift_start"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(shift["shift_end"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        total_seconds += max(0, (end - start).total_seconds())

    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return f"{hours}ч {minutes}м"


def format_schedule_message():
    """Форматирует список смен новой структуры в читаемое сообщение."""
    schedule = get_schedule()
    if not schedule:
        return "График смен пока не составлен"

    schedule_by_date = {}
    for item in schedule:
        try:
            date_value = datetime.strptime(item["date"], "%Y-%m-%d")
            date_label = date_value.strftime("%d.%m.%Y")
            shift_label = (
                f"{item['employee_name']}: "
                f"{item['start_time']}–{item['end_time']}"
            )
        except (KeyError, TypeError, ValueError):
            continue
        schedule_by_date.setdefault(date_label, []).append(
            (item["start_time"], shift_label)
        )

    if not schedule_by_date:
        return "В расписании пока нет корректных смен."

    message = "📅 График смен:\n\n"
    for date_label in sorted(
        schedule_by_date,
        key=lambda value: datetime.strptime(value, "%d.%m.%Y")
    ):
        message += f"📅 {date_label}:\n"
        for _, shift_label in sorted(schedule_by_date[date_label]):
            message += f"  • {shift_label}\n"
        message += "\n"

    return message.rstrip()


def _closed_shifts_between(start_date, end_date):
    """Возвращает закрытые смены за период [start_date, end_date)."""
    shifts = get_all_shifts(days=10**9)
    result = []
    for shift in shifts:
        if shift.get("status") != "closed":
            continue
        try:
            shift_date = datetime.strptime(
                shift["shift_start"], "%Y-%m-%d %H:%M:%S"
            ).date()
        except (KeyError, TypeError, ValueError):
            continue
        if start_date <= shift_date < end_date:
            result.append(shift)
    return result


def generate_weekly_report(today=None):
    """Генерирует отчет за текущую календарную неделю (с понедельника)."""
    today = today or date.today()
    start_date = today - timedelta(days=today.weekday())
    end_date = start_date + timedelta(days=7)
    shifts = _closed_shifts_between(start_date, end_date)
    period = (
        f"неделю ({start_date.strftime('%d.%m.%Y')}–"
        f"{(end_date - timedelta(days=1)).strftime('%d.%m.%Y')})"
    )
    return format_report(shifts, period)


def generate_monthly_report(today=None):
    """Генерирует отчет за текущий календарный месяц."""
    today = today or date.today()
    start_date = today.replace(day=1)
    if start_date.month == 12:
        end_date = date(start_date.year + 1, 1, 1)
    else:
        end_date = date(start_date.year, start_date.month + 1, 1)
    shifts = _closed_shifts_between(start_date, end_date)
    period = f"месяц ({start_date.strftime('%m.%Y')})"
    return format_report(shifts, period)


def format_report(shifts, period):
    """Группирует закрытые смены и считает время сотрудников."""
    if not shifts:
        return f"📊 Отчет за {period}:\n\nЗа выбранный период закрытых смен нет."

    from database import get_employee_by_user_id

    user_shifts = {}
    for shift in shifts:
        user_id = shift.get("user_id")
        user_shifts.setdefault(user_id, []).append(shift)

    report = f"📊 Отчет за {period}:\n\n"
    for user_id, employee_shifts in user_shifts.items():
        employee = get_employee_by_user_id(user_id) if user_id is not None else None
        if employee:
            name = " ".join(
                part for part in (
                    employee.get("first_name"), employee.get("last_name")
                ) if part
            ) or f"ID {user_id}"
        else:
            name = f"Неизвестный сотрудник (ID {user_id})"
        report += f"👤 {name}: {calculate_work_time(employee_shifts)}\n"
    return report.rstrip()
