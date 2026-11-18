from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pyside6_poc.global_inventory import GlobalInventoryPage
from pyside6_poc.tasks import task_busy
from services.global_inventory import INVENTORY_COLUMNS, RESOURCE_COLUMNS


class GlobalInventoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.errors = []
        self.page = GlobalInventoryPage(["other", "IB"], "other", "eu-west-1",
                                        lambda _profile, operation: operation(), self.errors.append, demo=True)

    def tearDown(self):
        self.page.cancel_event.set()
        self.wait_until(lambda: not task_busy(self.page))
        self.page.deleteLater(); self.app.processEvents()

    def wait_until(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(0.005)
        self.assertTrue(condition(), "GlobalInventory worker did not complete")

    def discover_demo(self):
        self.page.discover()
        self.wait_until(lambda: not task_busy(self.page))
        self.assertFalse(self.errors)

    def test_demo_discovery_is_offline_and_controls_follow_task_lifetime(self):
        self.assertEqual("IB", self.page.profile.currentText())
        with patch("pyside6_poc.global_inventory.collect_global_inventory") as collect:
            self.page.discover()
            self.assertFalse(self.page.query.isEnabled())
            self.wait_until(lambda: not task_busy(self.page))
        collect.assert_not_called()
        self.assertEqual(2, self.page.model.rowCount())
        self.assertEqual("DEMO", self.page.report["status"])
        self.assertTrue(self.page.json.isEnabled())

    def test_filters_sorting_and_filtered_export_selection_use_original_rows(self):
        self.discover_demo()
        self.page.search.setText("BUCKET")
        rows = self.page.filtered_resources()
        self.assertEqual(1, len(rows))
        self.assertEqual("AWS::S3::Bucket", rows[0]["resource_type"])
        self.assertEqual(2, len(self.page.report["resources"]))
        self.assertEqual("1 / 2 resources", self.page.count.text())
        self.page.search.clear()
        self.page.account_filter.setCurrentIndex(1)
        self.assertEqual("111111111111", self.page.filtered_resources()[0]["account_id"])
        self.page.region_filter.setCurrentIndex(1)  # incompatible region
        self.assertEqual([], self.page.filtered_resources())

    def test_setting_changes_clear_results_and_exports(self):
        self.discover_demo()
        self.page.region.setCurrentText("eu-south-2")
        self.assertIsNone(self.page.report)
        self.assertEqual(0, self.page.model.rowCount())
        self.assertFalse(self.page.csv.isEnabled())
        self.assertEqual("all_accounts", self.page.aggregator.currentText())

    def test_cancel_sets_event_and_preserves_result_from_worker(self):
        self.page.demo = False
        entered = threading.Event()
        def collect(*_args, cancel, **_kwargs):
            entered.set()
            cancel.wait(2)
            result = self.page._demo_report("all_accounts")
            result["status"] = "CANCELLED"
            return result
        with patch("pyside6_poc.global_inventory.collect_global_inventory", side_effect=collect) as mocked:
            self.page.discover()
            self.wait_until(entered.is_set)
            self.page.discover()  # second scan must not overwrite ownership
            self.page.cancel_scan()
            self.wait_until(lambda: not task_busy(self.page))
        mocked.assert_called_once()
        self.assertEqual("CANCELLED", self.page.report["status"])
        self.assertTrue(self.page.csv.isEnabled())

    def test_aggregator_reload_invalidates_previous_inventory(self):
        self.discover_demo()
        self.page.load_aggregators()
        self.wait_until(lambda: not task_busy(self.page))
        self.assertIsNone(self.page.report)
        self.assertEqual("all_accounts", self.page.aggregator.currentText())

    def test_large_inventory_uses_model_without_per_cell_widgets(self):
        rows = []
        for index in range(12000):
            row = dict.fromkeys(RESOURCE_COLUMNS, "")
            row.update(account_id="111111111111", region="eu-west-1", resource_type="AWS::EC2::Instance",
                       resource_id=f"i-{index}", resource_name=f"node-{index}")
            rows.append(row)
        self.page.model.set_rows(rows)
        self.page._set_filters(rows)
        self.page.search.setText("node-11999")
        self.assertEqual(1, self.page.proxy.rowCount())
        self.assertEqual("i-11999", self.page.filtered_resources()[0]["resource_id"])
        self.assertEqual(12000, self.page.model.rowCount())

    def test_tab_is_integrated_in_sre_and_has_independent_scope(self):
        import pyside6_poc.main as main
        with patch.object(main, "discover_profiles", return_value=["IB", "other"]):
            window = main.MainWindow(demo=True)
        try:
            sre = window.architecture
            labels = [sre.tabs.tabText(index) for index in range(sre.tabs.count())]
            self.assertIn("GlobalInventory", labels)
            page = sre.global_inventory
            page.profile.setCurrentText("IB")
            page._collected(page._demo_report("all_accounts"))
            window.home.commands.profile.setCurrentText("other")
            self.assertIsNotNone(page.report)
            self.assertEqual("IB", page.profile.currentText())
        finally:
            window.close(); window.deleteLater(); self.app.processEvents()

    def test_export_defaults_to_full_report_and_filtered_export_is_explicit(self):
        self.discover_demo()
        self.page.search.setText("bucket")
        for scope, expected_count in ((0, None), (1, 1)):
            self.page.export_scope.setCurrentIndex(scope)
            with patch("pyside6_poc.global_inventory.QFileDialog") as dialog, \
                 patch("pyside6_poc.global_inventory.export_inventory") as export:
                dialog.return_value.exec.return_value = 1
                dialog.return_value.selectedFiles.return_value = ["inventory.json"]
                self.page.export("json")
                self.wait_until(lambda: not task_busy(self.page))
            export.assert_called_once()
            rows = export.call_args.kwargs["resources"]
            self.assertEqual(expected_count, None if rows is None else len(rows))
            self.assertEqual(";", export.call_args.kwargs["delimiter"])
        self.assertEqual(2, len(self.page.report["resources"]))

    def test_table_has_no_internal_key_and_separates_arn_from_id(self):
        self.discover_demo()
        row = self.page.report["resources"][0]
        row.update(resource_id="arn:aws:sns:eu-west-1:111111111111:alerts", arn="arn:aws:sns:eu-west-1:111111111111:alerts")
        self.page.model.set_rows(self.page.report["resources"])
        model = self.page.model
        self.assertEqual(len(INVENTORY_COLUMNS), model.columnCount())
        from PySide6.QtCore import Qt
        self.assertEqual("Resource Type", model.headerData(0, Qt.Orientation.Horizontal))
        self.assertEqual("", model.data(model.index(0, INVENTORY_COLUMNS.index("resource_id"))))
        self.assertEqual(row["arn"], model.data(model.index(0, INVENTORY_COLUMNS.index("arn"))))
        self.assertNotIn("resource_key", INVENTORY_COLUMNS)
        self.page.search.setText("alerts")
        self.assertEqual(1, self.page.proxy.rowCount())

    def test_csv_separator_can_be_changed_for_comma_importers(self):
        self.discover_demo()
        self.page.csv_separator.setCurrentIndex(1)
        with patch("pyside6_poc.global_inventory.QFileDialog") as dialog, \
             patch("pyside6_poc.global_inventory.export_inventory") as export:
            dialog.return_value.exec.return_value = 1
            dialog.return_value.selectedFiles.return_value = ["inventory.csv"]
            self.page.export("csv")
            self.wait_until(lambda: not task_busy(self.page))
        self.assertEqual(",", export.call_args.kwargs["delimiter"])

    def test_partial_expected_count_remains_visible_after_export(self):
        report = self.page._demo_report("all_accounts")
        report["status"] = "PARTIAL"; report["summary"]["expected_resource_count"] = 70002
        self.page._collected(report); self.page._controls()
        self.assertIn("2/70002 expected", self.page.status.text())
        with patch("pyside6_poc.global_inventory.QFileDialog") as dialog, \
             patch("pyside6_poc.global_inventory.export_inventory"):
            dialog.return_value.exec.return_value = 1
            dialog.return_value.selectedFiles.return_value = ["inventory.json"]
            self.page.export("json"); self.wait_until(lambda: not task_busy(self.page))
        self.assertIn("PARTIAL", self.page.status.text())
        self.assertIn("2/70002 expected", self.page.status.text())


if __name__ == "__main__":
    unittest.main()
