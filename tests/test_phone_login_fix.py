"""Регрессионные тесты: после ввода номера бот должен отвечать, а необработанные ошибки не должны молчать."""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import database

PHONE = "+79990001122"
USER_ID = 555


class FakeMessage:
    def __init__(self, text, contact=None):
        self.text = text
        self.contact = contact
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_update(text, user_id=USER_ID, contact=None):
    user = SimpleNamespace(id=user_id, first_name="Иван", last_name=None, username=None)
    return SimpleNamespace(message=FakeMessage(text, contact), effective_user=user,
                           effective_message=None)


def run(coro):
    return asyncio.run(coro)


class PhoneLoginCompletesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(database, "DATA_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        owner = patch.object(bot.config, "OWNER_ID", 1)
        owner.start()
        self.addCleanup(owner.stop)
        database.add_employee(None, PHONE, "Иван", "Иванов")

    def context(self, awaiting=True):
        return SimpleNamespace(user_data={"awaiting_phone": True} if awaiting else {},
                               args=None, bot=SimpleNamespace(username="bot"))

    def test_typed_phone_logs_in_and_replies_with_menu(self):
        update, context = make_update(PHONE), self.context()
        run(bot.button_handler(update, context))
        self.assertTrue(update.message.replies, "бот промолчал после ввода номера")
        self.assertIn("Добро пожаловать", update.message.replies[-1])
        self.assertNotIn("awaiting_phone", context.user_data)
        self.assertEqual(database.get_employee_by_phone(PHONE)["user_id"], USER_ID)

    def test_own_contact_logs_in_and_replies(self):
        contact = SimpleNamespace(user_id=USER_ID, phone_number=PHONE)
        update, context = make_update("", contact=contact), self.context()
        run(bot.contact_handler(update, context))
        self.assertTrue(update.message.replies, "бот промолчал после отправки контакта")
        self.assertIn("Добро пожаловать", update.message.replies[-1])

    def test_login_is_written_to_audit_log(self):
        run(bot.button_handler(make_update(PHONE), self.context()))
        actions = [row.get("action") for row in database.load_json("audit_log.json")]
        self.assertIn("employee_linked", actions)


class ErrorHandlerTests(unittest.TestCase):
    def test_user_is_told_about_an_unhandled_error(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(error=RuntimeError("сбой"))
        run(bot.error_handler(update, context))
        message.reply_text.assert_awaited_once()
        self.assertIn("ошибка", message.reply_text.await_args.args[0])

    def test_error_without_message_does_not_raise(self):
        run(bot.error_handler(object(), SimpleNamespace(error=RuntimeError("сбой"))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
