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
