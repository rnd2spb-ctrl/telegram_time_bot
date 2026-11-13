"""Регрессионные тесты исправлений авторизации, учета времени и отчетов."""

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bot
import config
import database
import utils
from test_admin_schedule_features import FakeContext, FakeUpdate


class FollowupFixTests(unittest.TestCase):
    """Регрессионные проверки исправлений авторизации, смен, времени и отчетов."""
    def test_first_unrecognized_message_starts_onboarding_and_next_phone_is_processed(self):
        """Проверяет запуск идентификации и обработку следующего сообщения с телефоном."""
        context = FakeContext()
        first = FakeUpdate("Привет", 999)
        with patch.object(bot, "get_employee_by_user_id", return_value=None), \
             patch.object(bot, "get_user_role", return_value=None):
            asyncio.run(bot.button_handler(first, context))
        self.assertTrue(context.user_data["awaiting_phone"])
        self.assertIn("Поделитесь", first.message.replies[-1][0])

        second = FakeUpdate("+79990000099", 999)
        with patch.object(bot, "get_employee_by_user_id", return_value=None), \
             patch.object(bot, "get_user_role", return_value=None), \
             patch.object(bot, "complete_phone_identification", new=AsyncMock()) as bind:
            asyncio.run(bot.button_handler(second, context))
        bind.assert_awaited_once_with(second, "+79990000099")

    def test_role_change_list_includes_unbound_and_linked_records(self):
        """Проверяет список сотрудников с привязанным и непривязанным Telegram."""
        owner_id = 100
        records = [
            {"user_id": 100, "phone_number": "+79990000001", "role": "owner"},
            {"user_id": None, "phone_number": "+79990000002", "last_name": "Unbound", "role": "employee", "status": "active"},
            {"user_id": 201, "phone_number": "+79990000003", "last_name": "Bound", "role": "admin", "status": "active"},
        ]
        update = FakeUpdate("Назначить/снять администратора", owner_id)
        context = FakeContext()
        with patch.object(bot, "get_user_role", return_value="owner"), patch.object(bot, "get_all_employees", return_value=records):
            state = asyncio.run(bot.button_handler(update, context))
        self.assertEqual(state, bot.EDIT_EMPLOYEE_SELECT)
        self.assertEqual(context.user_data["editable_employee_phones"], ["+79990000002", "+79990000003"])
        self.assertIn("Unbound >", update.message.replies[-1][0])
        self.assertIn("Bound (админ) >", update.message.replies[-1][0])

    def test_unbound_employee_role_can_be_changed_by_phone(self):
        """Проверяет изменение роли сотрудника без привязки Telegram."""
        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder):
            database.save_json("employees.json", [{"user_id": None, "phone_number": "+79990000002", "role": "employee", "status": "active"}])
            changed, reason = database.set_employee_role_by_phone("+79990000002", "admin", protected_user_id=100)
            self.assertTrue(changed)
            self.assertEqual(reason, "ok")
            self.assertEqual(database.get_employee_by_phone("+79990000002")["role"], "admin")

    def test_owner_can_start_shift_without_employee_record(self):
        """Проверяет начало смены владельцем без записи в списке сотрудников."""
        owner_id = 100
        update = FakeUpdate("Начать смену", owner_id)
        with patch.object(bot, "get_employee_by_user_id", return_value=None), \
             patch.object(bot, "get_user_role", return_value="owner"), \
             patch.object(bot.config, "OWNER_ID", owner_id), \
             patch.object(bot, "get_active_shift", return_value=None), \
             patch.object(bot, "start_shift") as start_shift, \
             patch.object(bot, "audit_action"):
            asyncio.run(bot.button_handler(update, FakeContext()))
        start_shift.assert_called_once()
        self.assertEqual(start_shift.call_args.args[0], owner_id)

    def test_owner_can_view_time_and_salary_without_employee_record(self):
        """Проверяет расчет времени и оплаты владельца без записи сотрудника."""
        owner_id = 100
        update = FakeUpdate("Мое время", owner_id)
        shifts = [{"user_id": owner_id, "shift_start": "2026-11-13 09:00:00", "shift_end": "2026-11-13 16:15:00", "status": "closed"}]
        with patch.object(bot, "get_employee_by_user_id", return_value=None), \
             patch.object(bot, "get_user_role", return_value="owner"), \
             patch.object(bot.config, "OWNER_ID", owner_id), \
             patch.object(bot, "get_user_shifts", return_value=shifts), \
             patch.object(bot, "calculate_work_time", return_value="7ч 15м"), \
             patch.object(bot, "calculate_salary", return_value=1211):
            asyncio.run(bot.button_handler(update, FakeContext()))
        self.assertIn("7ч 15м", update.message.replies[-1][0])
        self.assertIn("1211 руб.", update.message.replies[-1][0])

    def test_schedule_time_normalizer_accepts_hours_and_minutes(self):
        """Проверяет ввод целых часов и времени с минутами через двоеточие и дефис."""
        self.assertEqual(bot.normalize_schedule_time("9"), "09:00")
        self.assertEqual(bot.normalize_schedule_time("15"), "15:00")
        self.assertEqual(bot.normalize_schedule_time("18:00"), "18:00")
        self.assertEqual(bot.normalize_schedule_time("9:30"), "09:30")
        self.assertEqual(bot.normalize_schedule_time("9-30"), "09:30")
        self.assertEqual(bot.normalize_schedule_time("15-45"), "15:45")
        self.assertEqual(bot.normalize_schedule_time("24-00", allow_24=True), "24:00")
        self.assertIsNone(bot.normalize_schedule_time("24-30", allow_24=True))
        self.assertEqual(bot.normalize_schedule_time("24", allow_24=True), "24:00")
        self.assertIsNone(bot.normalize_schedule_time("24"))
        self.assertIsNone(bot.normalize_schedule_time("25:00", allow_24=True))

    def test_schedule_edit_date_accepts_day_ranges_like_add(self):
        """Проверяет разбор диапазонов дней при изменении смены."""
        class FixedDateTime(datetime):
            """Фиксирует дату и время для проверки расчетов."""
            @classmethod
            def now(cls, tz=None):
                """Возвращает заданное тестом значение текущей даты и времени."""
                return cls(2026, 11, 13, 12, 0)
        update = FakeUpdate("13", 200)
        context = FakeContext({"schedule_action": "edit"})
        with patch.object(bot, "datetime", FixedDateTime), \
             patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "_schedule_employee_options", return_value=[{"employee_id": "phone:79990000003", "user_id": 201, "employee_name": "Ivanov Ivan", "employee_phone": "+79990000003", "role": "employee"}]):
            state = asyncio.run(bot.schedule_date(update, context))
        self.assertEqual(state, bot.GET_SCHEDULE_EMPLOYEE)
        self.assertEqual(context.user_data["schedule_dates"], ["2026-11-13"])

    def test_cancel_returns_to_parent_employee_menu(self):
        """Проверяет возврат из диалога в меню сотрудников."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "employees"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][0], "Управление сотрудниками. Выберите действие:")

    def test_cancel_returns_to_parent_schedule_menu(self):
        """Проверяет возврат из диалога в меню расписания."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "schedule"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][0], "Управление расписанием. Выберите действие:")

    def test_cancel_from_top_level_submenu_returns_to_main_menu(self):
        """Проверяет возврат из корневого подменю в главное меню."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "main"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][0], "Главное меню:")

    def test_schedule_minutes_are_validated_in_database(self):
        """Проверяет проверку времени смены на уровне базы данных."""
        self.assertEqual(database.schedule_time_to_minutes("09:30"), 570)
        self.assertEqual(database.schedule_time_to_minutes("18:15"), 1095)
        self.assertIsNone(database.schedule_time_to_minutes("24:30", allow_24=True))

    def test_reports_include_individual_and_total_salary(self):
        """Проверяет личную оплату сотрудников и общий итог в отчетах."""
        shifts = [
            {"user_id": 201, "shift_start": "2026-11-13 09:00:00", "shift_end": "2026-11-13 16:15:00", "status": "closed"},
            {"user_id": 202, "shift_start": "2026-11-13 09:00:00", "shift_end": "2026-11-13 17:00:00", "status": "closed"},
        ]
        employees = {
            201: {"first_name": "Ivan", "last_name": "Ivanov"},
            202: {"first_name": "Petr", "last_name": "Petrov"},
        }
        with patch.object(utils, "get_all_shifts", return_value=shifts), \
             patch.object(database, "get_employee_by_user_id", side_effect=lambda uid: employees.get(uid)), \
             patch.object(config, "HOURLY_RATE", __import__('decimal').Decimal('167')):
            from utils import generate_weekly_report
            report = generate_weekly_report(today=__import__('datetime').date(2026, 11, 13))
        self.assertIn("Ivan Ivanov: 7\u0447 15\u043c \u0421\u0443\u043c\u043c\u0430: 1211 \u0440\u0443\u0431.", report)
        self.assertIn("Petr Petrov: 8\u0447 0\u043c \u0421\u0443\u043c\u043c\u0430: 1336 \u0440\u0443\u0431.", report)
        self.assertIn("\u0418\u0442\u043e\u0433\u043e \u043e\u0431\u0449\u0430\u044f \u0441\u0443\u043c\u043c\u0430: 2547 \u0440\u0443\u0431.", report)


if __name__ == "__main__":
    unittest.main()
