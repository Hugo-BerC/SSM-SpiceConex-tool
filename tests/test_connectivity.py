from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.connectivity import (
    build_connectivity_script,
    parse_connectivity_csv,
    parse_connectivity_output,
)


class ConnectivityTests(unittest.TestCase):
    def test_csv_rows_become_individual_tcp_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_file = Path(directory) / "connectivity.csv"
            csv_file.write_text(
                "service,destination,port,scope,protocol\n"
                "api,api.internal.example,443,internal,TCP\n"
                "payment,payments.example,8443,external,TCP\n",
                encoding="utf-8",
            )
            rows = parse_connectivity_csv(csv_file)
        self.assertEqual(2, len(rows))
        script = build_connectivity_script(rows, 5)
        self.assertIn("api.internal.example", script)
        self.assertIn("payments.example", script)
        self.assertIn("api|api.internal.example|443|internal", script)
        self.assertIn("payment|payments.example|8443|external", script)

    def test_output_keeps_a_result_for_each_connection(self):
        results = parse_connectivity_output(
            "api|api.internal.example|443|internal|OK|12|connected\n"
            "payment|payments.example|8443|external|TIMEOUT|5010|command timeout\n"
        )
        self.assertEqual(["OK", "TIMEOUT"], [item["status"] for item in results])
        self.assertEqual("payments.example", results[1]["destination"])
