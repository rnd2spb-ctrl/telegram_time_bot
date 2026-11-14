"""Регрессия: после ввода номера (или контакта) бот должен ответить и показать меню.

Раньше complete_phone_identification использовала context, не принимая его:
привязка сохранялась, затем падал NameError, и пользователь не получал ответа.
"""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import database


class FakeMessage:
    def __init__(self, text=None, contact=None):
        self.text, self.contact, self.replies = text, contact, []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_update(user_id, text=None, contact=None):
    user = SimpleNamespace(id=user_id, first_name="Иван", last_name=None, username=None)
    return SimpleNamespace(message=FakeMessage(text, contact), effective_user=user)


def make_context(user_data=None):
    return SimpleNamespace(user_data=user_data if user_data is not None else {}, args=None)


class PhoneLoginRepliesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (patch.object(database, "DATA_DIR", tmp.name), patch.object(bot.config, "OWNER_ID", 1)):
            patcher.start()
            self.addCleanup(patcher.stop)
        database.add_employee(None, "+79990001122", "Иван", "Иванов")
        database.add_employee(None, "+79990002233", "Пётр", "Петров")

    def test_typed_number_gets_welcome_and_is_logged(self):
        context = make_context()
        asyncio.run(bot.start(make_update(555), context))
        self.assertTrue(context.user_data.get("awaiting_phone"))
        update = make_update(555, text="+79990001122")
        asyncio.run(bot.button_handler(update, context))
        self.assertTrue(update.message.replies, "бот промолчал")
        self.assertTrue(update.message.replies[-1].startswith("Добро пожаловать,"))
        self.assertNotIn("awaiting_phone", context.user_data)
        actions = [row.get("action") for row in database.load_json("audit_log.json")]
        self.assertIn("employee_linked", actions)

    def test_shared_contact_gets_welcome(self):
        contact = SimpleNamespace(user_id=777, phone_number="+79990002233")
        update = make_update(777, contact=contact)
        asyncio.run(bot.contact_handler(update, make_context({"awaiting_phone": True})))
        self.assertTrue(update.message.replies, "бот промолчал")
        self.assertTrue(update.message.replies[-1].startswith("Добро пожаловать,"))


if __name__ == "__main__":
    unittest.main()
