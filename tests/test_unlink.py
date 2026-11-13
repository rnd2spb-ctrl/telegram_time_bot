"""Тесты отвязки аккаунта владельцем («Отвязать аккаунт»).

Запуск:  python3 tests/test_unlink.py
Работают во временной папке; python-telegram-bot при отсутствии заменяется заглушкой.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_invites import (  # noqa: E402
    ADMIN_ID, NEWCOMER_ID, OWNER_ID, STAFF_ID, TOKEN_RE, TempDataCase,
    bot, database, employee, make_context, make_update, run,
)

STAFF_PHONE = "+70000000003"


class UnbindDatabaseTests(TempDataCase):
    def setUp(self):
        super().setUp()
        self.save([
            employee(OWNER_ID, "+70000000001", "Влад", "Владелец", role="admin"),
            employee(ADMIN_ID, "+70000000002", "Админ", "Админов", role="admin"),
            employee(STAFF_ID, STAFF_PHONE, "Стас", "Стасов"),
        ])

    def test_unbind_resets_account_and_old_account_loses_access(self):
        self.assertEqual(bot.get_user_role(STAFF_ID), "employee")
        self.assertEqual(database.unbind_employee(STAFF_PHONE, protected_user_id=OWNER_ID), (True, "ok"))
        record = database.get_employee_by_phone(STAFF_PHONE)
        self.assertIsNone(record["user_id"])
        self.assertNotIn("registered_at", record)
        self.assertIsNone(bot.get_user_role(STAFF_ID))

    def test_unbind_refuses_owner_unknown_and_unlinked(self):
        self.assertEqual(database.unbind_employee("+70000000001", protected_user_id=OWNER_ID), (False, "protected"))
        self.assertEqual(database.unbind_employee("+79999999999"), (False, "not_found"))
        database.unbind_employee(STAFF_PHONE)
        self.assertEqual(database.unbind_employee(STAFF_PHONE), (False, "not_bound"))

    def test_unbound_record_gets_new_working_link(self):
        database.unbind_employee(STAFF_PHONE)
        token = database.create_invite(STAFF_PHONE, issued_by=OWNER_ID)
        self.assertTrue(token)
        ok, reason, _ = database.redeem_invite(token, NEWCOMER_ID)
        self.assertTrue(ok, reason)
        self.assertEqual(database.get_employee_by_phone(STAFF_PHONE)["user_id"], NEWCOMER_ID)

    def test_scheduled_shifts_leave_the_old_account(self):
        target = {
            "employee_id": f"phone:{database.normalize_phone(STAFF_PHONE)}", "user_id": STAFF_ID,
            "employee_name": "Стасов Стас", "employee_phone": STAFF_PHONE,
        }
        ok, reason, _ = database.add_to_schedule(
            "2030-01-01", target, "09:00", "18:00",
            actor_user_id=OWNER_ID, actor_role="owner", owner_user_id=OWNER_ID)
        self.assertTrue(ok, reason)
        self.assertEqual(database.get_schedule()[0]["user_id"], STAFF_ID)
        database.unbind_employee(STAFF_PHONE)
        self.assertIsNone(database.get_schedule()[0]["user_id"])


class UnlinkDialogTests(TempDataCase):
    def setUp(self):
        super().setUp()
        self.save([
            employee(ADMIN_ID, "+70000000002", "Админ", "Админов", role="admin"),
            employee(STAFF_ID, STAFF_PHONE, "Стас", "Стасов"),
            employee(None, "+70000000004", "Без", "Привязки"),
            employee(5, "+70000000005", "Уволен", "Уволенов", status="dismissed"),
        ])

    def start_dialog(self, user_id):
        update = make_update(user_id, text="Отвязать аккаунт")
        context = make_context()
        result = run(bot.unlink_entry_point(update, context))
        return update, context, result

    def test_only_owner_can_open_the_dialog(self):
        update, context, _ = self.start_dialog(ADMIN_ID)
        self.assertIn("Только владелец", update.message.replies[-1])
        self.assertNotIn("unlink_candidates", context.user_data)

    def test_list_has_only_linked_active_non_owner_records(self):
        update, context, _ = self.start_dialog(OWNER_ID)
        phones = {item["phone_number"] for item in context.user_data["unlink_candidates"]}
        self.assertEqual(phones, {"+70000000002", STAFF_PHONE})

    def test_full_flow_unlinks_and_issues_new_link(self):
        _, context, _ = self.start_dialog(OWNER_ID)
        index = [i for i, x in enumerate(context.user_data["unlink_candidates"], 1) if x["phone_number"] == STAFF_PHONE][0]
        run(bot.unlink_employee_select(make_update(OWNER_ID, text=str(index)), context))
        confirm = make_update(OWNER_ID, text="1")
        run(bot.unlink_employee_confirm(confirm, context))
        reply = confirm.message.replies[-1]
        self.assertIn("Аккаунт отвязан", reply)
        token = TOKEN_RE.search(reply).group(1)
        self.assertIsNone(database.get_employee_by_phone(STAFF_PHONE)["user_id"])
        self.assertTrue(database.redeem_invite(token, NEWCOMER_ID)[0])
        actions = [row.get("action") for row in database.load_json("audit_log.json")]
        self.assertIn("employee_unlinked", actions)
        self.assertIn("invite_reissued", actions)
        self.assertNotIn(token, Path("data", "audit_log.json").read_text(encoding="utf-8"))

    def test_cancel_changes_nothing(self):
        _, context, _ = self.start_dialog(OWNER_ID)
        run(bot.unlink_employee_select(make_update(OWNER_ID, text="1"), context))
        update = make_update(OWNER_ID, text="2")
        run(bot.unlink_employee_confirm(update, context))
        self.assertIn("отменена", update.message.replies[-1])
        self.assertEqual(database.get_employee_by_user_id(STAFF_ID)["user_id"], STAFF_ID)

    def test_admin_cannot_confirm_even_with_forged_state(self):
        context = make_context(user_data={"unlink_phone": STAFF_PHONE})
        update = make_update(ADMIN_ID, text="1")
        run(bot.unlink_employee_confirm(update, context))
        self.assertIn("Недостаточно прав", update.message.replies[-1])
        self.assertEqual(database.get_employee_by_phone(STAFF_PHONE)["user_id"], STAFF_ID)

    def test_button_is_a_menu_button_and_shown_only_to_owner(self):
        import inspect
        import keyboards
        self.assertIn("Отвязать аккаунт", bot.MENU_BUTTONS)
        source = inspect.getsource(keyboards.get_employee_management_keyboard)
        owner_block = source.split("if is_owner:")[1]
        self.assertIn("Отвязать аккаунт", owner_block)
        self.assertNotIn("Отвязать аккаунт", source.split("if is_owner:")[0])
        # «Назад» должна быть у всех администраторов: строка на уровне 4 пробелов, вне блока владельца
        self.assertTrue(any(line.startswith("    buttons.append([KeyboardButton(BACK_BUTTON)])") for line in source.splitlines()))


class BotAssemblyTests(unittest.TestCase):
    def test_main_builds_handlers_without_errors(self):
        with patch.object(bot.config, "BOT_TOKEN", "t"), patch.object(bot.config, "OWNER_ID", OWNER_ID):
            bot.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
