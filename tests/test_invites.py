"""Тесты входа по ссылке-приглашению и уведомлений администраторам.

Запуск из любой папки:  python3 tests/test_invites.py
Тесты работают во временной папке и не трогают рабочие файлы в data/.
Если python-telegram-bot не установлен, он заменяется заглушкой: проверяется
только логика бота, а не работа с настоящим Telegram.
"""
import asyncio
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("BOT_TOKEN", "test-token")

try:
    import telegram  # noqa: F401
    import telegram.ext  # noqa: F401
except ImportError:
    for _name in ("telegram", "telegram.ext"):
        sys.modules.setdefault(_name, MagicMock())

import bot  # noqa: E402
import database  # noqa: E402

OWNER_ID = 1
PHONE = "+79123456789"
ADMIN_ID = 2
STAFF_ID = 3
NEWCOMER_ID = 777
TOKEN_RE = re.compile(r"start=([A-Za-z0-9_-]+)")


def employee(user_id, phone, first="Имя", last="Фамилия", role="employee", status="active"):
    return {
        "user_id": user_id, "phone_number": phone, "first_name": first,
        "last_name": last, "role": role, "status": status, "registered_at": None,
    }


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(str(text))


class FakeBot:
    username = "testbot"

    def __init__(self, fail_ids=()):
        self.sent = []
        self.fail_ids = set(fail_ids)

    async def send_message(self, chat_id, text):
        if chat_id in self.fail_ids:
            raise RuntimeError("blocked by user")
        self.sent.append((chat_id, text))


def make_update(user_id, text=None, username=None, first_name="Tg"):
    user = SimpleNamespace(id=user_id, first_name=first_name, last_name=None, username=username)
    return SimpleNamespace(effective_user=user, message=FakeMessage(text))


def make_context(args=None, user_data=None, fail_ids=()):
    return SimpleNamespace(args=args or [], user_data=user_data if user_data is not None else {},
                           bot=FakeBot(fail_ids))


def run(coro):
    return asyncio.run(coro)


class TempDataCase(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        patcher = patch.object(bot.config, "OWNER_ID", OWNER_ID)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def save(self, records):
        database.save_json("employees.json", records)

    def records(self):
        return database.load_json("employees.json")

    def raw_employees_file(self):
        return Path("data", "employees.json").read_text(encoding="utf-8")


class InviteDatabaseTests(TempDataCase):
    def test_redeem_links_account_and_removes_code(self):
        self.save([employee(None, PHONE, "Иван", "Иванов")])
        token = database.create_invite(PHONE, issued_by=ADMIN_ID)
        self.assertTrue(token)
        ok, reason, linked = database.redeem_invite(token, NEWCOMER_ID)
        self.assertEqual((ok, reason), (True, "ok"))
        record = self.records()[0]
        self.assertEqual(record["user_id"], NEWCOMER_ID)
        self.assertNotIn("invite_hash", record)
        self.assertNotIn("invite_expires", record)
        self.assertEqual(record["invited_by"], ADMIN_ID)
        self.assertTrue(record["registered_at"])
        self.assertEqual(linked["user_id"], NEWCOMER_ID)

    def test_code_is_not_stored_in_clear(self):
        self.save([employee(None, PHONE)])
        token = database.create_invite(PHONE)
        self.assertNotIn(token, self.raw_employees_file())

    def test_phone_number_or_guess_is_not_a_code(self):
        self.save([employee(None, PHONE)])
        database.create_invite(PHONE)
        for guess in (PHONE, "79123456789", "A" * 22, "", None, "x" * 100):
            ok, reason, _ = database.redeem_invite(guess, NEWCOMER_ID)
            self.assertEqual((ok, reason), (False, "invalid"))
        self.assertIsNone(self.records()[0]["user_id"])

    def test_code_works_only_once(self):
        self.save([employee(None, PHONE)])
        token = database.create_invite(PHONE)
        self.assertTrue(database.redeem_invite(token, NEWCOMER_ID)[0])
        ok, reason, _ = database.redeem_invite(token, 888)
        self.assertEqual((ok, reason), (False, "invalid"))
        self.assertEqual(self.records()[0]["user_id"], NEWCOMER_ID)

    def test_expired_code_is_rejected(self):
        self.save([employee(None, PHONE)])
        token = database.create_invite(PHONE)
        records = self.records()
        records[0]["invite_expires"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.save(records)
        self.assertEqual(database.redeem_invite(token, NEWCOMER_ID)[:2], (False, "invalid"))

    def test_dismissed_employee_cannot_use_code(self):
        self.save([employee(None, PHONE)])
        token = database.create_invite(PHONE)
        records = self.records()
        records[0]["status"] = "dismissed"
        self.save(records)
        self.assertEqual(database.redeem_invite(token, NEWCOMER_ID)[:2], (False, "invalid"))
        self.assertIsNone(database.create_invite(PHONE))

    def test_account_cannot_take_second_record(self):
        self.save([employee(None, PHONE), employee(None, "+79990001122")])
        first = database.create_invite(PHONE)
        second = database.create_invite("+79990001122")
        self.assertTrue(database.redeem_invite(first, NEWCOMER_ID)[0])
        self.assertEqual(database.redeem_invite(second, NEWCOMER_ID)[:2], (False, "user_already_bound"))
        self.assertIsNone(self.records()[1]["user_id"])

    def test_reissue_replaces_old_code(self):
        self.save([employee(None, PHONE)])
        old = database.create_invite(PHONE)
        new = database.create_invite(PHONE)
        self.assertNotEqual(old, new)
        self.assertEqual(database.redeem_invite(old, NEWCOMER_ID)[:2], (False, "invalid"))
        self.assertTrue(database.redeem_invite(new, NEWCOMER_ID)[0])

    def test_no_invite_for_linked_or_unknown_record(self):
        self.save([employee(STAFF_ID, PHONE)])
        self.assertIsNone(database.create_invite(PHONE))
        self.assertIsNone(database.create_invite("+70000000000"))

    def test_token_fits_telegram_start_payload(self):
        self.save([employee(None, PHONE)])
        token = database.create_invite(PHONE)
        self.assertRegex(token, r"^[A-Za-z0-9_-]{1,64}$")

    def test_invite_state(self):
        self.save([employee(None, PHONE)])
        self.assertEqual(database.get_invite_state(self.records()[0])[0], "none")
        database.create_invite(PHONE)
        state, expires = database.get_invite_state(self.records()[0])
        self.assertEqual(state, "pending")
        self.assertGreater(expires, datetime.now())
        self.assertEqual(database.get_invite_state(employee(STAFF_ID, PHONE))[0], "linked")

    def test_login_keeps_scheduled_shifts_attached(self):
        """Смены, назначенные до входа, получают Telegram ID вошедшего сотрудника."""
        self.save([employee(None, PHONE, "Иван", "Иванов")])
        target = {
            "employee_id": f"phone:{database.normalize_phone(PHONE)}", "user_id": None,
            "employee_name": "Иванов Иван", "employee_phone": PHONE,
        }
        ok, reason, _ = database.add_to_schedule(
            "2026-11-20", target, "09:00", "18:00",
            actor_user_id=OWNER_ID, actor_role="owner", owner_user_id=OWNER_ID)
        self.assertTrue(ok, reason)
        self.assertIsNone(database.get_schedule()[0]["user_id"])

        token = database.create_invite(PHONE)
        self.assertTrue(database.redeem_invite(token, NEWCOMER_ID)[0])
        self.assertEqual(database.get_schedule()[0]["user_id"], NEWCOMER_ID)


class BotInviteTests(TempDataCase):
    def setUp(self):
        super().setUp()
        self.save([
            employee(ADMIN_ID, "+70000000002", "Админ", "Админов", role="admin"),
            employee(STAFF_ID, "+70000000003", "Стас", "Стасов"),
            employee(4, "+70000000004", "Уволен", "Уволенов", role="admin", status="dismissed"),
        ])

    def issue_link_through_add_dialog(self, phone=PHONE, name="Иванов Иван"):
        update = make_update(ADMIN_ID, text=name)
        context = make_context(user_data={"new_employee_phone": phone})
        run(bot.add_employee_name(update, context))
        return update.message.replies[-1]

    def test_add_dialog_gives_admin_working_link(self):
        reply = self.issue_link_through_add_dialog()
        match = TOKEN_RE.search(reply)
        self.assertIn("https://t.me/testbot?start=", reply)
        self.assertTrue(match)

        update = make_update(NEWCOMER_ID, username="ivan_tg")
        run(bot.start(update, make_context(args=[match.group(1)])))
        self.assertIn("Добро пожаловать", update.message.replies[-1])
        self.assertIn("Иванов", update.message.replies[-1])
        self.assertEqual(bot.get_user_role(NEWCOMER_ID), "employee")

    def test_audit_log_never_contains_the_code(self):
        token = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        run(bot.start(make_update(NEWCOMER_ID), make_context(args=[token])))
        log_text = Path("data", "audit_log.json").read_text(encoding="utf-8")
        self.assertNotIn(token, log_text)
        self.assertIn("invite_created", log_text)
        self.assertIn("employee_linked", log_text)

    def test_admins_and_owner_are_told_who_logged_in(self):
        token = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        context = make_context(args=[token])
        run(bot.start(make_update(NEWCOMER_ID, username="ivan_tg"), context))

        recipients = {chat_id for chat_id, _ in context.bot.sent}
        self.assertEqual(recipients, {OWNER_ID, ADMIN_ID})  # уволенный админ не получает
        text = context.bot.sent[0][1]
        for expected in ("Иванов Иван", PHONE, "@ivan_tg", f"ID {NEWCOMER_ID}",
                         "Ссылку выдал: Админов Админ", "по ссылке-приглашению"):
            self.assertIn(expected, text)
        self.assertNotIn(token, text)

    def test_failed_notification_does_not_block_login(self):
        token = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        context = make_context(args=[token], fail_ids={OWNER_ID})
        run(bot.start(make_update(NEWCOMER_ID), context))
        self.assertEqual(bot.get_user_role(NEWCOMER_ID), "employee")
        self.assertEqual([chat_id for chat_id, _ in context.bot.sent], [ADMIN_ID])
        log_text = Path("data", "audit_log.json").read_text(encoding="utf-8")
        self.assertIn("notification_failed", log_text)

    def test_recipients_exclude_the_person_who_logged_in(self):
        recipients = bot.get_link_notification_recipients(exclude_user_id=ADMIN_ID)
        self.assertEqual(recipients, [OWNER_ID])

    def test_bad_link_gives_generic_message_and_no_access(self):
        update = make_update(NEWCOMER_ID)
        run(bot.start(update, make_context(args=["x" * 22])))
        self.assertIn("недействительна", update.message.replies[-1])
        self.assertIsNone(bot.get_user_role(NEWCOMER_ID))

    def test_phone_number_typed_as_text_gives_no_access(self):
        self.issue_link_through_add_dialog()  # запись Ивана существует, но не привязана
        update = make_update(NEWCOMER_ID, text=PHONE)
        run(bot.button_handler(update, make_context()))
        self.assertIsNone(database.get_employee_by_user_id(NEWCOMER_ID))
        self.assertIsNone(bot.get_user_role(NEWCOMER_ID))
        self.assertIn("ссылка", update.message.replies[-1])

    def test_start_without_link_asks_for_invite(self):
        update = make_update(NEWCOMER_ID)
        run(bot.start(update, make_context()))
        self.assertIn("ссылка-приглашение", update.message.replies[-1])

    def test_linked_employee_cannot_spend_someone_elses_code(self):
        token = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        run(bot.start(make_update(STAFF_ID), make_context(args=[token])))
        self.assertIsNone(database.get_employee_by_phone(PHONE)["user_id"])  # код не погашен
        self.assertTrue(database.redeem_invite(token, NEWCOMER_ID)[0])

    def test_dismissed_employee_with_a_link_stays_blocked(self):
        token = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        update = make_update(4)  # уволенный админ уже связан с записью
        run(bot.start(update, make_context(args=[token])))
        self.assertIn("приостановлен", update.message.replies[-1])
        self.assertIsNone(database.get_employee_by_phone(PHONE)["user_id"])

    def test_new_link_command_replaces_old_one(self):
        old = TOKEN_RE.search(self.issue_link_through_add_dialog()).group(1)
        update = make_update(ADMIN_ID, text="1")
        context = make_context(user_data={
            "manage_employee_action": "invite",
            "manage_employee_phones": [PHONE],
        })
        run(bot.manage_employee_select(update, context))
        new = TOKEN_RE.search(update.message.replies[-1]).group(1)
        self.assertNotEqual(old, new)

        self.assertEqual(database.redeem_invite(old, NEWCOMER_ID)[:2], (False, "invalid"))
        self.assertTrue(database.redeem_invite(new, NEWCOMER_ID)[0])

    def test_new_link_button_lists_only_unlinked_active_employees(self):
        self.issue_link_through_add_dialog()
        update = make_update(ADMIN_ID, text="Новая ссылка")
        context = make_context()
        run(bot.button_handler(update, context))
        self.assertEqual(context.user_data["manage_employee_phones"], [PHONE])
        self.assertEqual(context.user_data["manage_employee_action"], "invite")

    def test_list_marks_waiting_employees(self):
        self.issue_link_through_add_dialog()
        waiting = next(item for item in database.get_all_employees() if item["phone_number"] == PHONE)
        self.assertIn("ожидает входа", bot.describe_link_state(waiting))
        self.assertEqual(bot.describe_link_state(database.get_employee_by_user_id(STAFF_ID)), "")

        waiting["invite_expires"] = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        self.assertIn("истекла", bot.describe_link_state(waiting))
        waiting["status"] = "dismissed"
        self.assertEqual(bot.describe_link_state(waiting), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
