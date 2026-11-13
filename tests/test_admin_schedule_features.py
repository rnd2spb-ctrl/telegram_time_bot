import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bot
import database


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []
        self.contact = None

    async def reply_text(self, message, *args, **kwargs):
        self.replies.append((str(message), kwargs))


class FakeUser:
    def __init__(self, user_id, first_name="Test", last_name="User"):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name


class FakeUpdate:
    def __init__(self, text, user_id=200):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)


class FakeContext:
    def __init__(self, user_data=None):
        self.user_data = user_data if user_data is not None else {}


class AdminAndScheduleFeatureTests(unittest.TestCase):
    def test_number_selection_parser(self):
        self.assertEqual(bot.parse_number_selection("1,3,5", 5), [1, 3, 5])
        self.assertEqual(bot.parse_number_selection("1-4", 5), [1, 2, 3, 4])
        self.assertEqual(bot.parse_number_selection("1-3,5;7-8", 8), [1, 2, 3, 5, 7, 8])
        self.assertEqual(bot.parse_number_selection("2,2,1", 3), [1, 2])
        self.assertIsNone(bot.parse_number_selection("", 5))
        self.assertIsNone(bot.parse_number_selection("1,,3", 5))
        self.assertIsNone(bot.parse_number_selection("4-2", 5))
        self.assertIsNone(bot.parse_number_selection("0,2", 5))
        self.assertIsNone(bot.parse_number_selection("1-6", 5))

    def test_schedule_days_parser_handles_month_length_and_leap_year(self):
        self.assertEqual(
            bot.parse_schedule_days("13,15-17", date(2026, 11, 13)),
            ["2026-11-13", "2026-11-15", "2026-11-16", "2026-11-17"],
        )
        self.assertEqual(
            bot.parse_schedule_days("28-29", date(2024, 2, 1)),
            ["2024-02-28", "2024-02-29"],
        )
        self.assertIsNone(bot.parse_schedule_days("29", date(2025, 2, 1)))
        self.assertIsNone(bot.parse_schedule_days("31", date(2026, 11, 1)))

    def test_employee_prompt_labels_include_admin_role(self):
        ordinary = {"last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "role": "employee"}
        admin = {"last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin"}
        self.assertEqual(bot.employee_prompt_label(ordinary), "\u0418\u0432\u0430\u043d\u043e\u0432 >")
        self.assertEqual(bot.employee_prompt_label(admin), "\u041f\u0435\u0442\u0440\u043e\u0432 (\u0430\u0434\u043c\u0438\u043d) >")

    def test_admin_can_see_other_admin_and_employee_in_management_list(self):
        actor = {"user_id": 200, "phone_number": "+79990000002", "first_name": "Admin", "last_name": "Actor", "role": "admin", "status": "active"}
        other_admin = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petya", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        employee = {"user_id": 202, "phone_number": "+79990000004", "first_name": "Ivan", "last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "role": "employee", "status": "active"}
        owner = {"user_id": 100, "phone_number": "+79990000001", "first_name": "Owner", "last_name": "Owner", "role": "owner", "status": "active"}
        update = FakeUpdate("\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c", 200)
        context = FakeContext()
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_employee_by_user_id", return_value=actor), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "get_all_employees", return_value=[owner, actor, other_admin, employee]):
            asyncio.run(bot.button_handler(update, context))
        message = update.message.replies[-1][0]
        self.assertIn("\u041f\u0435\u0442\u0440\u043e\u0432 (\u0430\u0434\u043c\u0438\u043d) >", message)
        self.assertIn("\u0418\u0432\u0430\u043d\u043e\u0432 >", message)
        self.assertNotIn("Owner Owner", message)

    def test_admin_can_select_another_admin_for_editing(self):
        target_admin = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petya", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        update = FakeUpdate("1", 200)
        context = FakeContext({"manage_employee_action": "edit", "manage_employee_phones": [target_admin["phone_number"]]})
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_employee_by_phone", return_value=target_admin):
            state = asyncio.run(bot.manage_employee_select(update, context))
        self.assertEqual(state, bot.MANAGE_EMPLOYEE_NAME)
        self.assertEqual(context.user_data["manage_employee_phone"], target_admin["phone_number"])

    def test_whoami_reports_id_role_and_full_name(self):
        employee = {"user_id": 201, "first_name": "Petr", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        update = FakeUpdate("/whoami", 201)
        with patch.object(bot, "get_employee_by_user_id", return_value=employee), \
             patch.object(bot, "get_user_role", return_value="admin"):
            asyncio.run(bot.whoami(update, FakeContext()))
        message = update.message.replies[-1][0]
        self.assertIn("201", message)
        self.assertIn("\u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440", message)
        self.assertIn("\u041f\u0435\u0442\u0440\u043e\u0432 Petr", message)

    def test_invalid_schedule_cleanup_flow_supports_list_and_confirmation(self):
        records = [
            {"index": 10 + i, "name": f"Record {i + 1}", "date": "2026-11-13", "start_time": "09:00", "end_time": "15:00"}
            for i in range(5)
        ]
        update = FakeUpdate("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0441\u043c\u0435\u043d\u044b", 200)
        context = FakeContext()
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "get_invalid_schedule_records", return_value=records):
            state = asyncio.run(bot.schedule_menu_action(update, context))
        self.assertEqual(state, bot.SCHEDULE_DELETE_INVALID_SELECT)

        update = FakeUpdate("1,3,5", 200)
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)):
            state = asyncio.run(bot.schedule_invalid_select(update, context))
        self.assertEqual(state, bot.SCHEDULE_DELETE_INVALID_CONFIRM)
        self.assertEqual(context.user_data["schedule_invalid_selected_indices"], [10, 12, 14])

        update = FakeUpdate("1", 200)
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "delete_invalid_schedule_records", return_value=(True, "ok", [records[0], records[2], records[4]])) as delete, \
             patch.object(bot, "audit_action"):
            state = asyncio.run(bot.schedule_invalid_confirm(update, context))
        self.assertEqual(state, bot.SCHEDULE_MENU)
        self.assertEqual(delete.call_args.args[0], [10, 12, 14])
        self.assertIn("3", update.message.replies[-1][0])

    def test_batch_schedule_add_reports_partial_failures(self):
        employee = {"employee_id": "phone:79990000003", "user_id": 201, "employee_name": "\u041f\u0435\u0442\u0440\u043e\u0432 Petr", "employee_phone": "+79990000003"}
        dates = ["2026-11-13", "2026-11-15", "2026-11-17"]
        context = FakeContext({
            "schedule_action": "add", "schedule_dates": dates, "schedule_date": dates[0],
            "schedule_employee": employee, "schedule_start_time": "09:00"
        })
        update = FakeUpdate("18:00", 200)
        entries = [
            {"id": "a", "user_id": 201, "date": dates[0]},
            {"id": "c", "user_id": 201, "date": dates[2]},
        ]
        results = [(True, "ok", entries[0]), (False, "overlap", None), (True, "ok", entries[1])]
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "add_to_schedule", side_effect=results) as add, \
             patch.object(bot, "audit_action"):
            state = asyncio.run(bot.schedule_end_time(update, context))
        self.assertEqual(state, bot.SCHEDULE_MENU)
        self.assertEqual(add.call_count, 3)
        self.assertIn("2 \u0438\u0437 3", update.message.replies[-1][0])
        self.assertIn("15.11.2026", update.message.replies[-1][0])
        self.assertNotIn("schedule_dates", context.user_data)

    def test_schedule_employee_options_preserves_admin_role(self):
        employees = [
            {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petya", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"},
            {"user_id": 202, "phone_number": "+79990000004", "first_name": "Ivan", "last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "role": "employee", "status": "active"},
        ]
        with patch.object(bot, "get_all_employees", return_value=employees):
            options = bot._schedule_employee_options("admin")
        by_id = {item["user_id"]: item for item in options}
        self.assertEqual(by_id[201]["role"], "admin")
        self.assertEqual(by_id[202]["role"], "employee")

    def test_database_invalid_schedule_deletion_protects_valid_and_owner_records(self):
        owner_id = 100
        admin_id = 200
        valid = {
            "id": "valid-entry", "date": "2026-11-13", "employee_id": "phone:79990000004",
            "user_id": 202, "employee_name": "Ivanov Ivan", "employee_phone": "+79990000004",
            "start_time": "09:00", "end_time": "15:00", "created_by_user_id": owner_id,
            "created_by_role": "owner", "created_at": "2026-11-12T10:00:00"
        }
        bad_employee = {"employee_name": "Bad record", "date": "??"}
        bad_owner = {"user_id": owner_id, "employee_id": f"owner:{owner_id}", "employee_name": "Owner"}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(database, "DATA_DIR", temp_dir):
            database.save_json("employees.json", [
                {"user_id": admin_id, "phone_number": "+79990000002", "role": "admin", "status": "active"}
            ])
            database.save_json("schedule.json", [bad_employee, valid, bad_owner])
            records = database.get_invalid_schedule_records("admin", owner_id)
            self.assertEqual([item["index"] for item in records], [0])
            before_owner_attempt = Path(temp_dir, "schedule.json").read_bytes()
            success, reason, _ = database.delete_invalid_schedule_records([2], admin_id, "admin", owner_id)
            self.assertFalse(success)
            self.assertEqual(reason, "owner_protected")
            self.assertEqual(Path(temp_dir, "schedule.json").read_bytes(), before_owner_attempt)
            success, reason, deleted = database.delete_invalid_schedule_records([0], admin_id, "admin", owner_id)
            self.assertTrue(success)
            self.assertEqual(reason, "ok")
            self.assertEqual(len(deleted), 1)
            remaining = database._read_schedule_records()
            self.assertIsNone(remaining)  # the protected malformed owner record remains untouched
            raw = json.loads(Path(temp_dir, "schedule.json").read_text(encoding="utf-8"))
            self.assertEqual(raw, [valid, bad_owner])
            before = Path(temp_dir, "schedule.json").read_bytes()
            success, reason, _ = database.delete_invalid_schedule_records([0], admin_id, "admin", owner_id)
            self.assertFalse(success)
            self.assertEqual(reason, "not_invalid")
            self.assertEqual(Path(temp_dir, "schedule.json").read_bytes(), before)


    def test_admin_can_edit_another_admin_account(self):
        target_admin = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petya", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        context = FakeContext({
            "manage_employee_phone": target_admin["phone_number"],
            "manage_employee_last_name": "\u0421\u0438\u0434\u043e\u0440\u043e\u0432",
            "manage_employee_first_name": "Petr"
        })
        update = FakeUpdate("-", 200)
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_employee_by_phone", return_value=target_admin), \
             patch.object(bot, "update_employee", return_value=(True, "ok")) as update_employee, \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "audit_action"):
            state = asyncio.run(bot.manage_employee_phone(update, context))
        self.assertEqual(state, bot.ConversationHandler.END)
        update_employee.assert_called_once()
        self.assertIn("\u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u044b", update.message.replies[-1][0])

    def test_admin_can_dismiss_another_admin_account(self):
        target_admin = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petya", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        context = FakeContext({"manage_employee_action": "dismiss", "manage_employee_phone": target_admin["phone_number"]})
        update = FakeUpdate("1", 200)
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_employee_by_phone", return_value=target_admin), \
             patch.object(bot, "dismiss_employee", return_value=(True, "ok")) as dismiss, \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "audit_action"):
            state = asyncio.run(bot.manage_employee_confirm(update, context))
        self.assertEqual(state, bot.ConversationHandler.END)
        dismiss.assert_called_once_with(target_admin["phone_number"], protected_user_id=bot.config.OWNER_ID)
        self.assertIn("\u0443\u0432\u043e\u043b\u0435\u043d", update.message.replies[-1][0])

    def test_main_registers_whoami_and_invalid_schedule_states(self):
        class FakeApplication:
            def __init__(self):
                self.handlers = []
            def add_handler(self, handler):
                self.handlers.append(handler)
            def run_polling(self):
                return None

        fake_app = FakeApplication()
        builder = type("Builder", (), {})()
        builder.token = lambda token: builder
        builder.build = lambda: fake_app
        with patch.object(bot.config, "BOT_TOKEN", "test-token"), \
             patch.object(bot.config, "OWNER_ID", 100), \
             patch.object(bot.Application, "builder", return_value=builder):
            bot.main()
        command_names = [command for handler in fake_app.handlers if isinstance(handler, bot.CommandHandler) for command in handler.commands]
        self.assertIn("whoami", command_names)
        conversations = [handler for handler in fake_app.handlers if isinstance(handler, bot.ConversationHandler)]
        schedule_conversation = next(handler for handler in conversations if bot.SCHEDULE_DELETE_INVALID_SELECT in handler.states)
        self.assertIn(bot.SCHEDULE_DELETE_INVALID_CONFIRM, schedule_conversation.states)
        self.assertIn(bot.SCHEDULE_MENU, schedule_conversation.states)


    def test_admin_can_assign_schedule_to_another_admin_in_real_temporary_database(self):
        actor = {"user_id": 200, "phone_number": "+79990000002", "first_name": "Admin", "last_name": "Actor", "role": "admin", "status": "active"}
        target = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petr", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        selected = {
            "employee_id": "phone:79990000003", "user_id": 201,
            "employee_name": "\u041f\u0435\u0442\u0440\u043e\u0432 Petr", "employee_phone": "+79990000003"
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(database, "DATA_DIR", temp_dir):
            database.save_json("employees.json", [actor, target])
            success, reason, entry = database.add_to_schedule(
                "2026-11-13", selected, "09:00", "18:00",
                actor_user_id=200, actor_role="admin", owner_user_id=100
            )
            self.assertTrue(success)
            self.assertEqual(reason, "ok")
            self.assertEqual(entry["user_id"], 201)
            records = database.get_schedule()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["employee_id"], selected["employee_id"])
            overlap, overlap_reason, _ = database.add_to_schedule(
                "2026-11-13", selected, "12:00", "20:00",
                actor_user_id=200, actor_role="admin", owner_user_id=100
            )
            self.assertFalse(overlap)
            self.assertEqual(overlap_reason, "overlap")
            self.assertEqual(len(database.get_schedule()), 1)

    def test_schedule_confirmation_label_contains_admin_role(self):
        entry = {
            "date": "2026-11-13", "employee_name": "Petrov Petr",
            "employee_id": "phone:79990000003", "employee_phone": "+79990000003",
            "user_id": 201, "start_time": "09:00", "end_time": "18:00"
        }
        admin = {"user_id": 201, "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "first_name": "Petr", "role": "admin"}
        with patch.object(bot, "get_employee_by_user_id", return_value=admin):
            label = bot._schedule_entry_label(entry)
        self.assertIn("\u041f\u0435\u0442\u0440\u043e\u0432 (\u0430\u0434\u043c\u0438\u043d) >", label)
        self.assertIn("09:00\u201318:00", label)

    def test_employee_overview_preserves_dismissed_marker_and_role(self):
        dismissed_admin = {
            "user_id": 201, "phone_number": "+79990000003", "first_name": "Petr",
            "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "dismissed"
        }
        update = FakeUpdate("\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c", 200)
        with patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_employee_by_user_id", return_value={"user_id": 200, "role": "admin"}), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "get_all_employees", return_value=[dismissed_admin]):
            asyncio.run(bot.button_handler(update, FakeContext()))
        text = update.message.replies[-1][0]
        self.assertIn("\u2620\ufe0f \u041f\u0435\u0442\u0440\u043e\u0432 (\u0430\u0434\u043c\u0438\u043d) >", text)
        self.assertIn("dismissed", text)


    def test_schedule_keyboard_exposes_invalid_cleanup_action(self):
        markup = bot.get_schedule_management_keyboard()
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0441\u043c\u0435\u043d\u044b", labels)
        self.assertIn("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0448\u0438\u0431\u043e\u0447\u043d\u044b\u0435 \u0441\u043c\u0435\u043d\u044b", bot.MENU_BUTTONS)


    def test_explicit_employee_role_overrides_legacy_is_admin_flag(self):
        employee = {"last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "role": "employee", "is_admin": True}
        with patch.object(bot.config, "OWNER_ID", None):
            self.assertEqual(bot.employee_prompt_label(employee), "\u0418\u0432\u0430\u043d\u043e\u0432 >")
            unbound = {"last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "user_id": None, "role": "employee"}
            self.assertEqual(bot.employee_prompt_label(unbound), "\u0418\u0432\u0430\u043d\u043e\u0432 >")

    def test_schedule_date_handler_accepts_multiple_days_and_labels_admin(self):
        from datetime import datetime

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 13, 12, 0)

        admin_option = {
            "employee_id": "phone:79990000003", "user_id": 201,
            "employee_name": "\u041f\u0435\u0442\u0440\u043e\u0432 Petr", "employee_phone": "+79990000003",
            "role": "admin"
        }
        employee_option = {
            "employee_id": "phone:79990000004", "user_id": 202,
            "employee_name": "\u0418\u0432\u0430\u043d\u043e\u0432 Ivan", "employee_phone": "+79990000004",
            "role": "employee"
        }
        update = FakeUpdate("13,15-16", 200)
        context = FakeContext({"schedule_action": "add"})
        with patch.object(bot, "datetime", FixedDateTime), \
             patch.object(bot, "require_admin", new=AsyncMock(return_value=True)), \
             patch.object(bot, "get_user_role", return_value="admin"), \
             patch.object(bot, "_schedule_employee_options", return_value=[admin_option, employee_option]):
            state = asyncio.run(bot.schedule_date(update, context))
        self.assertEqual(state, bot.GET_SCHEDULE_EMPLOYEE)
        self.assertEqual(context.user_data["schedule_dates"], [
            "2026-11-13", "2026-11-15", "2026-11-16"
        ])
        prompt = update.message.replies[-1][0]
        self.assertIn("\u041f\u0435\u0442\u0440\u043e\u0432 (\u0430\u0434\u043c\u0438\u043d) >", prompt)
        self.assertIn("\u0418\u0432\u0430\u043d\u043e\u0432 >", prompt)

    def test_admin_can_update_dismiss_and_delete_other_admin_in_real_database(self):
        owner = {"user_id": 100, "phone_number": "+79990000001", "first_name": "Owner", "last_name": "Owner", "role": "owner", "status": "active"}
        first_admin = {"user_id": 201, "phone_number": "+79990000003", "first_name": "Petr", "last_name": "\u041f\u0435\u0442\u0440\u043e\u0432", "role": "admin", "status": "active"}
        second_admin = {"user_id": 202, "phone_number": "+79990000004", "first_name": "Ivan", "last_name": "\u0418\u0432\u0430\u043d\u043e\u0432", "role": "admin", "status": "active"}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(database, "DATA_DIR", temp_dir):
            database.save_json("employees.json", [owner, first_admin, second_admin])
            changed, reason = database.update_employee(
                first_admin["phone_number"], first_name="Petr Updated",
                last_name="\u0421\u0438\u0434\u043e\u0440\u043e\u0432", new_phone="+79990000033"
            )
            self.assertTrue(changed)
            self.assertEqual(reason, "ok")
            updated = database.get_employee_by_user_id(201)
            self.assertEqual(updated["role"], "admin")
            self.assertEqual(updated["phone_number"], "+79990000033")
            dismissed, reason = database.dismiss_employee("+79990000033", protected_user_id=100)
            self.assertTrue(dismissed)
            self.assertEqual(reason, "ok")
            self.assertEqual(database.get_employee_by_user_id(201)["role"], "admin")
            self.assertEqual(database.get_employee_by_user_id(201)["status"], "dismissed")
            deleted, reason = database.delete_employee_without_history(
                second_admin["phone_number"], protected_user_id=100
            )
            self.assertTrue(deleted)
            self.assertEqual(reason, "ok")
            self.assertIsNone(database.get_employee_by_user_id(202))
            protected, reason = database.dismiss_employee(owner["phone_number"], protected_user_id=100)
            self.assertFalse(protected)
            self.assertEqual(reason, "protected")
            self.assertIsNotNone(database.get_employee_by_user_id(100))


if __name__ == "__main__":
    unittest.main()
