"""Навигация по меню: «Назад» из «Редактировать» и приглашение в главном меню."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import bot
from test_admin_schedule_features import FakeContext, FakeUpdate


class MenuNavigationTests(unittest.TestCase):
    def run_back(self, text, parent, role="owner"):
        update = FakeUpdate(text, 100)
        context = FakeContext({"menu_parent": parent})
        employee = {"user_id": 100, "first_name": "Иван", "last_name": "Иванов", "role": role, "status": "active"}
        with patch.object(bot, "get_user_role", return_value=role), \
             patch.object(bot, "get_employee_by_user_id", return_value=employee), \
             patch.object(bot, "require_admin", new=AsyncMock(return_value=True)):
            asyncio.run(bot.button_handler(update, context))
        return update, context

    def test_back_from_employees_menu_after_entering_section_goes_to_main_menu(self):
        """Раньше menu_parent оставался 'employees', и «Назад» снова рисовал меню «Редактировать»."""
        with patch.object(bot, "get_main_keyboard", return_value="MAIN") as main_kb, \
             patch.object(bot, "get_employee_management_keyboard", return_value="EMP") as emp_kb:
            update, context = self.run_back(bot.BACK_BUTTON, "employees")
        self.assertEqual(update.message.replies[-1][1]["reply_markup"], "MAIN")
        main_kb.assert_called_once()
        emp_kb.assert_not_called()
        self.assertEqual(context.user_data["menu_parent"], "main")

    def test_main_menu_prompt_is_surname_with_marker(self):
        """В главном меню вместо «Главное меню:» показывается «Фамилия >»."""
        update, _ = self.run_back(bot.BACK_BUTTON, "main", role="employee")
        self.assertEqual(update.message.replies[-1][0], bot.employee_prompt_label(
            {"first_name": "Иван", "last_name": "Иванов", "role": "employee"}))
        self.assertTrue(update.message.replies[-1][0].endswith(">"))

    def test_cancel_inside_dialog_still_returns_one_level_up(self):
        """Из шага диалога «Назад» ведёт в меню «Редактировать», а не сразу в главное."""
        update = FakeUpdate(bot.BACK_BUTTON, 100)
        context = FakeContext({"menu_parent": "employees"})
        with patch.object(bot, "get_user_role", return_value="owner"), \
             patch.object(bot, "get_employee_management_keyboard", return_value="EMP"):
            asyncio.run(bot.cancel(update, context))
        self.assertEqual(update.message.replies[-1][1]["reply_markup"], "EMP")


if __name__ == "__main__":
    unittest.main()
