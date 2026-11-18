import unittest
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch

from aws.sso import _authorization_url, _complete_device_url, login_profile

PKCE_URL = ("https://oidc.eu-west-1.amazonaws.com/authorize?response_type=code&client_id=test-client"
            "&redirect_uri=http%3A%2F%2F127.0.0.1%3A1234%2Foauth%2Fcallback&code_challenge=test-challenge&state=test-state")


class FakeLoginProcess:
    def __init__(self, outputs=(), *, repeat_output="", return_code=0, cleanup_hangs=False):
        self.outputs = list(outputs)
        self.repeat_output = repeat_output
        self.final_code = return_code
        self.returncode = None
        self.killed = False
        self.timeouts = []
        self.cleanup_hangs = cleanup_hangs

    def communicate(self, timeout):
        self.timeouts.append(timeout)
        if self.killed:
            if self.cleanup_hangs:
                raise subprocess.TimeoutExpired("fake-aws", timeout)
            return "", None
        time.sleep(min(timeout, 0.005))
        if self.outputs:
            value = self.outputs.pop(0)
            if isinstance(value, Exception):
                raise value
            self.returncode = self.final_code
            return value, None
        raise subprocess.TimeoutExpired("fake-aws", timeout, output=self.repeat_output)

    def kill(self):
        self.killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode


class SsoOutputParsingTests(unittest.TestCase):
    def test_pkce_and_dual_stack_authorization_urls_are_recognized(self):
        self.assertEqual(PKCE_URL, _authorization_url([PKCE_URL]))
        dual_stack = PKCE_URL.replace("amazonaws.com", "api.aws")
        self.assertEqual(dual_stack, _authorization_url([dual_stack]))

    def test_device_base_url_is_not_opened_before_code_arrives(self):
        self.assertIsNone(_authorization_url(["https://example.awsapps.com/start/#/device"]))

    def test_device_url_preserves_existing_fragment_query_parameters(self):
        result = _complete_device_url(["https://example.awsapps.com/start/#/device?locale=en",
                                       "Then enter the code:", "ABCD-EFGH"])
        self.assertEqual("https://example.awsapps.com/start/#/device?locale=en&user_code=ABCD-EFGH", result)

    def test_standard_device_sso_endpoint_is_supported(self):
        result = _complete_device_url(["https://device.sso.eu-west-1.amazonaws.com/",
                                       "Then enter the code:", "ABCD-EFGH"])
        self.assertEqual("https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH", result)

    def test_non_authorization_urls_and_insecure_urls_are_not_opened(self):
        for url in ("https://example.test/start", PKCE_URL.replace("https://", "http://", 1),
                    "https://user:secret@example.test/authorize?user_code=ABCD-EFGH"):
            self.assertIsNone(_authorization_url([url]))

    def test_current_cli_platform_authorization_uses_autofill_url(self):
        initial = "https://eu-west-1.signin.aws/platform/authorize?request_uri=initial"
        autofill = "https://eu-west-1.signin.aws/platform/authorize?request_uri=autofill"
        lines = [
            "Browser will not be automatically opened.",
            "Please visit the following URL:",
            initial,
            "Then enter the code:",
            "ABCD-EFGH",
            "Alternatively, you may visit the following URL which will autofill the code upon loading:",
            autofill,
        ]
        self.assertEqual(autofill, _complete_device_url(lines))

    def test_current_cli_waits_for_autofill_instead_of_opening_first_url(self):
        lines = [
            "Please visit the following URL:",
            "https://eu-west-1.signin.aws/platform/authorize?request_uri=initial",
            "Then enter the code:",
            "ABCD-EFGH",
        ]
        self.assertIsNone(_complete_device_url(lines))

    def test_legacy_device_flow_appends_code_from_following_line(self):
        lines = [
            "Open https://example.awsapps.com/start/#/device",
            "Then enter the code:",
            "WXYZ-1234",
        ]
        self.assertEqual(
            "https://example.awsapps.com/start/#/device?user_code=WXYZ-1234",
            _complete_device_url(lines),
        )

    @patch("aws.sso.sys.platform", "win32")
    @patch("aws.sso.shutil.which", return_value="aws.exe")
    @patch("aws.sso.subprocess.Popen")
    def test_windows_delegates_browser_launch_to_aws_cli(self, popen, _which):
        process = MagicMock()
        process.communicate.return_value = ("Successfully logged in", None)
        process.returncode = 0
        popen.return_value = process

        self.assertIsNone(login_profile("engineering"))

        args = popen.call_args.args[0]
        self.assertEqual(["aws.exe", "sso", "login", "--profile", "engineering"], args)
        self.assertNotIn("--no-browser", args)


class SsoLoginLifecycleTests(unittest.TestCase):
    def run_login(self, process, *, platform="linux", **kwargs):
        with patch("aws.sso.sys.platform", platform), patch("aws.sso.shutil.which", return_value="aws"), \
             patch("aws.sso.subprocess.Popen", return_value=process) as popen:
            result = login_profile("engineering", use_device_code=False, **kwargs)
        return result, popen.call_args

    def test_unix_opens_complete_pkce_url_once_and_waits_for_cli_success(self):
        partial = PKCE_URL[:80]
        process = FakeLoginProcess([
            subprocess.TimeoutExpired("aws", 1, output=partial.encode()),
            subprocess.TimeoutExpired("aws", 1, output=(PKCE_URL + "\n").encode()),
            subprocess.TimeoutExpired("aws", 1, output=(PKCE_URL + "\n").encode()), "Success",
        ])
        with patch("aws.sso.webbrowser.open", return_value=True) as browser:
            result, call = self.run_login(process, timeout_seconds=1)
        self.assertEqual(PKCE_URL, result)
        browser.assert_called_once_with(PKCE_URL, new=2)
        self.assertIn("--no-browser", call.args[0])
        self.assertFalse(process.killed)

    def test_unix_opens_autofill_url_without_user_code_query_parameter(self):
        initial = "https://eu-west-1.signin.aws/platform/authorize?request_uri=initial"
        complete = "https://eu-west-1.signin.aws/platform/authorize?request_uri=autofill"
        output = f"Please visit the following URL:\n{initial}\nThen enter the code:\nABCD-EFGH\nAlternatively, autofill the code:\n{complete}\n"
        process = FakeLoginProcess([subprocess.TimeoutExpired("aws", 1, output=output), "Success"])
        with patch("aws.sso.webbrowser.open", return_value=True) as browser:
            result, _call = self.run_login(process, timeout_seconds=1)
        self.assertEqual(complete, result)
        browser.assert_called_once_with(complete, new=2)

    def test_timeout_before_any_output_kills_cli_and_never_opens_browser(self):
        process = FakeLoginProcess()
        started = time.monotonic()
        with patch("aws.sso.webbrowser.open") as browser:
            with self.assertRaisesRegex(RuntimeError, "not completed"):
                self.run_login(process, timeout_seconds=0.04)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(process.killed)
        browser.assert_not_called()

    def test_blocked_browser_does_not_extend_authorization_deadline(self):
        entered, release = threading.Event(), threading.Event()
        process = FakeLoginProcess(repeat_output=PKCE_URL + "\n")
        def open_browser(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return True
        started = time.monotonic()
        try:
            with patch("aws.sso.webbrowser.open", side_effect=open_browser):
                with self.assertRaisesRegex(RuntimeError, "not completed"):
                    self.run_login(process, timeout_seconds=0.06)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(process.killed)
        finally:
            release.set()

    def test_missing_browser_has_actionable_error_without_authorization_data(self):
        process = FakeLoginProcess(repeat_output=PKCE_URL + "\n")
        with patch("aws.sso.webbrowser.open", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Could not open a browser") as error:
                self.run_login(process, timeout_seconds=0.5)
        self.assertNotIn("test-challenge", str(error.exception))
        self.assertTrue(process.killed)

    def test_failed_cli_output_is_not_copied_to_logs_or_exception(self):
        output = f"InvalidGrantException: rejected\n{PKCE_URL}\nABCD-EFGH\naccess_token=synthetic-secret"
        process = FakeLoginProcess([output], return_code=1)
        with patch("aws.sso.logger") as logger, patch("aws.sso.webbrowser.open") as browser:
            with self.assertRaisesRegex(RuntimeError, "InvalidGrantException") as error:
                self.run_login(process, timeout_seconds=1)
        diagnostic = str(logger.mock_calls) + str(error.exception)
        for value in (PKCE_URL, "ABCD-EFGH", "synthetic-secret", "test-challenge"):
            self.assertNotIn(value, diagnostic)
        browser.assert_not_called()

    def test_denial_and_network_failures_have_distinct_safe_diagnostics(self):
        for output, expected in [("AccessDeniedException: user denied authorization", "denied or cancelled"),
                                 ("Could not connect to the endpoint URL", "could not be reached")]:
            process = FakeLoginProcess([output + "\n" + PKCE_URL], return_code=1)
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, expected) as error:
                self.run_login(process, timeout_seconds=1)
            self.assertNotIn(PKCE_URL, str(error.exception))

    def test_browser_exception_cannot_expose_authorization_url(self):
        process = FakeLoginProcess(repeat_output=PKCE_URL + "\n")
        with patch("aws.sso.webbrowser.open", side_effect=RuntimeError(PKCE_URL)):
            with self.assertRaisesRegex(RuntimeError, "Could not open a browser") as error:
                self.run_login(process, timeout_seconds=0.5)
        self.assertNotIn(PKCE_URL, str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_windows_timeout_and_stuck_cleanup_are_bounded(self):
        process = FakeLoginProcess(cleanup_hangs=True)
        with self.assertRaisesRegex(RuntimeError, "not completed"):
            self.run_login(process, platform="win32", timeout_seconds=0.03)
        self.assertTrue(process.killed)
        self.assertLessEqual(process.timeouts[-1], 2)

    def test_device_code_can_be_selected_explicitly_without_changing_profile(self):
        process = FakeLoginProcess(["Success"])
        with patch("aws.sso.sys.platform", "linux"), patch("aws.sso.shutil.which", return_value="aws"), \
             patch("aws.sso.subprocess.Popen", return_value=process) as popen:
            login_profile("engineering", use_device_code=True, timeout_seconds=1)
        self.assertEqual(["aws", "sso", "login", "--profile", "engineering", "--use-device-code", "--no-browser"],
                         popen.call_args.args[0])

    def test_device_code_environment_override_is_optional(self):
        process = FakeLoginProcess(["Success"])
        with patch("aws.sso.shutil.which", return_value="aws"), \
             patch("aws.sso.subprocess.Popen", return_value=process) as popen, \
             patch.dict("aws.sso.os.environ", {"SSM_SPICECONEX_SSO_DEVICE_CODE": "1"}):
            login_profile("engineering", timeout_seconds=1)
        self.assertIn("--use-device-code", popen.call_args.args[0])

    def test_invalid_deadline_is_rejected_before_starting_cli(self):
        with patch("aws.sso.subprocess.Popen") as popen:
            for value in (0, -1, float("nan"), float("inf")):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    login_profile("engineering", timeout_seconds=value)
        popen.assert_not_called()

    def test_global_deadline_includes_cli_startup_time(self):
        process = FakeLoginProcess(["Should not be consumed"])
        def start(*_args, **_kwargs):
            time.sleep(0.03)
            return process
        with patch("aws.sso.shutil.which", return_value="aws"), patch("aws.sso.subprocess.Popen", side_effect=start):
            with self.assertRaisesRegex(RuntimeError, "not completed"):
                login_profile("engineering", timeout_seconds=0.01)
        self.assertTrue(process.killed)
        self.assertEqual([2], process.timeouts)

    def test_owned_local_process_without_output_is_terminated_on_deadline(self):
        original_popen = subprocess.Popen
        created = []
        def start(_args, **kwargs):
            process = original_popen([sys.executable, "-B", "-c", "import time; time.sleep(10)"], **kwargs)
            created.append(process)
            return process
        try:
            with patch("aws.sso.shutil.which", return_value=sys.executable), \
                 patch("aws.sso.subprocess.Popen", side_effect=start):
                with self.assertRaisesRegex(RuntimeError, "not completed"):
                    login_profile("engineering", timeout_seconds=0.05)
            self.assertIsNotNone(created[0].poll())
        finally:
            for process in created:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
