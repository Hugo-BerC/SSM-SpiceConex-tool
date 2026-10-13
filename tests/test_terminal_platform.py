from __future__ import annotations

import unittest
from unittest.mock import patch

from terminal import platform as terminal_platform


class TerminalPlatformTests(unittest.TestCase):
    def test_xterm_fallback_has_an_explicit_dark_large_font_theme(self):
        args = terminal_platform._xterm_arguments("/usr/bin/xterm", "SSM", "echo ready")
        self.assertIn("#0B0A08", args)
        self.assertIn("#F0E8D8", args)
        self.assertIn("18", args)
        self.assertIn("150x42", args)
        self.assertEqual(["-e", "bash", "-lc", "echo ready"], args[-4:])

    def test_wsl_prefers_windows_terminal_before_linux_fallbacks(self):
        with patch.object(terminal_platform.platform, "system", return_value="Linux"), patch.object(terminal_platform, "_is_wsl", return_value=True), patch.object(terminal_platform, "_windows_terminal_in_wsl", return_value="/windows/wt.exe"), patch.object(terminal_platform.subprocess, "Popen") as popen:
            terminal_platform.open_command("aws ssm start-session", "SSM target")
        args = popen.call_args.args[0]
        self.assertEqual("/windows/wt.exe", args[0])
        self.assertIn("new-tab", args)
        self.assertNotIn("xterm", args)

    def test_native_windows_prefers_windows_terminal(self):
        with patch.object(terminal_platform.platform, "system", return_value="Windows"), patch.object(terminal_platform, "_windows_terminal_on_windows", return_value="C:/wt.exe"), patch.object(terminal_platform.subprocess, "Popen") as popen:
            terminal_platform.open_command("echo ready", "SSM target")
        args = popen.call_args.args[0]
        self.assertEqual("C:/wt.exe", args[0])
        self.assertIn("new-tab", args)
