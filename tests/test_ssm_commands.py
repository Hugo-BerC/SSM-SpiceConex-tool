from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

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
