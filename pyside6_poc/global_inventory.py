"""Independent SRE workspace for AWS Config aggregator inventory."""
from __future__ import annotations

import json
from threading import Event

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QTableView, QVBoxLayout, QWidget,
)

from app.config import AWS_REGIONS, DEFAULT_REGION
from pyside6_poc.tasks import start_task, task_busy
from services.global_inventory import (
    COVERAGE_NOTE, INVENTORY_COLUMNS, RESOURCE_COLUMNS, collect_global_inventory,
    export_inventory, inventory_value, list_aggregators,
)


class InventoryModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(INVENTORY_COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return str(inventory_value(self.rows[index.row()], INVENTORY_COLUMNS[index.column()]))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            column = INVENTORY_COLUMNS[section]
            return "Config Identifier ARN" if column == "config_resource_arn" else column.replace("_", " ").title()
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            if INVENTORY_COLUMNS[section] == "resource_id":
                return "Non-ARN Config identifier. If Config supplies an ARN as its ID, see the ARN columns instead."
            if INVENTORY_COLUMNS[section] == "config_resource_arn":
                return "ARN used as the Config identifier, shown only when it differs from the detailed ARN."
        return super().headerData(section, orientation, role)


class InventoryFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = {}
        self.search = ""

    def filterAcceptsRow(self, row, parent):
        resource = self.sourceModel().rows[row]
        if any(value and resource.get(key) != value for key, value in self.values.items()):
            return False
        return not self.search or any(self.search in str(resource.get(key, "")).casefold()
                                      for key in ("resource_name", "resource_id", "arn"))


class GlobalInventoryPage(QWidget):
    progress = Signal(str)

    def __init__(self, profiles, initial_profile, initial_region, run_with_sso, show_error, *, demo=False):
        super().__init__()
        self.recover, self.show_error, self.demo = run_with_sso, show_error, demo
        self.report = None
        self.cancel_event = Event()
        self.thread = None
        self.mode = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        heading = QLabel("GlobalInventory"); heading.setObjectName("pageTitle"); root.addWidget(heading)
        description = QLabel("Consolidated AWS Config inventory across accounts and regions · CMDB comparison evidence")
        description.setObjectName("pageSubtitle"); description.setWordWrap(True); root.addWidget(description)

        selectors = QHBoxLayout()
        self.profile = QComboBox(); self.profile.setEditable(True)
        self.profile.addItems(list(dict.fromkeys(profiles)))
        preferred = next((name for name in profiles if name.casefold() == "ib"), initial_profile)
        self.profile.setCurrentText(preferred)
        self.region = QComboBox(); self.region.setEditable(True); self.region.addItems(AWS_REGIONS)
        self.region.setCurrentText(initial_region or DEFAULT_REGION)
        self.aggregator = QComboBox(); self.aggregator.setEditable(True); self.aggregator.addItem("all_accounts")
        for label, control in (("AWS PROFILE / IB", self.profile), ("AGGREGATOR REGION", self.region),
                               ("AGGREGATOR", self.aggregator)):
            field = QVBoxLayout(); title = QLabel(label); title.setObjectName("microLabel")
            field.addWidget(title); control.setMinimumHeight(36); field.addWidget(control); selectors.addLayout(field, 1)
        root.addLayout(selectors)
        actions = QHBoxLayout()
        self.load = QPushButton("LOAD AGGREGATORS"); self.load.setObjectName("quietButton")
        self.query = QPushButton("DISCOVER"); self.query.setObjectName("primaryButton")
        self.cancel = QPushButton("CANCEL"); self.cancel.setObjectName("quietButton"); self.cancel.setEnabled(False)
        for button in (self.load, self.query, self.cancel):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(QLabel("CSV SEPARATOR"))
        self.csv_separator = QComboBox()
        self.csv_separator.addItem("Excel (;)", ";")
        self.csv_separator.addItem("CSV (,)", ",")
        self.csv_separator.setToolTip("Choose the delimiter expected by your spreadsheet or CSV importer.")
        actions.addWidget(self.csv_separator)
        root.addLayout(actions)
        self.status = QLabel("Select the IB profile and the region hosting all_accounts.")
        self.status.setWordWrap(True); self.status.setObjectName("tunnelInfo"); root.addWidget(self.status)
        note = QLabel(COVERAGE_NOTE); note.setWordWrap(True); note.setObjectName("footerText"); root.addWidget(note)

        filters = QHBoxLayout()
        self.account_filter, self.region_filter, self.type_filter = QComboBox(), QComboBox(), QComboBox()
        for control, label in ((self.account_filter, "ALL ACCOUNTS"), (self.region_filter, "ALL REGIONS"),
                               (self.type_filter, "ALL RESOURCE TYPES")):
            control.addItem(label, ""); control.currentIndexChanged.connect(self._filter)
            control.setMinimumWidth(145); filters.addWidget(control)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search resource name, ID or ARN")
        self.search.textChanged.connect(self._filter); filters.addWidget(self.search, 2)
        root.addLayout(filters)
        self.model = InventoryModel(self)
        self.proxy = InventoryFilter(self); self.proxy.setSourceModel(self.model)
        self.table = QTableView(); self.table.setObjectName("inventoryTable"); self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True); self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for column, key in enumerate(INVENTORY_COLUMNS):
            self.table.setColumnWidth(column, {"resource_type": 210, "resource_id": 190,
                                               "resource_name": 180, "account_id": 130, "region": 110,
                                               "arn": 280, "config_resource_arn": 280}.get(key, 190))
        root.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.count = QLabel("0 / 0 resources"); footer.addWidget(self.count)
        footer.addStretch()
        self.coverage = QPushButton("COVERAGE / WARNINGS"); self.coverage.setEnabled(False)
        self.export_scope = QComboBox(); self.export_scope.addItems(["ALL RESOURCES", "FILTERED RESOURCES"])
        self.csv = QPushButton("EXPORT CSV"); self.json = QPushButton("EXPORT JSON")
        for button in (self.coverage, self.csv, self.json):
            button.setObjectName("quietButton")
        for control in (self.coverage, self.export_scope, self.csv, self.json):
            footer.addWidget(control)
        root.addLayout(footer)
        self.load.clicked.connect(self.load_aggregators); self.query.clicked.connect(self.discover)
        self.cancel.clicked.connect(self.cancel_scan); self.coverage.clicked.connect(self.show_coverage)
        self.csv.clicked.connect(lambda: self.export("csv")); self.json.clicked.connect(lambda: self.export("json"))
        self.profile.currentTextChanged.connect(self._source_changed)
        self.region.currentTextChanged.connect(self._source_changed)
        self.aggregator.currentTextChanged.connect(self._invalidate)
        self.progress.connect(self._progress)
        self._controls()

    def _context(self):
        return (self.profile.currentText().strip(), self.region.currentText().strip(),
                self.aggregator.currentText().strip())

    @Slot(str)
    def _progress(self, message):
        self.status.setText(message)

    def _controls(self):
        busy = task_busy(self)
        for control in (self.profile, self.region, self.aggregator, self.load, self.query,
                        self.export_scope, self.csv_separator):
            control.setEnabled(not busy)
        self.cancel.setEnabled(busy and self.mode == "discovery" and not self.cancel_event.is_set())
        for control in (self.csv, self.json, self.coverage):
            control.setEnabled(self.report is not None and not busy)

    def _source_changed(self, *_args):
        self.aggregator.blockSignals(True)
        self.aggregator.clear(); self.aggregator.addItem("all_accounts")
        self.aggregator.blockSignals(False)
        self._invalidate()

    def _invalidate(self, *_args):
        self.report = None
        self.model.set_rows([])
        self._set_filters([])
        self.status.setText("Inventory settings changed. Run discovery to collect this scope.")
        self._controls()

    def _start(self, mode, operation, finished):
        if task_busy(self):
            return
        self.mode = mode
        self.cancel_event = Event()
        start_task(self, operation, finished, self._failed, self._controls, context=self._context)
        self._controls()

    def _failed(self, exc):
        self.status.setText("GlobalInventory operation failed. Review the error and retry.")
        self.show_error(exc)

    def load_aggregators(self):
        if task_busy(self):
            return
        profile, region, _aggregator = self._context()
        self.status.setText("Loading aggregators in the selected account and region…")
        self._start("aggregators", lambda: ([{"ConfigurationAggregatorName": "all_accounts"}] if self.demo else
                    list_aggregators(profile, region, run_with_sso=self.recover)), self._aggregators_loaded)

    def _aggregators_loaded(self, result):
        preferred = self.aggregator.currentText()
        names = [item["ConfigurationAggregatorName"] for item in result]
        self.aggregator.blockSignals(True)
        self.aggregator.clear(); self.aggregator.addItems(names)
        if preferred in names:
            self.aggregator.setCurrentText(preferred)
        self.aggregator.blockSignals(False)
        self._invalidate()
        self.status.setText(f"{len(names)} aggregator(s) found." if names else
                            "No aggregators found. Check the IB profile and aggregator region.")

    def discover(self):
        if task_busy(self):
            return
        profile, region, aggregator = self._context()
        self._invalidate()
        self.status.setText("Starting global inventory…")
        self._start("discovery", lambda: self._demo_report(aggregator) if self.demo else collect_global_inventory(
            profile, region, aggregator, run_with_sso=self.recover, cancel=self.cancel_event,
            progress=self.progress.emit), self._collected)

    def cancel_scan(self):
        self.cancel_event.set()
        self.status.setText("Cancellation requested. Waiting for the current AWS request or login to finish…")
        self._controls()

    def _collected(self, report):
        self.report = report
        self.model.set_rows(report["resources"])
        self._set_filters(report["resources"])
        self.status.setText(self._report_status())

    def _report_status(self):
        report = self.report
        summary = report["summary"]
        expected = summary.get("expected_resource_count")
        count = str(summary["resource_count"]) + (f"/{expected} expected" if expected is not None else " / expected count unknown")
        return (f"{report['status']} · {count} resources · "
                            f"{summary['account_count']} accounts with resources · "
                            f"{summary['region_count']} regions · {len(report['warnings'])} warning(s). "
                            "Coverage is limited to this aggregator.")

    def _set_filters(self, rows):
        for control, key, label in ((self.account_filter, "account_id", "ALL ACCOUNTS"),
                                     (self.region_filter, "region", "ALL REGIONS"),
                                     (self.type_filter, "resource_type", "ALL RESOURCE TYPES")):
            control.blockSignals(True); control.clear(); control.addItem(label, "")
            for value in sorted({row[key] for row in rows}):
                control.addItem(value, value)
            control.blockSignals(False)
        self.search.clear()
        self._filter()

    def _filter(self, *_args):
        # Signals can arrive while controls are being constructed.
        if not hasattr(self, "proxy"):
            return
        self.proxy.values = {key: control.currentData() for key, control in (
            ("account_id", self.account_filter), ("region", self.region_filter), ("resource_type", self.type_filter))}
        self.proxy.search = self.search.text().strip().casefold()
        self.proxy.invalidateFilter()
        self.count.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()} resources")

    def filtered_resources(self):
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]
                for row in range(self.proxy.rowCount())]

    def export(self, kind):
        if self.report is None or task_busy(self):
            return
        dialog = QFileDialog(self, f"Export GlobalInventory {kind.upper()}", f"global-inventory.{kind}",
                             f"{kind.upper()} files (*.{kind})")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix(kind)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
            return
        path = dialog.selectedFiles()[0]
        report = self.report
        resources = self.filtered_resources() if self.export_scope.currentIndex() == 1 else None
        delimiter = self.csv_separator.currentData()
        self.status.setText("Exporting inventory…")
        self._start("export", lambda: export_inventory(path, report, resources=resources, delimiter=delimiter),
                    lambda _result: self.status.setText(f"Inventory exported to {path}\n{self._report_status()}"))

    def show_coverage(self):
        if self.report is None:
            return
        dialog = QDialog(self); dialog.setWindowTitle("GlobalInventory coverage and warnings"); dialog.resize(850, 550)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(json.dumps({key: value for key, value in self.report.items() if key != "resources"},
                                     ensure_ascii=False, indent=2, default=str))
        layout.addWidget(text); dialog.exec()

    def _demo_report(self, aggregator):
        resources = []
        for account, region, kind, identifier in (
            ("111111111111", "eu-west-1", "AWS::EC2::Instance", "i-demo-app"),
            ("222222222222", "eu-south-2", "AWS::S3::Bucket", "demo-inventory-bucket"),
        ):
            row = dict.fromkeys(RESOURCE_COLUMNS, "")
            row.update(resource_key=json.dumps([account, region, kind, identifier]), account_id=account,
                       account_name="Demo account", region=region, resource_type=kind, resource_id=identifier,
                       resource_name=identifier, source=aggregator, details_status="AVAILABLE")
            resources.append(row)
        return {"schema_version": "2.0", "status": "DEMO", "source": {"aggregator": aggregator},
                "coverage": {"note": COVERAGE_NOTE}, "warnings": [{"stage": "demo", "message": "Synthetic data; no AWS calls."}],
                "resources": resources, "summary": {"resource_count": 2, "account_count": 2, "region_count": 2}}
