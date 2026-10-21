"""DR dashboard preparation, using Architecture's existing inventory."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QPointF, QThread, QTimeZone, QDateTime, Qt, Signal, Slot, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateTimeEdit, QFileDialog, QFrame,
    QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QHeaderView,
    QCheckBox, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from services.dr_dashboard import ComparisonPair, TEMPLATES, artifact_prefix, build_dashboard, build_pair_metrics, dashboard_name_for_profile, format_metric_value, metric_dimensions, publish_new_dashboard, query_metrics, render_html_report
from utils.csv_safety import csv_safe_value
from app.config import AWS_REGIONS


class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            self.finished.emit(self.operation())
        except Exception as exc:
            self.failed.emit(exc)


class MetricPreview(QGraphicsView):
    """Small native preview of returned series; gaps remain gaps, not zeroes."""
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setBackgroundBrush(QColor("#090d0e"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(230)

    def show_results(self, results, regions, start, end, period, account, application):
        self.scene.clear()
        groups = defaultdict(list)
        for result in results:
            metric = result.metric
            groups[(metric.resource_type, metric.name, metric.label, metric.statistic, metric.unit)].append(result)
        header = self.scene.addText(f"DR METRIC EVIDENCE | Account {account} | {application}\n{regions[0]} / {regions[1]} | {start:%Y-%m-%d %H:%M UTC} — {end:%Y-%m-%d %H:%M UTC}\nMissing data is not zero; shared resources may include other applications.")
        header.setDefaultTextColor(QColor("#e7dfce")); header.setFont(QFont("Segoe UI", 9)); header.setPos(12, 8)
        y = 75
        colors = {regions[0]: QColor("#69a7e0"), regions[1]: QColor("#d8ad65")}
        for (resource_type, _name, label, statistic, unit), series in sorted(groups.items()):
            height = 215 + len(series) * 18
            box = self.scene.addRect(10, y, 760, height, QPen(QColor("#394241")))
            box.setBrush(QColor("#121718"))
            title = self.scene.addText(f"{resource_type} | {label} | {statistic} | {unit}")
            title.setDefaultTextColor(QColor("#e7dfce")); title.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold)); title.setPos(20, y + 8)
            points = [float(value) for result in series for _timestamp, value in result.points]
            x0, x1, y0, y1 = 105, 745, y + 40, y + 175
            self.scene.addLine(x0, y0, x0, y1, QPen(QColor("#65716e")))
            self.scene.addLine(x0, y1, x1, y1, QPen(QColor("#65716e")))
            if points:
                maximum = max(points); minimum = min(0.0, min(points))
                if maximum <= minimum:
                    maximum = minimum + 1.0
                scale = (end - start).total_seconds()
                for result in series:
                    if not result.points:
                        continue
                    path = QPainterPath(); previous = None
                    for timestamp, value in result.points:
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        px = x0 + max(0.0, min(1.0, (timestamp - start).total_seconds() / scale)) * (x1 - x0)
                        py = y1 - (float(value) - minimum) / (maximum - minimum) * (y1 - y0)
                        if previous is None or (timestamp - previous).total_seconds() > period * 1.5:
                            path.moveTo(QPointF(px, py))
                        else:
                            path.lineTo(QPointF(px, py))
                        previous = timestamp
                        self.scene.addEllipse(px - 2, py - 2, 4, 4, QPen(colors[result.metric.region]), colors[result.metric.region])
                    self.scene.addPath(path, QPen(colors[result.metric.region], 2))
                value_label = self.scene.addText(f"{format_metric_value(maximum)} {unit}")
                value_label.setDefaultTextColor(QColor("#aaa79f")); value_label.setPos(14, y0 - 8)
            else:
                message = self.scene.addText("No data available for this metric in the selected interval")
                message.setDefaultTextColor(QColor("#cfaa78")); message.setPos(135, y + 90)
            for index, result in enumerate(series):
                label_item = self.scene.addText(f"{result.metric.region} | {result.metric.resource_name} | {result.metric.attribution} | {result.status}")
                label_item.setDefaultTextColor(colors[result.metric.region] if result.status == "DATA" else QColor("#d78f7e"))
                label_item.setFont(QFont("Segoe UI", 8)); label_item.setPos(22, y + 190 + index * 18)
                if result.detail:
                    label_item.setToolTip(result.detail)
            y += height + 14
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-8, -8, 12, 12))


class DRDashboardPage(QWidget):
    def __init__(self, profile_getter, run_with_sso, show_error, demo=False):
        super().__init__()
        self.profile_getter = profile_getter
        self.run_with_sso = run_with_sso
        self.show_error = show_error
        self.demo = demo
        self.graph = None
        self.application = "ALL PRODUCTS"
        self.tag_key = ""
        self.context_key = None
        self.prepared = None
        self.thread = None
        self.worker = None
        self._populating = False
        self._revision = 0
        self.preferred_regions = ()
        self.comparison_pairs: list[ComparisonPair] = []
        root = QVBoxLayout(self); root.setContentsMargins(8, 12, 8, 8); root.setSpacing(8)
        panel = QFrame(); panel.setObjectName("operationPanel")
        controls = QGridLayout(panel); controls.setContentsMargins(12, 10, 12, 10)
        self.region_a = QComboBox(); self.region_b = QComboBox()
        self.start = QDateTimeEdit(); self.end = QDateTimeEdit()
        for widget in (self.start, self.end):
            widget.setTimeZone(QTimeZone.utc()); widget.setCalendarPopup(True); widget.setDisplayFormat("yyyy-MM-dd HH:mm 'UTC'")
        self.end.setDateTime(QDateTime.currentDateTimeUtc()); self.start.setDateTime(QDateTime.currentDateTimeUtc().addSecs(-21600))
        for index, (title, widget) in enumerate((("SOURCE REGION", self.region_a), ("DR REGION", self.region_b), ("START UTC", self.start), ("END UTC", self.end))):
            label = QLabel(title); label.setObjectName("microLabel"); controls.addWidget(label, 0, index); controls.addWidget(widget, 1, index)
            widget.setMinimumHeight(34)
        self.prepare_button = QPushButton("PREPARE METRICS"); self.prepare_button.setObjectName("primaryButton"); controls.addWidget(self.prepare_button, 2, 0, 1, 4)
        root.addWidget(panel)
        self.context = QLabel("Run Architecture discovery for two regions to prepare a DR dashboard."); self.context.setWordWrap(True); self.context.setObjectName("footerText"); root.addWidget(self.context)
        self.tabs = QTabWidget(); self.tabs.setObjectName("inventoryTabs")
        resource_page = QWidget(); resource_layout = QVBoxLayout(resource_page); resource_layout.setContentsMargins(4, 8, 4, 4)
        hint = QLabel("Select API Gateway resources explicitly. API Gateway is the DR source of truth for external API traffic; ALB traffic can include service-to-service calls. No logs are enabled or queried."); hint.setWordWrap(True); hint.setObjectName("footerText"); resource_layout.addWidget(hint)
        filters = QHBoxLayout(); filters.setSpacing(8)
        filter_label = QLabel("FILTER RESOURCES"); filter_label.setObjectName("microLabel"); filters.addWidget(filter_label)
        self.resource_filter = QLineEdit(); self.resource_filter.setPlaceholderText("API name, ID, region or attribution"); self.resource_filter.setClearButtonEnabled(True); self.resource_filter.setMinimumWidth(300); filters.addWidget(self.resource_filter, 1)
        self.resource_type_filter = QComboBox(); self.resource_type_filter.addItem("ALL TYPES"); self.resource_type_filter.setMinimumWidth(150); filters.addWidget(self.resource_type_filter)
        self.selected_only = QCheckBox("SHOW SELECTED ONLY"); self.selected_only.setObjectName("footerText"); filters.addWidget(self.selected_only)
        self.resource_visible_count = QLabel("0 / 0 resources"); self.resource_visible_count.setObjectName("sectionCounter"); filters.addWidget(self.resource_visible_count)
        resource_layout.addLayout(filters)
        self.resources = QTableWidget(0, 5); self.resources.setHorizontalHeaderLabels(["Include", "Region", "Type", "Resource", "Attribution"])
        self.resources.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.resources.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.resources.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.resources.horizontalHeader().setStretchLastSection(False)
        self.resources.setColumnHidden(4, True); self.resources.setMinimumHeight(260)
        resource_layout.addWidget(self.resources, 1)
        pair_actions = QHBoxLayout(); pair_actions.setSpacing(8)
        self.add_pair = QPushButton("ADD COMPARISON FROM SELECTED"); self.add_pair.setObjectName("primaryButton"); pair_actions.addWidget(self.add_pair)
        self.clear_selection = QPushButton("CLEAR SELECTION"); self.clear_selection.setObjectName("quietButton"); pair_actions.addWidget(self.clear_selection)
        self.toggle_pairs = QPushButton("COMPARISON SETS (0)"); self.toggle_pairs.setObjectName("quietButton"); pair_actions.addWidget(self.toggle_pairs)
        pair_hint = QLabel("Select one or more resources per region of the same type. A set produces one two-region widget per metric."); pair_hint.setObjectName("footerText"); pair_actions.addWidget(pair_hint, 1)
        resource_layout.addLayout(pair_actions)
        self.pair_section = QFrame(); pair_section_layout = QVBoxLayout(self.pair_section); pair_section_layout.setContentsMargins(0, 0, 0, 0); pair_section_layout.setSpacing(5)
        self.pairs = QTableWidget(0, 4); self.pairs.setHorizontalHeaderLabels(["Comparison set", "Type", "Source", "DR"])
        self.pairs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.pairs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pairs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); self.pairs.horizontalHeader().setStretchLastSection(True); self.pairs.setMaximumHeight(118)
        pair_section_layout.addWidget(self.pairs)
        pair_remove = QHBoxLayout(); self.remove_pair = QPushButton("REMOVE SELECTED PAIR"); self.remove_pair.setObjectName("quietButton"); pair_remove.addWidget(self.remove_pair); pair_remove.addStretch()
        pair_section_layout.addLayout(pair_remove); self.pair_section.setVisible(False); resource_layout.addWidget(self.pair_section)
        self.tabs.addTab(resource_page, "RESOURCES")
        preview_page = QWidget(); preview_layout = QVBoxLayout(preview_page); preview_layout.setContentsMargins(4, 8, 4, 4)
        self.preview = MetricPreview(); preview_layout.addWidget(self.preview, 1)
        self.metric_table = QTableWidget(0, 6); self.metric_table.setHorizontalHeaderLabels(["Region", "Resource", "Metric", "Status", "Last value", "Details"])
        self.metric_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.metric_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); self.metric_table.horizontalHeader().setStretchLastSection(True); self.metric_table.setMaximumHeight(190); preview_layout.addWidget(self.metric_table)
        self.tabs.addTab(preview_page, "METRIC PREVIEW")
        root.addWidget(self.tabs, 1)
        self.actions_panel = QFrame(); actions = QHBoxLayout(self.actions_panel); actions.setContentsMargins(0, 0, 0, 0)
        self.export_html = QPushButton("EXPORT DR REPORT HTML"); self.export_json = QPushButton("EXPORT DASHBOARD JSON"); self.export_csv = QPushButton("EXPORT DATA CSV"); self.export_png = QPushButton("EXPORT PREVIEW PNG"); self.publish = QPushButton("CREATE / UPDATE CLOUDWATCH DASHBOARD")
        for button in (self.export_html, self.export_json, self.export_csv, self.export_png):
            button.setObjectName("quietButton"); actions.addWidget(button)
        actions.addStretch(); self.publish.setObjectName("primaryButton"); actions.addWidget(self.publish); self.actions_panel.setVisible(False); root.addWidget(self.actions_panel)
        self.prepare_button.clicked.connect(self.prepare)
        self.region_a.currentIndexChanged.connect(self._regions_changed); self.region_b.currentIndexChanged.connect(self._regions_changed)
        self.start.dateTimeChanged.connect(self.invalidate); self.end.dateTimeChanged.connect(self.invalidate)
        self.resources.itemChanged.connect(self._resource_item_changed)
        self.add_pair.clicked.connect(self.add_comparison_pair); self.remove_pair.clicked.connect(self.remove_comparison_pair); self.clear_selection.clicked.connect(self.clear_resource_selection); self.toggle_pairs.clicked.connect(self.toggle_pair_section)
        self.resource_filter.textChanged.connect(self._filter_resource_rows)
        self.resource_type_filter.currentTextChanged.connect(self._filter_resource_rows)
        self.selected_only.toggled.connect(self._filter_resource_rows)
        self.export_html.clicked.connect(self.save_html); self.export_json.clicked.connect(self.save_json); self.export_csv.clicked.connect(self.save_csv); self.export_png.clicked.connect(self.save_png); self.publish.clicked.connect(self.publish_dashboard)
        self.invalidate()

    def _profile_current(self):
        return self.profile_getter().strip()

    def set_context(self, graph, application, tag_key):
        key = (id(graph), application, tag_key, self._profile_current()) if graph else None
        if key == self.context_key:
            return
        self.context_key = key; self.graph = graph; self.application = application; self.tag_key = tag_key
        self.comparison_pairs.clear(); self._render_pairs()
        self._populating = True
        self.region_a.clear(); self.region_b.clear()
        if graph:
            # Keep the same regional catalogue as the connection selector.
            # Discovery is still the authority for which regions may be
            # queried: a selection outside graph.regions is explicitly blocked
            # in prepare(), rather than silently producing a one-region report.
            regions = list(dict.fromkeys([*AWS_REGIONS, *graph.regions]))
            self.region_a.addItems(regions); self.region_b.addItems(regions)
            source_index = self.region_a.findText(graph.regions[0]) if graph.regions else -1
            if source_index >= 0:
                self.region_a.setCurrentIndex(source_index)
            if len(graph.regions) > 1:
                destination_index = self.region_b.findText(graph.regions[1])
                if destination_index >= 0:
                    self.region_b.setCurrentIndex(destination_index)
            if len(self.preferred_regions) == 2:
                for selector, preferred in zip((self.region_a, self.region_b), self.preferred_regions):
                    index = selector.findText(preferred)
                    if index >= 0: selector.setCurrentIndex(index)
        self._populating = False
        self._refresh_resources()

    def profile_changed(self, *_args):
        self.context_key = None
        self.invalidate()
        if self.graph and self.graph.account_name != self._profile_current() and not self.demo:
            self.context.setText("AWS profile changed. Run Architecture discovery again before preparing a DR dashboard.")

    def _regions_changed(self, *_args):
        if not self._populating:
            self.comparison_pairs.clear(); self._render_pairs()
            self._refresh_resources()

    def _refresh_resources(self):
        self._populating = True
        previous_type = self.resource_type_filter.currentText()
        self.resources.setRowCount(0)
        regions = {self.region_a.currentText(), self.region_b.currentText()}
        if self.graph:
            undiscovered = sorted(regions - set(self.graph.regions))
            if undiscovered:
                self.context.setText(
                    "Selected region has not been discovered: " + ", ".join(undiscovered) +
                    ". Select it in Architecture / SELECTED REGIONS and run DISCOVER once before preparing metrics."
                )
                self.context.setToolTip("Architecture discovery is the evidence source for the DR resource selection.")
                self.resource_type_filter.blockSignals(True); self.resource_type_filter.clear(); self.resource_type_filter.addItem("ALL TYPES"); self.resource_type_filter.blockSignals(False)
                self._populating = False
                self.invalidate()
                self._filter_resource_rows()
                return
            scoped = self.graph.scoped(self.application, self.tag_key)
            # DR traffic evidence is intentionally limited to the public API
            # boundary. The full inventory remains available in its own tab.
            api_gateway_types = set(TEMPLATES)
            nodes = sorted((
                n for n in scoped.nodes.values()
                if n.region in regions and n.resource_type in api_gateway_types
            ), key=lambda n: (n.region, n.resource_type, n.name or n.id))
            # A current graph normally has a single canonical node per API
            # Gateway after Config/live merging.  Retain this defensive view
            # de-duplication for a graph that was built by an older client or
            # for partial Config records whose identifier differs from the
            # native API identifier.
            unique_nodes = {}
            for node in nodes:
                identity = node.id
                if node.resource_type in {"APIGateway", "APIGatewayV2"}:
                    # REST API metrics are keyed by ApiName.  If Config does
                    # not expose an ARN/API ID, same-name APIs of the same
                    # family and region cannot be distinguished by the
                    # dashboard anyway, so present one canonical selector row.
                    identity = (node.name or "").strip().casefold()
                    if not identity:
                        arn = node.arn or ""
                        for marker in ("/restapis/", "/apis/"):
                            if marker in arn:
                                identity = arn.split(marker, 1)[1].split("/", 1)[0]
                                break
                    identity = identity or str(node.metadata.get("ApiId") or node.id)
                key = (node.region, node.resource_type, identity)
                existing = unique_nodes.get(key)
                # Prefer live service data, which has the metric dimensions
                # needed by CloudWatch, over a Config-only shadow record.
                if existing is None or (existing.metadata.get("DiscoverySource") == "AWS Config" and node.metadata.get("DiscoverySource") != "AWS Config"):
                    unique_nodes[key] = node
            nodes = sorted(unique_nodes.values(), key=lambda n: (n.region, n.resource_type, n.name or n.id))
            for node in nodes:
                row = self.resources.rowCount(); self.resources.insertRow(row)
                direct = bool(node.tags.get(self.tag_key, "").strip())
                attribution = "ALL PRODUCTS" if self.application == "ALL PRODUCTS" else ("APPLICATION TAG" if direct else "SHARED / UNTAGGED")
                check = QTableWidgetItem(); check.setData(Qt.ItemDataRole.UserRole, node.id)
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                check.setCheckState(Qt.CheckState.Unchecked)
                self.resources.setItem(row, 0, check)
                for col, value in enumerate((node.region, node.resource_type, node.name or node.id, attribution), 1):
                    item = QTableWidgetItem(str(value)); item.setToolTip(node.id); self.resources.setItem(row, col, item)
        self.resource_type_filter.blockSignals(True)
        self.resource_type_filter.clear(); self.resource_type_filter.addItem("ALL TYPES")
        types = sorted({self.resources.item(row, 2).text() for row in range(self.resources.rowCount()) if self.resources.item(row, 2)})
        self.resource_type_filter.addItems(types)
        saved_index = self.resource_type_filter.findText(previous_type)
        self.resource_type_filter.setCurrentIndex(saved_index if saved_index >= 0 else 0)
        self.resource_type_filter.blockSignals(False)
        self._populating = False
        self.invalidate()
        self._filter_resource_rows()
        if self.graph:
            warning = f" | Discovery warnings: {len(self.graph.warnings)} (see Architecture status)" if self.graph.warnings else ""
            self.context.setText(f"Account {self.graph.account_id} | {self.application} | {len(self.graph.regions)} discovered region(s) | {self.resources.rowCount()} API Gateway candidate(s){warning}. API Gateway native metrics only.")
            self.context.setToolTip("\n".join(self.graph.warnings))
        else:
            self.context.setText("Run Architecture discovery for two regions to prepare a DR dashboard.")

    def _filter_resource_rows(self, *_args):
        """Filter locally; hiding a row never changes its checked state."""
        query = self.resource_filter.text().strip().casefold()
        resource_type = self.resource_type_filter.currentText()
        selected_only = self.selected_only.isChecked()
        visible = 0
        for row in range(self.resources.rowCount()):
            values = [self.resources.item(row, column).text() if self.resources.item(row, column) else "" for column in range(1, 5)]
            checked = self.resources.item(row, 0) and self.resources.item(row, 0).checkState() == Qt.CheckState.Checked
            matches = (not query or query in " ".join(values).casefold()) and (resource_type == "ALL TYPES" or values[1] == resource_type) and (not selected_only or checked)
            self.resources.setRowHidden(row, not matches)
            visible += int(matches)
        selected = sum(
            bool(self.resources.item(row, 0)) and self.resources.item(row, 0).checkState() == Qt.CheckState.Checked
            for row in range(self.resources.rowCount())
        )
        self.resource_visible_count.setText(f"{visible} visible / {self.resources.rowCount()} total | {selected} selected")

    def _resource_item_changed(self, *_args):
        self.invalidate()
        if self.selected_only.isChecked():
            self._filter_resource_rows()

    def _render_pairs(self):
        self.pairs.setRowCount(0)
        for pair in self.comparison_pairs:
            row = self.pairs.rowCount(); self.pairs.insertRow(row)
            source_names = ", ".join(node.name or node.id for node in pair.sources)
            dr_names = ", ".join(node.name or node.id for node in pair.drs)
            source_text = pair.source.name or pair.source.id if len(pair.sources) == 1 else f"{len(pair.sources)} resources"
            dr_text = pair.dr.name or pair.dr.id if len(pair.drs) == 1 else f"{len(pair.drs)} resources"
            for column, value in enumerate((pair.label, pair.source.resource_type, source_text, dr_text)):
                item = QTableWidgetItem(value); item.setToolTip(f"{pair.source.region}: {source_names}\n{pair.dr.region}: {dr_names}"); self.pairs.setItem(row, column, item)
        count = len(self.comparison_pairs)
        self.toggle_pairs.setText(f"COMPARISON PAIRS ({count})" + (" ▾" if self.pair_section.isVisible() else " ▸"))
        self.pair_section.setVisible(bool(count))
        self.toggle_pairs.setText(f"COMPARISON SETS ({count})")

    def toggle_pair_section(self):
        if not self.comparison_pairs:
            return
        self.pair_section.setVisible(not self.pair_section.isVisible())
        self.toggle_pairs.setText(f"COMPARISON PAIRS ({len(self.comparison_pairs)})" + (" ▾" if self.pair_section.isVisible() else " ▸"))
        state = "HIDE" if self.pair_section.isVisible() else "SHOW"
        self.toggle_pairs.setText(f"COMPARISON SETS ({len(self.comparison_pairs)}) - {state}")

    def clear_resource_selection(self):
        self._populating = True
        for row in range(self.resources.rowCount()):
            item = self.resources.item(row, 0)
            if item: item.setCheckState(Qt.CheckState.Unchecked)
        self._populating = False
        self.invalidate(); self._filter_resource_rows()

    def add_comparison_pair(self):
        if not self.graph: return
        selected = self._selected_nodes()
        regions = (self.region_a.currentText(), self.region_b.currentText())
        source_nodes = tuple(node for node in selected if node.region == regions[0])
        dr_nodes = tuple(node for node in selected if node.region == regions[1])
        if not source_nodes or not dr_nodes or len(source_nodes) + len(dr_nodes) != len(selected):
            QMessageBox.warning(self, "DR comparison", "Select at least one resource in the source region and one in the DR region.")
            return
        selected_types = {}
        for node in selected:
            selected_types[node.resource_type] = selected_types.get(node.resource_type, 0) + 1
        if len(selected_types) != 1:
            details = ", ".join(f"{resource_type}: {count}" for resource_type, count in sorted(selected_types.items()))
            QMessageBox.warning(
                self, "DR comparison",
                "A comparison set must contain one resource type in both regions.\n\n"
                f"Current selection: {details}.\n"
                "Selections remain active when a filter hides their rows. Use SHOW SELECTED ONLY to review them, or CLEAR SELECTION before selecting a new set.",
            )
            return
        source, dr = source_nodes[0], dr_nodes[0]
        if source.resource_type not in TEMPLATES:
            QMessageBox.warning(self, "DR comparison", f"{source.resource_type} does not have a native metric template yet.")
            return
        label = f"{source.name or source.id} ↔ {dr.name or dr.id}"
        if len(source_nodes) != 1 or len(dr_nodes) != 1:
            label = f"{source.resource_type} set ({len(source_nodes)} source / {len(dr_nodes)} DR)"
        pair = ComparisonPair(source, dr, label, source_nodes, dr_nodes)
        selected_ids = {node.id for node in (*source_nodes, *dr_nodes)}
        existing_ids = {node.id for existing in self.comparison_pairs for node in (*existing.sources, *existing.drs)}
        if selected_ids & existing_ids:
            QMessageBox.warning(self, "DR comparison", "A selected resource is already used in another comparison set.")
            return
        self.comparison_pairs.append(pair); self._render_pairs(); self.clear_resource_selection()
        self.context.setText(f"{len(self.comparison_pairs)} comparison set(s) prepared. Add another set or prepare metrics.")

    def remove_comparison_pair(self):
        rows = sorted({index.row() for index in self.pairs.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.comparison_pairs): self.comparison_pairs.pop(row)
        self._render_pairs(); self.invalidate()

    def invalidate(self, *_args):
        if self._populating:
            return
        self._revision += 1
        self.prepared = None
        for button in (self.export_html, self.export_json, self.export_csv, self.export_png, self.publish):
            button.setEnabled(False)
        self.actions_panel.setVisible(False)
        self.preview.scene.clear(); self.metric_table.setRowCount(0)

    def _selected_nodes(self):
        return [self.graph.nodes[item.data(Qt.ItemDataRole.UserRole)] for row in range(self.resources.rowCount())
                if (item := self.resources.item(row, 0)) and item.checkState() == Qt.CheckState.Checked]

    def _run_worker(self, operation, finished):
        if self.thread is not None:
            return
        self.prepare_button.setEnabled(False); self.publish.setEnabled(False)
        thread = QThread(self); worker = _Worker(operation); self.thread = thread; self.worker = worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.finished.connect(finished); worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater); worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup); thread.start()

    @Slot()
    def _cleanup(self):
        thread = self.thread
        self.thread = None; self.worker = None
        if thread:
            thread.deleteLater()
        self.prepare_button.setEnabled(True)
        self.publish.setEnabled(bool(self.prepared) and not self.demo)

    @Slot(object)
    def _failed(self, exc):
        self.context.setText(f"DR operation failed: {exc}")
        self.show_error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

    def prepare(self):
        if not self.graph:
            QMessageBox.warning(self, "DR dashboard", "Discover infrastructure first."); return
        profile = self._profile_current()
        if not self.demo and profile != self.graph.account_name:
            QMessageBox.warning(self, "DR dashboard", "The AWS profile changed. Discover infrastructure again."); return
        regions = (self.region_a.currentText(), self.region_b.currentText())
        if any(region not in self.graph.regions for region in regions):
            QMessageBox.warning(
                self, "DR dashboard",
                "Both selected regions must be included in the latest Architecture discovery. "
                "Choose SELECTED REGIONS in Architecture, select this source/destination pair and run DISCOVER once.",
            )
            return
        start = self.start.dateTime().toPython().replace(tzinfo=timezone.utc)
        end = self.end.dateTime().toPython().replace(tzinfo=timezone.utc)
        if start >= end or end > datetime.now(timezone.utc) + timedelta(minutes=5) or end - start > timedelta(days=455):
            QMessageBox.warning(self, "DR dashboard", "Select a valid UTC interval up to 455 days, ending no later than now."); return
        try:
            if not self.comparison_pairs:
                raise ValueError("Create at least one source/DR comparison set before preparing metrics.")
            metrics, skipped = build_pair_metrics(self.comparison_pairs, regions, self.application, self.tag_key)
            # API Gateway emits at one-minute resolution. Keep source and DR
            # traffic comparable at that granularity while CloudWatch retains it.
            age = datetime.now(timezone.utc) - start
            period = 60 if age <= timedelta(days=15) else 300 if age <= timedelta(days=63) else 3600
            body = build_dashboard(self.graph.account_id, self.application, regions, metrics, period)
        except ValueError as exc:
            QMessageBox.warning(self, "DR dashboard", str(exc)); return
        body["start"] = start.isoformat().replace("+00:00", "Z"); body["end"] = end.isoformat().replace("+00:00", "Z")
        account = self.graph.account_id; context = self.context_key; application = self.application; self.invalidate(); revision = self._revision
        self.context.setText(f"Reading existing CloudWatch metrics | account {account} | {regions[0]} / {regions[1]} | {len(metrics)} series...")
        if self.demo:
            QMessageBox.information(self, "DR dashboard demo", "Use a real AWS profile and discovery to query native CloudWatch metrics. Demo mode does not publish or query AWS.")
            return
        def operation():
            results = self.run_with_sso(profile, lambda: query_metrics(profile, account, metrics, start, end, period))
            return {"results": results, "body": body, "regions": regions, "start": start, "end": end, "period": period, "profile": profile, "account": account, "application": application, "skipped": skipped, "context": context, "revision": revision}
        self._run_worker(operation, self._prepared)

    @Slot(object)
    def _prepared(self, data):
        if data["context"] != self.context_key or data["profile"] != self._profile_current() or data["revision"] != self._revision:
            self.context.setText("Selection or AWS context changed while querying metrics. Results were discarded; prepare again."); return
        self.prepared = data
        results = data["results"]; self.metric_table.setRowCount(len(results))
        for row, result in enumerate(results):
            metric = result.metric
            last = f"{format_metric_value(result.points[-1][1])} {metric.unit}" if result.points else "—"
            for col, value in enumerate((metric.region, metric.resource_name, metric.name, result.status, last, result.detail)):
                self.metric_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.preview.show_results(results, data["regions"], data["start"], data["end"], data["period"], data["account"], data["application"])
        counts = {status: sum(result.status == status for result in results) for status in ("DATA", "NO DATA", "INCOMPLETE", "ERROR")}
        warning = f" | {len(data['skipped'])} selected resource(s) without template" if data["skipped"] else ""
        self.context.setText(f"Account {data['account']} | {data['application']} | {data['regions'][0]} / {data['regions'][1]} | DATA {counts['DATA']} | NO DATA {counts['NO DATA']} | INCOMPLETE {counts['INCOMPLETE']} | ERROR {counts['ERROR']}{warning}. Missing data is not zero; shared resources may include other applications.")
        self.context.setToolTip("\n".join(list(self.graph.warnings) + data["skipped"]))
        for button in (self.export_html, self.export_json, self.export_csv, self.export_png):
            button.setEnabled(True)
        self.actions_panel.setVisible(True)
        self.tabs.setCurrentIndex(1)

    def save_json(self):
        if not self.prepared: return
        path, _ = QFileDialog.getSaveFileName(self, "Export CloudWatch dashboard JSON", f"{artifact_prefix(self.prepared['profile'])}-Disaster_Recovery-dashboard.json", "JSON (*.json)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as file:
                    json.dump(self.prepared["body"], file, indent=2)
            except OSError as exc: self.show_error(exc)

    def save_html(self):
        if not self.prepared: return
        prefix = artifact_prefix(self.prepared["profile"])
        path, _ = QFileDialog.getSaveFileName(self, "Export DR evidence report", f"{prefix}-Disaster_Recovery-report.html", "HTML (*.html)")
        if path:
            try:
                report = render_html_report(self.prepared["profile"], self.prepared["account"], self.prepared["application"], self.prepared["regions"], self.prepared["start"], self.prepared["end"], self.prepared["results"], self.prepared["period"])
                with open(path, "w", encoding="utf-8") as file: file.write(report)
            except OSError as exc: self.show_error(exc)

    def save_csv(self):
        if not self.prepared: return
        path, _ = QFileDialog.getSaveFileName(self, "Export DR metric evidence", f"{artifact_prefix(self.prepared['profile'])}-Disaster_Recovery-metrics.csv", "CSV (*.csv)")
        if path:
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.writer(file); writer.writerow(["Account", "Application", "StartUTC", "EndUTC", "Region", "ResourceId", "Resource", "Attribution", "Namespace", "Metric", "Dimension", "DimensionValue", "Statistic", "PeriodSeconds", "Unit", "Status", "TimestampUTC", "Value", "Details"])
                    for result in self.prepared["results"]:
                        metric = result.metric
                        for timestamp, value in result.points or [(None, None)]:
                            dimensions = ", ".join(f"{name}={dimension_value}" for name, dimension_value in metric_dimensions(metric))
                            row = [self.prepared["account"], self.prepared["application"], self.prepared["start"].isoformat(), self.prepared["end"].isoformat(), metric.region, metric.resource_id, metric.resource_name, metric.attribution, metric.namespace, metric.name, dimensions, "", metric.statistic, self.prepared["period"], metric.unit, result.status, timestamp.isoformat() if timestamp else "", value if value is not None else "", result.detail]
                            writer.writerow([csv_safe_value(value) for value in row])
            except OSError as exc: self.show_error(exc)

    def save_png(self):
        if not self.prepared: return
        rect = self.preview.scene.sceneRect()
        if rect.width() > 16000 or rect.height() > 16000:
            QMessageBox.warning(self, "DR dashboard", "Preview is too large for one image. Select fewer resources or export CSV."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export DR preview", f"{artifact_prefix(self.prepared['profile'])}-Disaster_Recovery-preview.png", "PNG (*.png)")
        if path:
            image = QImage(int(rect.width()) + 1, int(rect.height()) + 1, QImage.Format.Format_ARGB32)
            image.fill(QColor("#090d0e")); painter = QPainter(image); self.preview.scene.render(painter); painter.end()
            if not image.save(path): self.show_error(RuntimeError("Unable to save the preview image."))

    def publish_dashboard(self):
        if not self.prepared or self.demo: return
        data = self.prepared
        if data["profile"] != self._profile_current() or data["context"] != self.context_key:
            self.invalidate(); QMessageBox.warning(self, "DR dashboard", "AWS context changed; prepare the dashboard again."); return
        count = len(data["body"]["widgets"])
        dashboard_name = dashboard_name_for_profile(data["profile"])
        answer = QMessageBox.question(self, "Update CloudWatch dashboard", f"Create or replace this CloudWatch dashboard?\n\nDashboard: {dashboard_name}\nAccount: {data['account']}\nApplication: {data['application']}\nRegions: {data['regions'][0]} / {data['regions'][1]}\nWidgets: {count}\n\nNo logs or metrics will be enabled. If this exact dashboard already exists, it will be replaced.")
        if answer != QMessageBox.StandardButton.Yes: return
        self._publish_region = data["regions"][0]
        self._run_worker(lambda: self.run_with_sso(data["profile"], lambda: publish_new_dashboard(data["profile"], data["account"], data["regions"][0], data["application"], data["body"])), self._published)

    @Slot(object)
    def _published(self, result):
        name, warnings = result
        detail = "\n".join(str(item.get("Message", item)) for item in warnings)
        message = f"Dashboard: {name}\n\n" + (f"AWS warnings:\n{detail}" if detail else "The dashboard was created without validation warnings.")
        if QMessageBox.question(self, "CloudWatch dashboard created", message + "\n\nOpen it in the AWS console?") == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(f"https://{self._publish_region}.console.aws.amazon.com/cloudwatch/home?region={self._publish_region}#dashboards:name={name}"))
