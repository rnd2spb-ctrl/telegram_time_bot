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
    def test_unrecognized_user_needs_invite_and_phone_text_gives_no_access(self):
        """Проверяет, что неопознанный пользователь получает просьбу о ссылке, а номер текстом доступа не даёт."""
        context = FakeContext()
        for text in ("Привет", "+79990000099"):
            update = FakeUpdate(text, 999)
            with patch.object(bot, "get_employee_by_user_id", return_value=None), \
                 patch.object(bot, "get_user_role", return_value=None), \
                 patch.object(bot, "redeem_invite") as redeem:
                asyncio.run(bot.button_handler(update, context))
            self.assertIn("ссылка-приглашение", update.message.replies[-1][0])
            redeem.assert_not_called()


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


    def test_my_time_is_zero_until_a_shift_is_started(self):
        """No shift record means bot session time is not counted as work time."""
        owner_id = 100
        update = FakeUpdate("\u041c\u043e\u0435 \u0432\u0440\u0435\u043c\u044f", owner_id)
        with patch.object(bot, "get_employee_by_user_id", return_value=None), \
             patch.object(bot, "get_user_role", return_value="owner"), \
             patch.object(bot.config, "OWNER_ID", owner_id), \
             patch.object(bot, "get_user_shifts", return_value=[]), \
             patch.object(bot, "get_active_shift", return_value=None):
            asyncio.run(bot.button_handler(update, FakeContext()))

        message = update.message.replies[-1][0]
        self.assertIn("0\u0447 0\u043c", message)
        self.assertIn("\u0410\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u0441\u043c\u0435\u043d\u044b \u043d\u0435\u0442", message)


    def test_shift_auto_closes_at_2201_with_2100_end_time(self):
        """Before 22:01 a shift stays open; at 22:01 it closes at 21:00."""
        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder):
            database.save_json("shifts.json", [{
                "user_id": 201,
                "shift_start": "2026-11-15 09:00:00",
                "shift_end": None,
                "status": "active",
                "employee_name": "Test Employee",
            }])

            before_cutoff = datetime(2026, 11, 15, 22, 0, 59)
            self.assertEqual(database.auto_close_overdue_shifts(now=before_cutoff), [])
            self.assertEqual(database.load_json("shifts.json")[0]["status"], "active")

            at_cutoff = datetime(2026, 11, 15, 22, 1, 0)
            # Use the explicit time for the closure decision and inspect persisted data.
            closed = database.auto_close_overdue_shifts(now=at_cutoff)
            self.assertEqual(len(closed), 1)
            saved = database.load_json("shifts.json")[0]
            self.assertEqual(saved["status"], "closed")
            self.assertEqual(saved["shift_end"], "2026-11-15 21:00:00")
            self.assertTrue(saved["auto_closed"])
            self.assertIsNone(database.get_active_shift(201))

            audit = database.get_audit_log()
            self.assertEqual(audit[-1]["action"], "shift_auto_closed")
            self.assertEqual(audit[-1]["target_user_id"], 201)
            self.assertEqual(audit[-1]["actor_role"], "system")

    def test_overdue_shift_from_previous_day_is_recovered_at_2100(self):
        """A shift left open overnight is backdated to 21:00 on its start date."""
        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder):
            database.save_json("shifts.json", [{
                "user_id": 202,
                "shift_start": "2026-11-14 09:15:00",
                "shift_end": None,
                "status": "active",
            }])
            now = datetime(2026, 11, 15, 16, 0, 0)
            closed = database.auto_close_overdue_shifts(now=now)

            self.assertEqual(len(closed), 1)
            saved = database.load_json("shifts.json")[0]
            self.assertEqual(saved["shift_end"], "2026-11-14 21:00:00")
            self.assertEqual(saved["status"], "closed")
            self.assertEqual(utils.work_time_seconds([saved]), 42300)


    def test_shift_started_after_pvz_close_gets_zero_recorded_duration(self):
        """A shift started after 21:00 still follows the fixed 21:00 auto-close rule."""
        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder):
            database.save_json("shifts.json", [{
                "user_id": 205,
                "shift_start": "2026-11-14 22:26:34",
                "shift_end": None,
                "status": "active",
            }])
            closed = database.auto_close_overdue_shifts(
                now=datetime(2026, 11, 15, 10, 0, 0)
            )

            self.assertEqual(len(closed), 1)
            saved = database.load_json("shifts.json")[0]
            self.assertEqual(saved["shift_end"], "2026-11-14 21:00:00")
            self.assertEqual(utils.work_time_seconds([saved]), 0)

    def test_manual_end_before_2201_uses_actual_end_time(self):
        """A manual end before the cutoff keeps the actual button-press time."""
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 15, 18, 42, 30)

        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder), \
             patch.object(database, "datetime", FixedDateTime):
            database.save_json("shifts.json", [{
                "user_id": 203,
                "shift_start": "2026-11-15 09:00:00",
                "shift_end": None,
                "status": "active",
            }])

            self.assertTrue(database.end_shift(203))
            saved = database.load_json("shifts.json")[0]
            self.assertEqual(saved["status"], "closed")
            self.assertEqual(saved["shift_end"], "2026-11-15 18:42:30")
            self.assertNotIn("auto_closed", saved)

    def test_manual_end_after_2201_uses_automatic_2100_cutoff(self):
        """Pressing End after 22:01 cannot replace the scheduled 21:00 close."""
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 15, 22, 1, 5)

        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DATA_DIR", folder), \
             patch.object(database, "datetime", FixedDateTime):
            database.save_json("shifts.json", [{
                "user_id": 204,
                "shift_start": "2026-11-15 09:00:00",
                "shift_end": None,
                "status": "active",
            }])

            self.assertFalse(database.end_shift(204))
            saved = database.load_json("shifts.json")[0]
            self.assertEqual(saved["status"], "closed")
            self.assertEqual(saved["shift_end"], "2026-11-15 21:00:00")
            self.assertTrue(saved["auto_closed"])

    def test_delete_schedule_employee_list_uses_blank_lines(self):
        """The delete-shift employee list separates its title and item by a blank line."""
        entry = {
            "id": "delete-entry-1",
            "date": "2026-11-15",
            "employee_id": "phone:79043405112",
            "user_id": 201,
            "employee_name": "\u0410\u0440\u0442\u0435\u043c \u0410\u0440\u0442\u0435\u043c",
            "employee_phone": "+79043405112",
            "start_time": "15:00",
            "end_time": "21:00",
        }
        employee = {"user_id": 201, "phone_number": "+79043405112", "role": "admin", "status": "active"}

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 15, 12, 0)

        update = FakeUpdate("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443", 200)
        context = FakeContext()
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "_manageable_schedule_entries", return_value=[entry]), \
             patch.object(bot, "get_all_employees", return_value=[employee]), \
             patch.object(bot, "datetime", FixedDateTime), \
             patch.object(bot.config, "OWNER_ID", 100):
            state = asyncio.run(bot.schedule_menu_action(update, context))

        self.assertEqual(state, bot.GET_SCHEDULE_EMPLOYEE)
        self.assertEqual(
            update.message.replies[-1][0],
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u0430, \u0447\u044c\u0438 \u0441\u043c\u0435\u043d\u044b \u043d\u0443\u0436\u043d\u043e \u0443\u0434\u0430\u043b\u0438\u0442\u044c:\n\n"
            "1. \u0410\u0440\u0442\u0435\u043c \u0410\u0440\u0442\u0435\u043c (\u0430\u0434\u043c\u0438\u043d) \u2014 +79043405112",
        )


    def test_edit_schedule_list_uses_blank_lines_between_entries(self):
        """The edit-shift list separates its title and every shift with blank lines."""
        entries = [
            {
                "id": "edit-entry-1",
                "date": "2026-11-15",
                "employee_id": "phone:79043405112",
                "user_id": 201,
                "employee_name": "\u0410\u0440\u0442\u0435\u043c",
                "employee_phone": "+79043405112",
                "start_time": "15:00",
                "end_time": "21:00",
            },
            {
                "id": "edit-entry-2",
                "date": "2026-11-16",
                "employee_id": "phone:79043405113",
                "user_id": 202,
                "employee_name": "\u0421\u0443\u0445\u043e\u0432\u0435\u0435\u0432",
                "employee_phone": "+79043405113",
                "start_time": "09:00",
                "end_time": "21:00",
            },
        ]
        labels = ["\u0410\u0440\u0442\u0435\u043c (\u0430\u0434\u043c\u0438\u043d) >", "\u0421\u0443\u0445\u043e\u0432\u0435\u0435\u0432 (\u0430\u0434\u043c\u0438\u043d) >"]

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 15, 12, 0)

        update = FakeUpdate("\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0441\u043c\u0435\u043d\u0443", 200)
        context = FakeContext()
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "_manageable_schedule_entries", return_value=entries), \
             patch.object(bot, "schedule_entry_prompt_label", side_effect=labels), \
             patch.object(bot, "datetime", FixedDateTime), \
             patch.object(bot.config, "OWNER_ID", 100):
            state = asyncio.run(bot.schedule_menu_action(update, context))

        self.assertEqual(state, bot.SCHEDULE_SELECT_ENTRY)
        self.assertEqual(
            update.message.replies[-1][0],
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043c\u0435\u043d\u0443, \u0432\u0432\u0435\u0434\u044f \u0435\u0451 \u043d\u043e\u043c\u0435\u0440:\n\n"
            "1. \u0410\u0440\u0442\u0435\u043c (\u0430\u0434\u043c\u0438\u043d) > \u2014 2026-11-15 \u2014 15:00\u201321:00\n\n"
            "2. \u0421\u0443\u0445\u043e\u0432\u0435\u0435\u0432 (\u0430\u0434\u043c\u0438\u043d) > \u2014 2026-11-16 \u2014 09:00\u201321:00",
        )


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
        """Проверяет возврат из диалога в меню сотрудников и сброс родительского меню."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "employees"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][0], "Управление сотрудниками. Выберите действие:")
        self.assertEqual(context.user_data["menu_parent"], "main")

    def test_two_consecutive_back_presses_return_to_main_menu(self):
        """Проверяет два последовательных нажатия «Назад» из подраздела сотрудников."""
        context = FakeContext({"menu_parent": "employees"})
        first_update = FakeUpdate("⬅️ Назад в меню", 200)
        second_update = FakeUpdate("⬅️ Назад в меню", 200)

        with patch.object(bot, "get_user_role", return_value="admin"), patch.object(bot, "get_employee_by_user_id", return_value=None):
            # Первое нажатие обрабатывается fallback-функцией активного диалога.
            asyncio.run(bot.cancel(first_update, context))
            self.assertEqual(
                first_update.message.replies[-1][0],
                "Управление сотрудниками. Выберите действие:"
            )

            # Диалог уже завершен; второе нажатие попадает в общий обработчик кнопок.
            asyncio.run(bot.button_handler(second_update, context))

        self.assertEqual(second_update.message.replies[-1][0], "Test >")
        self.assertEqual(context.user_data["menu_parent"], "main")

    def test_cancel_returns_to_parent_schedule_menu(self):
        """Проверяет возврат из диалога в меню расписания."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "schedule"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][0], "Управление расписанием. Выберите действие:")


    def test_schedule_back_resets_parent_and_second_back_goes_to_main(self):
        """Back from an interrupted schedule operation returns to main after two presses."""
        context = FakeContext({"menu_parent": "schedule", "schedule_action": "add"})
        first_update = FakeUpdate("\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434 \u0432 \u043c\u0435\u043d\u044e", 200)
        second_update = FakeUpdate("\u2b05\ufe0f \u041d\u0430\u0437\u0430\u0434 \u0432 \u043c\u0435\u043d\u044e", 200)

        with patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "get_employee_by_user_id", return_value=None):
            # First press is handled by the active conversation fallback.
            asyncio.run(bot.cancel(first_update, context))
            self.assertEqual(first_update.message.replies[-1][0], "\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435:")
            self.assertEqual(context.user_data["menu_parent"], "main")

            # After the conversation ends, the next press is handled globally.
            asyncio.run(bot.button_handler(second_update, context))

        self.assertEqual(second_update.message.replies[-1][0], "Test >")
        self.assertEqual(context.user_data["menu_parent"], "main")

    def test_cancel_from_top_level_submenu_returns_to_main_menu(self):
        """Проверяет возврат из корневого подменю в главное меню."""
        update = FakeUpdate("⬅️ Назад в меню", 200)
        context = FakeContext({"menu_parent": "main"})
        with patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.cancel(update, context))
        self.assertTrue(update.message.replies[-1][0].endswith(" >"))
        self.assertNotEqual(update.message.replies[-1][0], "Главное меню:")

    def test_schedule_minutes_are_validated_in_database(self):
        """Проверяет проверку времени смены на уровне базы данных."""
        self.assertEqual(database.schedule_time_to_minutes("09:30"), 570)
        self.assertEqual(database.schedule_time_to_minutes("18:15"), 1095)
        self.assertIsNone(database.schedule_time_to_minutes("24:30", allow_24=True))


    def test_weekly_and_monthly_reports_exclude_current_active_shift(self):
        """Недельный и месячный отчёты учитывают только закрытые смены."""
        shifts = [{
            "user_id": 202,
            "shift_start": "2026-11-13 09:00:00",
            "shift_end": None,
            "status": "active",
            "employee_name": "Ivan Ivanov",
        }]
        with patch.object(utils, "get_all_shifts", return_value=shifts):
            weekly = utils.generate_weekly_report(today=__import__("datetime").date(2026, 11, 13))
            monthly = utils.generate_monthly_report(today=__import__("datetime").date(2026, 11, 13))

        for report in (weekly, monthly):
            self.assertIn("завершенных смен нет", report)
            self.assertNotIn("Итого общая сумма", report)
            self.assertNotIn("0 руб.", report)

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
