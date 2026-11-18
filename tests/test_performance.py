from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import pyside6_poc.main as ui
import pyside6_poc.performance_page as performance_ui
from models.instance import EC2Instance
from pyside6_poc.tasks import task_busy
from services.ebs import create_cloudwatch_dashboard, list_instance_volumes, suggested_dashboard_name


VOLUMES = [{"VolumeId": "vol-example", "Device": "/dev/sda1", "Type": "gp3", "Size": 100,
            "Iops": 3000, "Throughput": 125}]


class DashboardTests(unittest.TestCase):
    def test_names_retain_full_instance_name_and_do_not_collide(self):
        first = suggested_dashboard_name("app.production.example", "eu-west-1", "i-original")
        second = suggested_dashboard_name("app.production.example", "eu-west-1", "i-original")
        self.assertIn("app-production-example", first)
        self.assertIn("i-original", first)
        self.assertNotEqual(first, second)

    def test_existing_name_is_never_written(self):
        client = Mock()
        with patch("services.ebs.get_client", return_value=client), self.assertRaises(FileExistsError):
            create_cloudwatch_dashboard("test", "eu-west-1", "instance", VOLUMES, dashboard_name="existing")
        client.put_dashboard.assert_not_called()

    def test_permissions_failure_does_not_fall_back_to_blind_overwrite(self):
        client = Mock()
        client.get_dashboard.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetDashboard")
        with patch("services.ebs.get_client", return_value=client), self.assertRaises(ClientError):
            create_cloudwatch_dashboard("test", "eu-west-1", "instance", VOLUMES, dashboard_name="new")
        client.put_dashboard.assert_not_called()

    def test_new_name_is_published_exactly_once(self):
        client = Mock()
        client.get_dashboard.side_effect = ClientError({"Error": {"Code": "ResourceNotFound", "Message": "missing"}}, "GetDashboard")
        client.put_dashboard.return_value = {}
        with patch("services.ebs.get_client", return_value=client):
            name = create_cloudwatch_dashboard("test", "eu-west-1", "instance", VOLUMES, dashboard_name="confirmed-name")
        self.assertEqual("confirmed-name", name)
        client.put_dashboard.assert_called_once()
        self.assertEqual("confirmed-name", client.put_dashboard.call_args.kwargs["DashboardName"])

    def test_empty_or_invalid_name_is_rejected_before_any_api(self):
        with patch("services.ebs.get_client") as client:
            for name in ("", "bad/name", "x" * 256):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    create_cloudwatch_dashboard("test", "eu-west-1", "instance", VOLUMES, dashboard_name=name)
        client.assert_not_called()

    def test_volume_pages_filter_other_multiattach_instances(self):
        client = Mock()
        client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"VolumeId": "vol-first", "Attachments": [
                {"Device": "/dev/one", "InstanceId": "i-original"}, {"Device": "/dev/other", "InstanceId": "i-other"}]}]},
            {"Volumes": [{"VolumeId": "vol-second", "Attachments": [{"Device": "/dev/two", "InstanceId": "i-original"}]}]},
        ]
        with patch("services.ebs.get_ec2_client", return_value=client):
            rows = list_instance_volumes("test", "eu-west-1", "i-original")
        self.assertEqual(["vol-first", "vol-second"], [row["VolumeId"] for row in rows])


class PerformanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialogs = patch.object(performance_ui, "show_dialog"); self.dialogs.start()
        self.errors = patch.object(performance_ui, "show_exception"); self.errors.start()
        self.recover = patch.object(performance_ui, "_run_with_sso", side_effect=lambda _profile, operation: operation()); self.recover.start()
        self.window = ui.MainWindow(demo=True)
        self.window.power.demo = False
        self.page = self.window.performance
        self.instance = EC2Instance("i-original", "app.production", "192.0.2.1", "Linux", "running", True, "Online")
        self.page.set_instances([self.instance])
        self.release = threading.Event()

    def tearDown(self):
        self.release.set(); self.wait_until(lambda: not task_busy(self.page))
        self.window.close(); self.window.deleteLater(); self.app.processEvents()
        self.recover.stop(); self.errors.stop(); self.dialogs.stop()

    def wait_until(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(0.005)
        self.assertTrue(condition(), "Performance task did not complete")

    def load_volumes(self):
        self.page._volumes_loaded([dict(volume) for volume in VOLUMES]); self.page._controls()

    def test_ebs_query_runs_off_gui_thread_and_keeps_timer_responsive(self):
        threads, ticks = [], []
        def read(*_args):
            threads.append(QThread.currentThread()); self.release.wait(2); return VOLUMES
        with patch.object(performance_ui, "list_instance_volumes", side_effect=read):
            self.page.load_ebs_data()
            self.assertFalse(self.page.load_ebs.isEnabled())
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait_until(lambda: ticks and threads)
            self.assertNotEqual(self.app.thread(), threads[0])
            self.release.set(); self.wait_until(lambda: not task_busy(self.page))
        self.assertTrue(self.page.dashboard.isEnabled())

    def test_cancel_confirmation_starts_no_aws_operation(self):
        self.load_volumes()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
             patch.object(performance_ui, "create_cloudwatch_dashboard") as create:
            self.page.create_dashboard()
        create.assert_not_called()
        self.assertFalse(task_busy(self.page))

    def test_confirmed_publication_runs_off_gui_thread_with_exact_name(self):
        self.load_volumes()
        self.page.dashboard_name.setText("confirmed-dashboard")
        threads = []
        def publish(*_args, **kwargs):
            threads.append(QThread.currentThread()); self.assertEqual("confirmed-dashboard", kwargs["dashboard_name"])
            return "confirmed-dashboard"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as confirmation, \
             patch.object(performance_ui, "create_cloudwatch_dashboard", side_effect=publish):
            self.page.create_dashboard(); self.wait_until(lambda: not task_busy(self.page))
        self.assertIn("confirmed-dashboard", confirmation.call_args.args[2])
        self.assertNotEqual(self.app.thread(), threads[0])

    def test_cancel_read_discards_results_and_never_publishes(self):
        def read(*_args): self.release.wait(2); return VOLUMES
        with patch.object(performance_ui, "list_instance_volumes", side_effect=read), patch.object(performance_ui, "create_cloudwatch_dashboard") as create:
            self.page.load_ebs_data(); self.page.cancel_operation(); self.release.set()
            self.wait_until(lambda: not task_busy(self.page))
        self.assertEqual([], self.page._volumes)
        create.assert_not_called()

    def test_baseline_calculation_runs_in_background(self):
        threads = []
        def calculate(_path): threads.append(QThread.currentThread()); return {"Metric": 1.0}
        self.page.csv_path.setText("synthetic.csv")
        with patch.object(performance_ui, "calculate_baseline", side_effect=calculate):
            self.page.analyze_csv(); self.wait_until(lambda: not task_busy(self.page))
        self.assertNotEqual(self.app.thread(), threads[0])
        self.assertIn("Metric: 1.00", self.page.csv_output.toPlainText())

    def test_context_change_discards_ebs_results_and_prevents_reentry(self):
        def read(*_args): self.release.wait(2); return VOLUMES
        with patch.object(performance_ui, "list_instance_volumes", side_effect=read) as operation:
            self.page.load_ebs_data()
            self.window.home.commands.profile.addItem("other-profile")
            self.window.home.commands.profile.setCurrentText("other-profile")
            self.page.load_ebs_data()
            self.release.set(); self.wait_until(lambda: not task_busy(self.page))
        self.assertLessEqual(operation.call_count, 1)
        self.assertEqual([], self.page._volumes)
        self.assertFalse(self.page.dashboard.isEnabled())
