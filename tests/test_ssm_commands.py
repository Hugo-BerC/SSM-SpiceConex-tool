from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from services.ssm import MAX_COMMAND_TARGETS, send_command


class SsmCommandTests(unittest.TestCase):
    def _client(self):
        client = Mock()
        client.send_command.return_value = {"Command": {"CommandId": "command-123"}}
        client.get_command_invocation.side_effect = [
            {"Status": "Success", "StandardOutputContent": "one"},
            {"Status": "Failed", "StandardErrorContent": "two"},
        ]
        client.exceptions.InvocationDoesNotExist = type("InvocationDoesNotExist", (Exception,), {})
        return client

    @patch("services.ssm.get_ssm_client")
    def test_submits_one_command_for_all_targets_and_collects_each_result(self, get_client):
        client = self._client()
        get_client.return_value = client

        results = send_command("audit", "eu-west-1", ["i-1", "i-2"], "uname -a", poll_interval=0)

        self.assertEqual(1, client.send_command.call_count)
        request = client.send_command.call_args.kwargs
        self.assertEqual(["i-1", "i-2"], request["InstanceIds"])
        self.assertEqual("AWS-RunShellScript", request["DocumentName"])
        self.assertEqual("0", request["MaxErrors"])
        self.assertEqual(["i-1", "i-2"], [result.instance_id for result in results])
        self.assertEqual({"Success", "Failed"}, {result.status for result in results})

    def test_rejects_an_unbounded_target_selection(self):
        ids = [f"i-{index}" for index in range(MAX_COMMAND_TARGETS + 1)]
        with self.assertRaisesRegex(ValueError, "limited"):
            send_command("audit", "eu-west-1", ids, "uname -a")

    @patch("services.ssm.get_ssm_client")
    def test_expired_credentials_during_polling_never_resubmit_command(self, get_client):
        original, refreshed = self._client(), self._client()
        original.get_command_invocation.side_effect = ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "ExpiredToken"}},
            "GetCommandInvocation",
        )
        refreshed.get_command_invocation.side_effect = None
        refreshed.get_command_invocation.return_value = {"Status": "Success"}
        get_client.side_effect = [original, refreshed]

        def recover(profile, operation):
            self.assertEqual("audit", profile)
            try:
                return operation()
            except ClientError:
                return operation()

        results = send_command("audit", "eu-west-1", ["i-1"], "uname -a",
                               poll_interval=0, run_with_sso=recover)

        original.send_command.assert_called_once()
        refreshed.send_command.assert_not_called()
        self.assertEqual("command-123", results[0].command_id)
        refreshed.get_command_invocation.assert_called_once_with(
            CommandId="command-123", InstanceId="i-1",
        )

    @patch("services.ssm.get_ssm_client")
    def test_authentication_failure_before_submission_can_recover(self, get_client):
        original, refreshed = self._client(), self._client()
        original.send_command.side_effect = ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "ExpiredToken"}}, "SendCommand",
        )
        get_client.side_effect = [original, refreshed]

        def recover(_profile, operation):
            try:
                return operation()
            except ClientError:
                return operation()

        result = send_command("audit", "eu-west-1", ["i-1"], "uname -a",
                              poll_interval=0, run_with_sso=recover)
        self.assertEqual("command-123", result[0].command_id)
        original.send_command.assert_called_once()
        refreshed.send_command.assert_called_once()

    @patch("services.ssm.get_ssm_client")
    def test_uncertain_submission_failure_is_not_retried(self, get_client):
        client = self._client()
        client.send_command.side_effect = TimeoutError("Response lost")
        get_client.return_value = client
        with self.assertRaises(TimeoutError):
            send_command("audit", "eu-west-1", ["i-1"], "uname -a")
        client.send_command.assert_called_once()

    @patch("services.ssm.get_ssm_client")
    def test_recovery_preserves_command_id_with_real_ssm_request_validation(self, get_client):
        clients = [boto3.client("ssm", region_name="eu-west-1", aws_access_key_id="testing",
                                aws_secret_access_key="testing") for _ in range(2)]
        get_client.side_effect = clients
        command_id = "00000000-0000-0000-0000-000000000001"
        instance_id = "i-0123456789abcdef0"
        invocation = {"CommandId": command_id, "InstanceId": instance_id}
        with Stubber(clients[0]) as original, Stubber(clients[1]) as refreshed:
            original.add_response("send_command", {"Command": {"CommandId": command_id}}, {
                "InstanceIds": [instance_id], "DocumentName": "AWS-RunShellScript",
                "Parameters": {"commands": ["uname -a"]}, "TimeoutSeconds": 300,
                "MaxConcurrency": "1", "MaxErrors": "0",
            })
            original.add_client_error("get_command_invocation", "ExpiredToken",
                                      expected_params=invocation)
            refreshed.add_response("get_command_invocation", {"Status": "Success"}, invocation)

            def recover(_profile, operation):
                try:
                    return operation()
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ExpiredToken":
                        raise
                    return operation()

            result = send_command("audit", "eu-west-1", [instance_id], "uname -a",
                                  poll_interval=0, run_with_sso=recover)
            self.assertEqual(command_id, result[0].command_id)
            original.assert_no_pending_responses()
            refreshed.assert_no_pending_responses()

    @patch("services.ssm.get_ssm_client")
    def test_local_tracking_timeout_preserves_partial_output_and_remote_status_uncertainty(self, get_client):
        client = self._client(); get_client.return_value = client
        now = [0]
        def invocation(**_kwargs):
            now[0] = 11
            return {"Status": "InProgress", "StandardOutputContent": "partial output"}
        client.get_command_invocation.side_effect = invocation
        with patch("services.ssm.time.monotonic", side_effect=lambda: now[0]), patch("services.ssm.time.sleep"):
            result = send_command("audit", "eu-west-1", ["i-1"], "uname -a", timeout_seconds=10)[0]
        self.assertTrue(result.tracking_timed_out)
        self.assertEqual("TimedOut", result.status)
        self.assertEqual("partial output", result.stdout)
        self.assertIn("may still be running", result.stderr)
        client.send_command.assert_called_once()

    @patch("services.ssm.get_ssm_client")
    def test_remote_timeout_is_not_a_local_tracking_timeout(self, get_client):
        client = self._client(); get_client.return_value = client
        client.get_command_invocation.side_effect = [{"Status": "TimedOut"}]
        result = send_command("audit", "eu-west-1", ["i-1"], "uname -a")[0]
        self.assertFalse(result.tracking_timed_out)

    @patch("services.ssm.get_ssm_client")
    def test_explicit_delivery_and_execution_limits_are_independent_of_polling_budget(self, get_client):
        client = self._client(); get_client.return_value = client
        send_command("audit", "eu-west-1", ["i-1"], "uname -a", timeout_seconds=600,
                     delivery_timeout_seconds=60, execution_timeout_seconds=450)
        request = client.send_command.call_args.kwargs
        self.assertEqual(60, request["TimeoutSeconds"])
        self.assertEqual(["450"], request["Parameters"]["executionTimeout"])
