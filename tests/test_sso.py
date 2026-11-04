import unittest

from aws.sso import _complete_device_url


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


if __name__ == "__main__":
    unittest.main()
