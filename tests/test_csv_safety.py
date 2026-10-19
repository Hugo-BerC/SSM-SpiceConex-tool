from __future__ import annotations

import unittest

from utils.csv_safety import csv_safe_value


class CsvSafetyTests(unittest.TestCase):
    def test_formula_like_text_is_escaped(self):
        for value in ("=1+1", "+SUM(A1:A2)", "-10+5", "@cmd", "  =A1"):
            with self.subTest(value=value):
                self.assertEqual("'" + value, csv_safe_value(value))

    def test_normal_values_are_unchanged(self):
        self.assertEqual("i-012345", csv_safe_value("i-012345"))
        self.assertEqual(42, csv_safe_value(42))
