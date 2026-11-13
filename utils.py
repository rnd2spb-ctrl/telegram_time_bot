"""Расчеты рабочего времени и зарплаты, форматирование расписания и отчетов."""

# utils.py
from datetime import date, datetime, timedelta

from database import get_schedule, get_all_shifts


def work_time_seconds(shifts):
    """Суммирует длительность закрытых смен и время текущей открытой смены."""
    total_seconds = 0
    now = datetime.now()
    for shift in shifts:
        try:
            start = datetime.strptime(shift["shift_start"], "%Y-%m-%d %H:%M:%S")
            if shift.get("status") == "active" and not shift.get("shift_end"):
                end = now
            else:
                end = datetime.strptime(shift["shift_end"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        total_seconds += max(0, (end - start).total_seconds())
    return int(total_seconds)


def calculate_work_time(shifts):
    """Преобразует общее время работы в часы и минуты."""
    total_seconds = work_time_seconds(shifts)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}\u0447 {minutes}\u043c"


def calculate_salary(shifts):
    """Рассчитывает оплату по фактическим секундам и ставке из конфигурации."""
    from decimal import Decimal, ROUND_HALF_UP
    import config

    rate = Decimal(str(config.HOURLY_RATE))
    amount = (Decimal(work_time_seconds(shifts)) * rate / Decimal(3600)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(amount)


def format_schedule_message():
    """Группирует и форматирует корректные смены по датам для показа в Telegram."""
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
    """Выбирает закрытые смены за период от start_date включительно до end_date не включительно."""
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
    """Формирует отчет по закрытым сменам за календарную неделю с понедельника."""
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
    """Формирует отчет по закрытым сменам за текущий календарный месяц."""
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
    """Группирует смены по сотрудникам и рассчитывает личные и общие суммы."""
    from collections import defaultdict
    from database import get_employee_by_user_id

    user_shifts = defaultdict(list)
    for shift in shifts:
        user_shifts[shift.get("user_id")].append(shift)

    report = f"\U0001F4CA Отчет за {period}:\n\n"
    total_salary = 0
    for user_id, employee_shifts in sorted(
        user_shifts.items(), key=lambda item: str(item[0])
    ):
        employee = get_employee_by_user_id(user_id) if user_id is not None else None
        if employee:
            name = " ".join(
                part for part in (employee.get("first_name"), employee.get("last_name"))
                if part
            ) or f"ID {user_id}"
        elif employee_shifts[0].get("employee_name"):
            name = employee_shifts[0]["employee_name"]
        else:
            name = f"Неизвестный сотрудник (ID {user_id})"
        salary = calculate_salary(employee_shifts)
        total_salary += salary
        report += f"\U0001F464 {name}: {calculate_work_time(employee_shifts)} Сумма: {salary} руб.\n\n"

    if not user_shifts:
        report += "За выбранный период закрытых смен нет.\n\n"
    report += f"Итого общая сумма: {total_salary} руб."
    return report.rstrip()
