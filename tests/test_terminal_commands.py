from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from services.ssm_commands import _executable, build_start_session_command, build_port_forward_command, shell_quote
from terminal import platform as terminal_platform


class TerminalCommandTests(unittest.TestCase):
    def test_posix_arguments_roundtrip_without_becoming_shell_syntax(self):
        profile = "operación 'quotes' \"double\"; $(printf unexpected)"
        for system in ("Linux", "Darwin"):
            with self.subTest(system=system), patch("services.ssm_commands.platform.system", return_value=system):
                command = build_start_session_command("/path with spaces/aws", "i-test", profile, "eu-west-1")
            self.assertEqual(["/path with spaces/aws", "ssm", "start-session", "--target", "i-test",
                              "--profile", profile, "--region", "eu-west-1"], shlex.split(command))

    def test_windows_command_invokes_quoted_executable_and_quotes_profile(self):
        with patch("services.ssm_commands.platform.system", return_value="Windows"):
            command = build_start_session_command("C:/Program Files/Amazon/aws.exe", "i-test",
                                                  "ops;Write-Output", "eu-west-1")
        self.assertTrue(command.startswith("& 'C:/Program Files/Amazon/aws.exe' "))
        self.assertIn("'ops;Write-Output'", command)

    def test_windows_terminal_receives_encoded_script_not_raw_shell_text(self):
        command = "Write-Output 'safe; literal $value'"
        with patch.object(terminal_platform.platform, "system", return_value="Windows"), \
             patch.object(terminal_platform, "_windows_terminal_on_windows", return_value="wt.exe"), \
             patch.object(terminal_platform.subprocess, "Popen") as popen:
            terminal_platform.open_command(command, "name;with separator")
        args = popen.call_args.args[0]
        self.assertIn("-EncodedCommand", args)
        decoded = base64.b64decode(args[-1]).decode("utf-16le")
        self.assertEqual(command, decoded)
        self.assertNotIn(command, args)
        self.assertEqual(r"name\;with separator", args[args.index("--title") + 1])

    def test_windows_port_forward_parameters_remain_one_literal_argument(self):
        with patch("services.ssm_commands.platform.system", return_value="Windows"):
            command = build_port_forward_command("aws", "i-test", "profile;name", "eu-west-1", 8080, 443)
        self.assertIn("--parameters 'portNumber=443,localPortNumber=8080'", command)
        self.assertIn("--profile 'profile;name'", command)

    def test_wsl_escapes_windows_terminal_separator_without_changing_terminal_selection(self):
        command = "aws ssm start-session --profile 'profile;name'"
        with patch.object(terminal_platform.platform, "system", return_value="Linux"), \
             patch.object(terminal_platform, "_is_wsl", return_value=True), \
             patch.object(terminal_platform, "_windows_terminal_in_wsl", return_value="wt.exe"), \
             patch.object(terminal_platform.subprocess, "Popen") as popen:
            terminal_platform.open_command(command, "name;title")
        args = popen.call_args.args[0]
        self.assertIn("wsl.exe", args)
        self.assertEqual(command.replace(";", r"\;"), args[-1])

    def test_macos_script_escapes_applescript_strings_and_handles_no_windows(self):
        command = 'printf \'quote " and backslash \\\'; printf "\\n"'
        title = 'título "quoted"\\line\nnext'
        with patch.object(terminal_platform.platform, "system", return_value="Darwin"), \
             patch.object(terminal_platform.subprocess, "Popen") as popen:
            terminal_platform.open_command(command, title)
        script = popen.call_args.args[0][-1]
        self.assertIn('count of windows', script)
        self.assertIn('set sessionTab to do script "', script)
        self.assertIn('set custom title of sessionTab to "', script)
        self.assertIn(r'\"quoted\"', script)
        self.assertIn(r'\\line\nnext', script)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Requires Windows PowerShell")
    def test_windows_native_arguments_preserve_spaces_quotes_unicode_and_metacharacters(self):
        values = ["plain", "", "spaces and áccents", "apostrophe's", "d’équipe", "left‘quote", "low‚quote", "high‛quote",
                  'double"quote', 'space "quoted" end', 'slash\\"quoted', "C:\\folder with spaces\\",
                  "ops;$(Write-Output unexpected)", "literal|&<>%PATH%`text"]
        code = "import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=True))"
        with patch("services.ssm_commands.platform.system", return_value="Windows"):
            executable = _executable(getattr(sys, "_base_executable", sys.executable))
            command = executable + " " + " ".join(shell_quote(v) for v in ["-c", code, *values])
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                 base64.b64encode(command.encode("utf-16le")).decode("ascii")],
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(values, json.loads(result.stdout.strip()))
