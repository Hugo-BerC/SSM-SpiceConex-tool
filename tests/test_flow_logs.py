from __future__ import annotations

import ipaddress
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox

from pyside6_poc.main import FlowLogsPage
from services.flow_logs import _direction, _rows, build_insights_query, discover_vpc_flow_inventory


class FlowLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # Qt's offscreen backend can retain orphaned top-level widgets until
        # interpreter shutdown. Dispose them between UI tests explicitly.
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def test_page_exposes_minimal_connection_columns_and_two_views(self):
        profile, region = QComboBox(), QComboBox()
        profile.addItem("engineering"); region.addItem("eu-west-1")
        page = FlowLogsPage(SimpleNamespace(commands=SimpleNamespace(profile=profile, region=region)))
        self.assertEqual(["Origin", "Destination", "Protocol", "Port", "Result"], page.table.headers)
        self.assertEqual(["CONNECTIONS", "CONNECTION MAP"], [page.tabs.tabText(index) for index in range(page.tabs.count())])

    def test_page_only_lists_enis_with_a_covering_cloudwatch_flow_log(self):
        profile, region = QComboBox(), QComboBox()
        profile.addItem("engineering"); region.addItem("eu-west-1")
        page = FlowLogsPage(SimpleNamespace(commands=SimpleNamespace(profile=profile, region=region)))
        page.flow_logs = [
            {"VpcId": "vpc-123", "ResourceId": "eni-covered", "DestinationType": "cloud-watch-logs", "Scope": "INTERFACE", "LogGroupName": "flow-group"},
            {"VpcId": "vpc-123", "ResourceId": "eni-s3-only", "DestinationType": "s3", "Scope": "INTERFACE"},
        ]
        page.interfaces = [
            {"VpcId": "vpc-123", "SubnetId": "subnet-1", "InterfaceId": "eni-covered", "PrivateIp": "10.0.0.10", "Resource": "covered"},
            {"VpcId": "vpc-123", "SubnetId": "subnet-1", "InterfaceId": "eni-s3-only", "PrivateIp": "10.0.0.11", "Resource": "not usable"},
            {"VpcId": "vpc-123", "SubnetId": "subnet-1", "InterfaceId": "eni-no-log", "PrivateIp": "10.0.0.12", "Resource": "no log"},
        ]
        page.vpc.addItem("vpc-123", "vpc-123")
        page.vpc.setCurrentIndex(0)
        page._vpc_selected()
        self.assertEqual(1, page.interface.count())
        self.assertEqual("eni-covered", page.interface.currentData()["InterfaceId"])
        self.assertEqual("eni-covered", page.selected_flow_log["ResourceId"])

    def test_vpc_selector_includes_the_aws_name_tag(self):
        profile, region = QComboBox(), QComboBox()
        profile.addItem("engineering"); region.addItem("eu-west-1")
        page = FlowLogsPage(SimpleNamespace(commands=SimpleNamespace(profile=profile, region=region)))
        page._logs_loaded({
            "vpcs": [{"VpcId": "vpc-123", "Name": "payments-production"}],
            "interfaces": [],
            "flow_logs": [],
        })
        self.assertIn("vpc-123  ·  payments-production", page.vpc.itemText(0))

    def test_inventory_unpacks_vpcs_from_the_ec2_paginator_page(self):
        class Paginator:
            def __init__(self, pages): self.pages = pages
            def paginate(self, **_kwargs): return self.pages
        class Ec2:
            def get_paginator(self, name):
                pages = {
                    "describe_network_interfaces": [{"NetworkInterfaces": [{"VpcId": "vpc-123", "NetworkInterfaceId": "eni-123", "PrivateIpAddress": "10.0.1.9"}]}],
                    "describe_vpcs": [{"Vpcs": [{"VpcId": "vpc-123", "Tags": [{"Key": "Name", "Value": "payments-production"}], "CidrBlockAssociationSet": [{"CidrBlock": "10.0.0.0/16"}]}]}],
                    "describe_subnets": [{"Subnets": []}],
                    "describe_flow_logs": [{"FlowLogs": [{"FlowLogId": "fl-123", "ResourceId": "vpc-123", "LogDestinationType": "s3"}]}],
                }
                return Paginator(pages[name])
        with patch("services.flow_logs.get_ec2_client", return_value=Ec2()):
            inventory = discover_vpc_flow_inventory("profile", "eu-west-1")
        self.assertEqual("vpc-123", inventory["vpcs"][0]["VpcId"])
        self.assertEqual("payments-production", inventory["vpcs"][0]["Name"])
        self.assertEqual("vpc-123", inventory["interfaces"][0]["VpcId"])
        self.assertEqual("vpc-123", inventory["flow_logs"][0]["VpcId"])

    def test_query_groups_by_connection_dimensions_not_raw_events(self):
        query = build_insights_query("${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status}")
        self.assertIn("\n| parse @message /", query)
        self.assertIn("stats count(*) as observations", query)
        self.assertIn("by srcaddr, dstaddr, srcport, dstport, protocol, action", query)
        self.assertIn("log_status = 'OK'", query)

    def test_query_scopes_results_to_the_selected_interface(self):
        query = build_insights_query("${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status}", "eni-123")
        self.assertIn("interface_id = 'eni-123'", query)

    def test_endpoint_grouping_consolidates_ephemeral_destination_ports(self):
        query = build_insights_query("${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status}", aggregation="endpoint")
        self.assertIn("count_distinct(dstport) as port_count", query)
        self.assertNotIn("by srcaddr, dstaddr, protocol, dstport, action", query)

    def test_rows_classify_inbound_outbound_and_keep_minimal_display_columns(self):
        networks = [ipaddress.ip_network("10.0.0.0/16")]
        results = [[
            {"field": "srcaddr", "value": "10.0.4.10"}, {"field": "dstaddr", "value": "198.51.100.20"},
            {"field": "protocol", "value": "6"}, {"field": "dstport", "value": "443"},
            {"field": "action", "value": "ACCEPT"}, {"field": "observations", "value": "42"}, {"field": "bytes", "value": "1024"},
        ], [
            {"field": "srcaddr", "value": "203.0.113.4"}, {"field": "dstaddr", "value": "10.0.5.20"},
            {"field": "protocol", "value": "6"}, {"field": "dstport", "value": "22"},
            {"field": "action", "value": "REJECT"}, {"field": "observations", "value": "3"}, {"field": "bytes", "value": "0"},
        ]]
        rows, observations = _rows(results, networks)
        self.assertEqual(45, observations)
        self.assertEqual("OUTBOUND", rows[0]["Direction"])
        self.assertEqual("INBOUND", rows[1]["Direction"])
        self.assertEqual({"Origin", "Destination", "Protocol", "Port", "Result"}, set(rows[0]).intersection({"Origin", "Destination", "Protocol", "Port", "Result"}))

    def test_selected_eni_takes_precedence_over_the_vpc_cidr_for_direction(self):
        networks = [ipaddress.ip_network("10.0.0.0/16")]
        results = [[
            {"field": "srcaddr", "value": "10.0.9.10"}, {"field": "dstaddr", "value": "10.0.8.5"},
            {"field": "protocol", "value": "6"}, {"field": "dstport", "value": "443"},
            {"field": "action", "value": "ACCEPT"}, {"field": "observations", "value": "5"}, {"field": "bytes", "value": "512"},
        ], [
            {"field": "srcaddr", "value": "10.0.8.5"}, {"field": "dstaddr", "value": "10.0.9.10"},
            {"field": "protocol", "value": "6"}, {"field": "dstport", "value": "8443"},
            {"field": "action", "value": "REJECT"}, {"field": "observations", "value": "2"}, {"field": "bytes", "value": "0"},
        ]]
        rows, _ = _rows(results, networks, ["10.0.9.10"])
        self.assertEqual("OUTBOUND", rows[0]["Direction"])
        self.assertEqual("INBOUND", rows[1]["Direction"])
        self.assertEqual("INTERNAL", rows[0]["Scope"])

    def test_normalised_service_view_folds_client_ephemeral_reply_ports(self):
        networks = [ipaddress.ip_network("10.0.0.0/16")]
        results = [[
            {"field": "srcaddr", "value": "10.0.8.5"}, {"field": "dstaddr", "value": "10.0.9.10"},
            {"field": "srcport", "value": "49152"}, {"field": "dstport", "value": "443"},
            {"field": "protocol", "value": "6"}, {"field": "action", "value": "ACCEPT"},
            {"field": "observations", "value": "3"}, {"field": "bytes", "value": "100"},
        ], [
            {"field": "srcaddr", "value": "10.0.9.10"}, {"field": "dstaddr", "value": "10.0.8.5"},
            {"field": "srcport", "value": "443"}, {"field": "dstport", "value": "49152"},
            {"field": "protocol", "value": "6"}, {"field": "action", "value": "ACCEPT"},
            {"field": "observations", "value": "2"}, {"field": "bytes", "value": "50"},
        ]]
        rows, observations = _rows(results, networks, ["10.0.9.10"], True, 32768, 65535)
        self.assertEqual(5, observations)
        self.assertEqual(1, len(rows))
        self.assertEqual("10.0.8.5", rows[0]["Origin"])
        self.assertEqual("10.0.9.10", rows[0]["Destination"])
        self.assertEqual("443", rows[0]["Port"])
        self.assertEqual("INBOUND", rows[0]["Direction"])
        self.assertEqual(2, rows[0]["RawGroups"])

    def test_direction_handles_private_to_private_paths(self):
        networks = [ipaddress.ip_network("10.0.0.0/16")]
        self.assertEqual("INTERNAL", _direction("10.0.1.2", "10.0.2.3", networks))
