"""Regression tests for AWS context, operation reentry and window shutdown."""
from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import pyside6_poc.main as ui
from models.instance import EC2Instance
from pyside6_poc.tasks import start_task, task_busy
from services.discovery.models import InfrastructureGraph, ResourceNode
from services.ssm import CommandResult


class OfflineIcons(QObject):
    changed = Signal()

    def path(self, _resource_type):
        return None

    def request(self, _resource_type):
        pass


class OperationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.icons = patch.object(ui, "AwsIconProvider", OfflineIcons)
        self.icons.start()
        self.dialogs = patch.object(ui, "show_dialog")
        self.dialogs.start()
        self.errors = patch.object(ui, "show_exception")
        self.errors.start()
        self.window = ui.MainWindow(demo=True)
        self.window.power.demo = False
        self.window.home.demo = False
        self.commands = self.window.home.commands
        self.commands.profile.addItem("other-account")
        self.release = threading.Event()
        self.instance = EC2Instance("i-original", "original", "192.0.2.1",
                                    "Linux", "running", True, "Online")

    def tearDown(self):
        self.release.set()
        self.wait_until(lambda: not self.window.active_threads())
        # Also drain queued results and QThread.finished cleanup before deletion.
        self.wait_until(lambda: all(not task_busy(page) for page in (
            self.window.home, self.window.power, self.window.command,
            self.window.cloudfront, self.window.connectivity, self.window.flow_logs,
            self.window.architecture, self.window.architecture.dr_page,
        )))
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.errors.stop(); self.dialogs.stop(); self.icons.stop()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "Qt operation did not finish")

    def blocked(self, result):
        self.release.wait(2)
        return result

    def select_target(self):
        self.window.power.set_instances([self.instance])
        self.window.power.fleet.rows[0].check.setChecked(True)

    def test_late_fleet_result_is_discarded_even_after_returning_to_original_profile(self):
        original = self.commands.profile.currentText()
        with patch.object(ui, "list_instances", side_effect=lambda *_: self.blocked([self.instance])), \
             patch.object(ui, "_run_with_sso", side_effect=lambda _profile, operation: operation()):
            self.window.home.connect_aws()
            self.commands.profile.setCurrentText("other-account")
            self.commands.profile.setCurrentText(original)
            self.release.set()
            self.wait_until(lambda: not task_busy(self.window.power))
        self.assertEqual([], self.window.power.instances)
        self.assertEqual(0, self.window.tunnel.target.count())
        self.assertFalse(self.window.power.open_sessions.isEnabled())
        self.assertTrue(self.commands.connect.isEnabled())

    def test_stale_sts_validation_does_not_load_a_different_region(self):
        with patch.object(ui, "get_caller_identity", side_effect=lambda *_: self.blocked({"Account": "123456789012"})), \
             patch.object(ui, "_run_with_sso", side_effect=lambda _profile, operation: operation()), \
             patch.object(self.window.power, "load") as load:
            self.window.home.validate_profile()
            self.commands.region.setCurrentText("eu-south-2")
            self.release.set()
            self.wait_until(lambda: not task_busy(self.window.home))
        load.assert_not_called()
        self.assertTrue(self.commands.validate.isEnabled())

    def test_profile_change_invalidates_loaded_resources_and_exports(self):
        self.select_target()
        cert = self.window.certificates
        cert.finished({"certificates": [{"Domain": "example.test"}], "balancers": []})
        route = self.window.route53
        route.job = "zones"
        route.finished({"zones": [{"Id": "ZOLD", "Name": "example.test", "RecordSetCount": 1}]})
        self.window.cloudfront.finished({"profile": "original", "distributions": [{"DomainName": "example.test"}]})
        graph = InfrastructureGraph("123456789012", "original", ["eu-west-1"])
        graph.add_node(ResourceNode("i-original", None, "EC2", "original", "eu-west-1", "123456789012"))
        self.window.architecture._populate(graph)
        flow = self.window.flow_logs
        flow._logs_loaded({
            "vpcs": [{"VpcId": "vpc-original"}],
            "interfaces": [{"VpcId": "vpc-original", "InterfaceId": "eni-original",
                            "PrivateIp": "192.0.2.1", "Resource": "original"}],
            "flow_logs": [{"VpcId": "vpc-original", "ResourceId": "vpc-original",
                           "DestinationType": "cloud-watch-logs", "LogGroupName": "original"}],
        })
        flow._analysis_ready({"vpc_id": "vpc-original", "rows": [{
            "Origin": "192.0.2.1", "Destination": "192.0.2.2", "Protocol": "6", "Port": "443",
            "Direction": "OUTBOUND", "Result": "ACCEPT", "Observations": 1,
        }]})
        self.commands.profile.setCurrentText("other-account")
        self.assertEqual([], self.window.power.instances)
        self.assertEqual(0, cert.cert_table.table.rowCount())
        self.assertFalse(cert.cert_export.isEnabled())
        self.assertEqual({}, route.zones_by_id)
        self.assertFalse(route.discover_records.isEnabled())
        self.assertEqual([], self.window.cloudfront.distributions)
        self.assertFalse(self.window.cloudfront.export.isEnabled())
        self.assertIsNone(self.window.architecture.graph)
        self.assertIsNone(self.window.architecture.dr_page.graph)
        self.assertFalse(self.window.architecture.resource_export.isEnabled())
        self.assertEqual([], flow.rows)
        self.assertEqual(0, flow.interface.count())
        self.assertFalse(flow.export_csv.isEnabled())

    def test_clearing_architecture_discards_discovery_already_in_progress(self):
        page = self.window.architecture
        page.demo = False
        graph = InfrastructureGraph("123456789012", "original", ["eu-west-1"])
        graph.add_node(ResourceNode("i-old", None, "EC2", "old", "eu-west-1", "123456789012"))
        with patch.object(ui, "discover_infrastructure", side_effect=lambda *_: self.blocked(graph)), \
             patch.object(ui, "_run_with_sso", side_effect=lambda _profile, operation: operation()):
            page.run_discovery()
            page.clear_view()
            self.release.set()
            self.wait_until(lambda: not task_busy(page))
        self.assertIsNone(page.graph)
        self.assertFalse(page.resource_export.isEnabled())
        self.assertTrue(page.discover.isEnabled())

    def test_refreshing_same_profile_also_invalidates_loaded_fleet(self):
        self.select_target()
        profile = self.commands.profile.currentText()
        with patch.object(ui, "discover_profiles", return_value=[profile]):
            self.window.home.load_profiles()
        self.assertEqual([], self.window.power.instances)
        self.assertEqual(profile, self.commands.profile.currentText())

    def test_cloudfront_discards_inflight_inventory_and_restores_discover_button(self):
        page = self.window.cloudfront
        with patch.object(ui, "discover_distributions", side_effect=lambda *_: self.blocked(
            {"distributions": [{"DomainName": "old.example.test"}]})), \
             patch.object(ui, "_run_with_sso", side_effect=lambda _profile, operation: operation()):
            page.run_discovery()
            self.commands.region.setCurrentText("eu-south-2")
            self.release.set()
            self.wait_until(lambda: not task_busy(page))
        self.assertEqual([], page.distributions)
        self.assertFalse(page.export.isEnabled())
        self.assertTrue(page.discover.isEnabled())

    def test_editing_command_cannot_reenable_or_submit_a_second_operation(self):
        self.select_target()
        page = self.window.command
        page.command.setPlainText("uname -a")
        result = CommandResult(self.instance.instance_id, "Success", "output", "", "original-command")
        with patch.object(ui, "confirm_operation", return_value=True), \
             patch.object(ui, "send_command", side_effect=lambda *args, **kwargs: self.blocked([result])) as send:
            page.run_command()
            page.command.setPlainText("another command")
            self.assertFalse(page.execute.isEnabled())
            page.run_command()
            self.release.set()
            self.wait_until(lambda: not task_busy(page))
        send.assert_called_once()
        self.assertIs(ui._run_with_sso, send.call_args.kwargs["run_with_sso"])
        self.assertIn("original-command", page.output.toPlainText())
        self.assertTrue(page.execute.isEnabled())

    def test_failed_command_cleans_up_and_can_be_retried_by_operator(self):
        self.select_target()
        page = self.window.command
        page.command.setPlainText("uname -a")
        with patch.object(ui, "confirm_operation", return_value=True), \
             patch.object(ui, "send_command", side_effect=RuntimeError("Access denied")):
            page.run_command()
            self.wait_until(lambda: not task_busy(page))
        self.assertEqual("ERROR", page.output_status.text())
        self.assertTrue(page.execute.isEnabled())
        self.assertIsNone(page.worker)

    def test_window_refuses_to_close_with_active_worker(self):
        page = self.window.command
        start_task(page, lambda: self.blocked("done"), lambda _result: None,
                   lambda _exc: None, lambda: None)
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.release.set()
        self.wait_until(lambda: not task_busy(page))
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_update_cannot_close_window_while_another_operation_runs(self):
        page = self.window.command
        start_task(page, lambda: self.blocked("done"), lambda _result: None,
                   lambda _exc: None, lambda: None)
        info = {"available": True, "can_apply": True, "installed_version": "0.5.1",
                "available_version": "0.5.2", "tag": "v0.5.2"}
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(ui, "launch_update_process") as launch:
            self.window._update_check_finished(info)
        launch.assert_not_called()
        self.assertIsNone(self.window._pending_update_tag)

    def test_dr_context_change_suppresses_old_errors_and_keeps_reentry_blocked(self):
        page = self.window.architecture.dr_page
        def operation():
            self.release.wait(2)
            raise RuntimeError("old account error")
        with patch.object(page, "show_error") as show_error, \
             patch.object(QMessageBox, "warning") as warning:
            page._run_worker(operation, lambda _result: None)
            page.prepare()
            warning.assert_not_called()
            self.commands.profile.setCurrentText("other-account")
            self.release.set()
            self.wait_until(lambda: not task_busy(page))
        show_error.assert_not_called()
        self.assertTrue(page.prepare_button.isEnabled())
        self.assertFalse(page.publish.isEnabled())

    def test_local_ssm_tracking_timeout_is_not_presented_as_remote_completion(self):
        result = CommandResult("i-original", "TimedOut", "partial", "Remote status unknown",
                               "original-command", tracking_timed_out=True)
        self.window.command.finished([result])
        self.assertIn("REMOTE STATUS UNKNOWN", self.window.command.output_status.text())
        self.assertIn("original-command", self.window.command.output.toPlainText())

    def test_partial_connectivity_reports_missing_results_separately_from_network_failures(self):
        self.select_target()
        page = self.window.connectivity
        row = {"service": "api", "destination": "example.test", "port": "443",
               "scope": "internal", "protocol": "TCP"}
        page.rows = [dict(row), dict(row)]
        results = [{**row, "status": "OK", "duration": "10", "detail": "connected"},
                   {**row, "status": "UNKNOWN", "duration": "", "detail": "Remote status unknown"}]
        command = CommandResult("i-original", "TimedOut", "partial", "Remote status unknown",
                                "original-command", tracking_timed_out=True)
        page.finished((results, command))
        self.assertIn("Partial validation", page.summary.text())
        self.assertIn("KO 0", page.summary.text())
        self.assertIn("No result 1", page.summary.text())
        self.assertIn("original-command", page.summary.text())
        from PySide6.QtWidgets import QGraphicsTextItem
        labels = {item.toPlainText() for item in page.connection_map.scene.items()
                  if isinstance(item, QGraphicsTextItem)}
        self.assertIn("OK", labels)
        self.assertIn("UNKNOWN", labels)
