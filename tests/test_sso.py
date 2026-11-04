import unittest
from unittest.mock import MagicMock, patch

from aws.sso import _complete_device_url, login_profile


class SsoOutputParsingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
