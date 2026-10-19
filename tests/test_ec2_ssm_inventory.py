from __future__ import annotations

import unittest
from unittest.mock import patch

from aws.ec2 import list_instances


class _Paginator:
    def __init__(self, pages): self.pages = pages
    def paginate(self): return self.pages


class _Ec2:
    def get_paginator(self, name):
        self.name = name
        return _Paginator([{"Reservations": [{"Instances": [
            {"InstanceId": "i-online", "State": {"Name": "running"}, "PlatformDetails": "Linux/UNIX"},
            {"InstanceId": "i-unmanaged", "State": {"Name": "running"}, "PlatformDetails": "Linux/UNIX"},
        ]}]}])


class _Ssm:
    def get_paginator(self, name):
        self.name = name
        return _Paginator([{"InstanceInformationList": [{
            "InstanceId": "i-online", "PingStatus": "Online", "AgentVersion": "3.2.1",
        }]}])


class Ec2SsmInventoryTests(unittest.TestCase):
    @patch("aws.ec2.get_ssm_client", return_value=_Ssm())
    @patch("aws.ec2.get_ec2_client", return_value=_Ec2())
    def test_fleet_is_enriched_with_ssm_managed_and_ping_status(self, _ec2, _ssm):
        instances = list_instances("engineering", "eu-west-1")
        online, unmanaged = instances
        self.assertTrue(online.ssm_managed)
        self.assertEqual("Online", online.ssm_ping_status)
        self.assertTrue(online.session_ready)
        self.assertFalse(unmanaged.ssm_managed)
        self.assertFalse(unmanaged.session_ready)

    @patch("aws.ec2.get_ssm_client", side_effect=RuntimeError("Access denied"))
    @patch("aws.ec2.get_ec2_client", return_value=_Ec2())
    def test_missing_ssm_inventory_permission_does_not_hide_ec2_fleet(self, _ec2, _ssm):
        instances = list_instances("engineering", "eu-west-1")
        self.assertEqual(2, len(instances))
        self.assertIsNone(instances[0].ssm_managed)
        self.assertEqual("SSM UNKNOWN", instances[0].ssm_status_label)
