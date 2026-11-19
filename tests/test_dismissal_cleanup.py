"""Тесты правильного закрытия смен и очистки расписания при увольнении."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
import utils


class DismissalCleanupTests(unittest.TestCase):
    """Проверяет обработку активных смен и будущих назначений при увольнении."""

    def _schedule_entry(self, entry_id, user_id, phone, date_value, start, end):
        return {
            "id": entry_id,
            "date": date_value,
            "employee_id": f"phone:{phone.lstrip('+')}",
            "user_id": user_id,
            "employee_name": "Тестовый сотрудник",
            "employee_phone": phone,
            "start_time": start,
            "end_time": end,
            "created_by_user_id": 100,
            "created_by_role": "admin",
            "created_at": "2026-11-01T09:00:00",
        }

    def test_dismiss_closes_active_shift_removes_future_schedule_and_keeps_history(self):
        """При увольнении закрывается текущая смена, а завершённая история сохраняется."""

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 19, 12, 0, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            employee_phone = "+79990000001"
            database_files = {
                "employees.json": [{
                    "user_id": 201,
                    "phone_number": employee_phone,
                    "first_name": "Тест",
                    "last_name": "Сотрудник",
                    "role": "employee",
                    "status": "active",
                }],
                "shifts.json": [
                    {
                        "user_id": 201,
                        "shift_start": "2026-11-18 09:00:00",
                        "shift_end": "2026-11-18 11:00:00",
                        "status": "closed",
                        "employee_name": "Сотрудник",
                    },
                    {
                        "user_id": 201,
                        "shift_start": "2026-11-19 10:00:00",
                        "shift_end": None,
                        "status": "active",
                        "employee_name": "Сотрудник",
                    },
                    {
                        "user_id": 202,
                        "shift_start": "2026-11-19 10:00:00",
                        "shift_end": None,
                        "status": "active",
                    },
                ],
                "schedule.json": [
                    self._schedule_entry(
                        "past", 201, employee_phone,
                        "2026-11-18", "09:00", "11:00"
                    ),
                    self._schedule_entry(
                        "today_future", 201, employee_phone,
                        "2026-11-19", "12:30", "14:00"
                    ),
                    self._schedule_entry(
                        "tomorrow", 201, employee_phone,
                        "2026-11-20", "09:00", "17:00"
                    ),
                    self._schedule_entry(
                        "other_employee", 202, "+79990000002",
                        "2026-11-20", "09:00", "17:00"
                    ),
                ],
            }

            with patch.object(database, "DATA_DIR", temp_dir), \
                 patch.object(database, "datetime", FixedDateTime):
                for filename, payload in database_files.items():
                    database.save_json(filename, payload)

                success, reason = database.dismiss_employee(employee_phone)

                self.assertTrue(success)
                self.assertEqual(reason, "ok")

                employees = database.load_json("employees.json")
                shifts = database.load_json("shifts.json")
                schedule = database.load_json("schedule.json")

                self.assertEqual(employees[0]["status"], "dismissed")

                # Активная смена завершается точным временем увольнения.
                dismissed_shift = next(
                    shift for shift in shifts
                    if shift.get("user_id") == 201
                    and shift.get("shift_start") == "2026-11-19 10:00:00"
                )
                self.assertEqual(dismissed_shift["status"], "closed")
                self.assertEqual(
                    dismissed_shift["shift_end"], "2026-11-19 12:00:00"
                )
                self.assertTrue(dismissed_shift["closed_on_dismissal"])
                self.assertNotIn("auto_closed", dismissed_shift)

                # Предыдущая закрытая смена не меняется и остаётся в выборке отчёта.
                historical_shift = next(
                    shift for shift in shifts
                    if shift.get("shift_start") == "2026-11-18 09:00:00"
                )
                self.assertEqual(
                    historical_shift["shift_end"], "2026-11-18 11:00:00"
                )
                report_shifts = database.get_all_shifts(days=30)
                self.assertIn(historical_shift, report_shifts)
                self.assertEqual(utils.work_time_seconds([historical_shift]), 7200)

                # Удаляются только назначения, которые ещё не закончились к увольнению.
                self.assertEqual(
                    {entry["id"] for entry in schedule},
                    {"past", "other_employee"},
                )
                # Чужая активная смена не затрагивается.
                other_shift = next(
                    shift for shift in shifts if shift.get("user_id") == 202
                )
                self.assertEqual(other_shift["status"], "active")

    def test_invalid_schedule_prevents_partial_dismissal(self):
        """При повреждённом расписании увольнение не меняет сотрудника и смены."""

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "employees.json").write_text(
                json.dumps([{
                    "user_id": 201,
                    "phone_number": "+79990000001",
                    "role": "employee",
                    "status": "active",
                }]),
                encoding="utf-8",
            )
            Path(temp_dir, "shifts.json").write_text(
                json.dumps([{
                    "user_id": 201,
                    "shift_start": "2026-11-19 10:00:00",
                    "shift_end": None,
                    "status": "active",
                }]),
                encoding="utf-8",
            )
            # Дубликаты идентификаторов делают файл расписания некорректным.
            invalid_entry = self._schedule_entry(
                "duplicate", 201, "+79990000001",
                "2026-11-20", "09:00", "17:00"
            )
            Path(temp_dir, "schedule.json").write_text(
                json.dumps([invalid_entry, dict(invalid_entry)]),
                encoding="utf-8",
            )

            with patch.object(database, "DATA_DIR", temp_dir):
                success, reason = database.dismiss_employee("+79990000001")

                self.assertFalse(success)
                self.assertEqual(reason, "schedule_data_invalid")
                self.assertEqual(
                    database.load_json("employees.json")[0]["status"], "active"
                )
                self.assertEqual(
                    database.load_json("shifts.json")[0]["status"], "active"
                )


if __name__ == "__main__":
    unittest.main()
