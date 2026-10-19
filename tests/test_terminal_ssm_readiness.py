from __future__ import annotations
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.config import APP_VERSION
from models.instance import EC2Instance
from pyside6_poc.main import FleetPanel, InstanceRow, Sidebar


class TerminalSsmReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sidebar_exposes_the_same_application_version_as_the_updater(self):
        sidebar = Sidebar()
        self.assertEqual(f"VERSION {APP_VERSION}", sidebar.version.text())
        sidebar.set_connection_state("error", "◆  AWS CONNECTION ERROR")
        self.assertEqual("◆  AWS CONNECTION ERROR", sidebar.status.text())
        self.assertEqual("error", sidebar.status.property("connectionState"))

    def test_only_online_ssm_instances_are_selectable_and_tunnel_eligible(self):
        online = EC2Instance("i-online", "online", "10.0.0.1", "Linux", "running", True, "Online")
        lost = EC2Instance("i-lost", "lost", "10.0.0.2", "Linux", "running", True, "ConnectionLost")
        unmanaged = EC2Instance("i-unmanaged", "unmanaged", "10.0.0.3", "Linux", "running", False)

        online_row = InstanceRow(online)
        lost_row = InstanceRow(lost)
        unmanaged_row = InstanceRow(unmanaged)
        self.assertTrue(online_row.check.isEnabled())
        self.assertFalse(lost_row.check.isEnabled())
        self.assertFalse(unmanaged_row.check.isEnabled())

        unknown = EC2Instance("i-unknown", "unknown", "10.0.0.4", "Linux", "running")
        self.assertTrue(unknown.session_action_eligible)
        self.assertFalse(lost.session_action_eligible)
        self.assertFalse(unmanaged.session_action_eligible)

        # Constructing the full page is unnecessary: validate the selection
        # predicate used by Tunneling directly as a stable operational contract.
        eligible = [item for item in (online, lost, unmanaged, unknown) if item.session_action_eligible]
        self.assertEqual([online, unknown], eligible)

    def test_fleet_can_be_cleared_repeatedly_with_rows_and_layout_spacers(self):
        fleet = FleetPanel()
        instance = EC2Instance("i-online", "online", "10.0.0.1", "Linux", "running", True, "Online")
        fleet.set_instances([instance])
        fleet.set_instances([])
        fleet.set_instances([])
        self.assertEqual([], fleet.rows)
