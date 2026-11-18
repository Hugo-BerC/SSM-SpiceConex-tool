from __future__ import annotations

import tempfile
import io
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from services.connectivity import (
    build_connectivity_script,
    parse_connectivity_csv,
    parse_connectivity_output,
    validate_connectivity_csv,
)
from services.ssm import CommandResult


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

    def row(self, **overrides):
        return {"service": "api", "destination": "api.example.test", "port": "443",
                "scope": "internal", "protocol": "TCP", **overrides}

    def test_multiline_data_has_an_unindented_heredoc_terminator(self):
        rows = [self.row(), self.row(service="second")]
        script = build_connectivity_script(rows, 5)
        self.assertEqual("EOF_CONNECTIVITY", script.splitlines()[-1])
        self.assertIn("\napi|api.example.test|443|internal\nsecond|api.example.test|443|internal\n", script)

    def test_empty_cells_are_rejected_and_literal_na_is_preserved(self):
        import pandas as pd
        for values in (",api.example.test,443,internal", "api,,443,internal", "api,host,443,"):
            frame = pd.read_csv(io.StringIO("service,destination,port,scope\n" + values + "\n"),
                                dtype=str, keep_default_na=False)
            with self.subTest(values=values), patch("services.connectivity.Path.is_file", return_value=True), \
                 patch("services.connectivity.pd.read_csv", return_value=frame):
                with self.assertRaisesRegex(ValueError, "missing"):
                    parse_connectivity_csv("synthetic.csv")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.csv"
            path.write_text("service,destination,port,scope\nNA,na.example.test,443,internal\n", encoding="utf-8")
            self.assertEqual("NA", parse_connectivity_csv(path)[0]["service"])

    def test_udp_and_option_like_destinations_are_rejected_before_sending(self):
        for row in (self.row(protocol="UDP"), self.row(destination="-e")):
            with self.subTest(row=row), patch("services.connectivity.send_command") as send:
                with self.assertRaises(ValueError):
                    validate_connectivity_csv("profile", "eu-west-1", "i-test", [row], 5)
                send.assert_not_called()

    def test_wait_budget_scales_with_matrix_and_sets_remote_execution_limit(self):
        rows = [self.row(service=f"api-{index}") for index in range(20)]
        result = CommandResult("i-test", "Success", "", "", "command-id")
        with patch("services.connectivity.send_command", return_value=[result]) as send:
            results, _command = validate_connectivity_csv("profile", "eu-west-1", "i-test", rows, 10)
        request = send.call_args.kwargs
        self.assertGreaterEqual(request["execution_timeout_seconds"], 20 * 10)
        self.assertGreater(request["timeout_seconds"], request["execution_timeout_seconds"])
        self.assertEqual(20, len(results))
        self.assertTrue(all(row["status"] == "NO_RESULT" for row in results))

    def test_tracking_timeout_preserves_partial_results_without_claiming_failure_for_unreported_rows(self):
        rows = [self.row(), self.row(service="second")]
        command = CommandResult("i-test", "TimedOut", "api|api.example.test|443|internal|OK|10|connected\n",
                                "Remote status unknown", "original-command", tracking_timed_out=True)
        with patch("services.connectivity.send_command", return_value=[command]):
            results, returned = validate_connectivity_csv("profile", "eu-west-1", "i-test", rows, 5)
        self.assertIs(command, returned)
        self.assertEqual(["OK", "UNKNOWN"], [row["status"] for row in results])
        self.assertEqual("", results[1]["duration"])
        self.assertIn("original-command", results[1]["detail"])

    def test_remote_failure_without_output_reports_command_identifier(self):
        command = CommandResult("i-test", "Failed", "", "nc command not found", "original-command")
        with patch("services.connectivity.send_command", return_value=[command]):
            with self.assertRaisesRegex(RuntimeError, "original-command"):
                validate_connectivity_csv("profile", "eu-west-1", "i-test", [self.row()], 5)

    def test_duplicate_destinations_keep_individual_probe_results(self):
        rows = [self.row(), self.row()]
        command = CommandResult("i-test", "Success",
                                "api|api.example.test|443|internal|OK|1|connected\n"
                                "api|api.example.test|443|internal|REFUSED|2|refused\n", "", "command-id")
        with patch("services.connectivity.send_command", return_value=[command]):
            results, _command = validate_connectivity_csv("profile", "eu-west-1", "i-test", rows, 5)
        self.assertEqual(["OK", "REFUSED"], [row["status"] for row in results])

    def test_oversized_execution_budget_is_rejected_before_submitting(self):
        with patch("services.connectivity.send_command") as send:
            with self.assertRaisesRegex(ValueError, "split"):
                validate_connectivity_csv("profile", "eu-west-1", "i-test", [self.row()] * 600, 300)
        send.assert_not_called()

    def test_shell_runs_multiple_rows_and_treats_data_as_literal_without_network(self):
        if os.name == "nt":
            git = shutil.which("git")
            shell = Path(git).parent.parent / "bin" / "bash.exe" if git else None
            shell = str(shell) if shell and shell.is_file() else None
        else:
            shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            self.skipTest("No local POSIX shell available")
        rows = [self.row(), self.row(service="second", destination="refused.example.test"),
                self.row(service="literal $(printf unexpected)", destination="literal;$(printf unexpected)")]
        # Both functions replace the real tools: this test never opens a socket.
        prefix = """
nc() {
    case "$4" in
        refused.example.test) printf 'connection refused'; return 1;;
        *) return 0;;
    esac
}
timeout() { shift; "$@"; }
"""
        result = subprocess.run([shell, "-c", prefix + build_connectivity_script(rows, 5)],
                                capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        results = parse_connectivity_output(result.stdout)
        self.assertEqual(["OK", "REFUSED", "OK"], [row["status"] for row in results])
        self.assertEqual(rows[2]["service"], results[2]["service"])
        self.assertEqual(rows[2]["destination"], results[2]["destination"])
        self.assertNotIn("here-document", result.stderr)
