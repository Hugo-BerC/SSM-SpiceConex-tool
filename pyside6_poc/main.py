from __future__ import annotations

import argparse
import os
import sys
import csv
from pathlib import Path
from collections import defaultdict

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QPointF, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QImage, QLinearGradient, QRadialGradient, QKeySequence, QAction, QDesktopServices, QIcon, QPolygonF, QPen, QPainterPath
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget,
    QPlainTextEdit, QFileDialog, QDialog, QGridLayout, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsLineItem, QGraphicsPathItem,
)

from aws.ec2 import list_instances, get_instance_volumes
from app.config import APP_VERSION
from app.updater import check_for_update, launch_update_process
from aws.identity import get_caller_identity
from aws.sso import login_profile
from aws.session import clear_session_cache
from aws.profiles import discover_profiles
from models.instance import EC2Instance
from terminal.platform import open_command
from services.ssm_commands import build_port_forward_command, build_start_session_command, validate_port
from services.ssm import send_command
from services.ebs import list_instance_volumes, create_cloudwatch_dashboard
from services.performance import calculate_baseline
from services.connectivity import parse_connectivity_csv, validate_connectivity_csv
from services.certificates import (
    BALANCER_COLUMNS, CERTIFICATE_COLUMNS, discover_certificate_inventory,
)
from services.discovery.engine import DEFAULT_TAG_KEY, SUPPORTED_TAG_KEYS, discover_infrastructure
from services.discovery.models import InfrastructureGraph, ResourceNode
from utils.logging import get_logger, log_path, log_directory
from app.config import DEFAULT_REGION

logger = get_logger("ui")

ROOT = Path(__file__).resolve().parent
SKIN = ROOT.parent / "skin_spiceconex.jpg"
TEXTURE = ROOT / "dune_topography.png"
LOGO_SIDEBAR = ROOT / "spiceconex_logo_sidebar.png"
LOGO_MAIN = ROOT / "spiceconex_logo_main.png"

VOID = "#080a0b"
BASALT = "#0d1112"
SURFACE = "#121718"
SURFACE_2 = "#171d1e"
LINE = "#29302f"
TEXT = "#eee9df"
MUTED = "#858a86"
SAND = "#c7a56a"
SPICE = "#d3914d"
MELANGE = "#b96836"
MELANGE_GLOW = "#d47a3f"
GREEN = "#7ea67a"
RED = "#c56f60"


# ---------------------------------------------------------------------------
# User-facing dialogs
# ---------------------------------------------------------------------------
# Keep the visible message useful to an operator while retaining the raw
# exception in the expandable details section. This is preferable to showing
# a bare Python/boto3 exception as the only message.
def _exception_summary(context: str, exc: Exception) -> tuple[str, str]:
    message = str(exc).strip() or exc.__class__.__name__
    text = message.lower()

    if "profile" in text and ("not found" in text or "could not be found" in text):
        summary = f"The AWS profile required for {context} could not be found."
        action = "Check the selected profile and run 'aws configure list-profiles' if necessary."
    elif "unable to locate credentials" in text or "no credentials" in text:
        summary = f"No AWS credentials are available to perform {context}."
        action = "Authenticate with AWS SSO or configure the selected AWS credential source, then try again."
    elif "expiredtoken" in text or "expired token" in text:
        summary = f"The AWS session used for {context} has expired."
        action = "Refresh the AWS SSO session and try again."
    elif "accessdenied" in text or "access denied" in text or "not authorized" in text:
        summary = f"AWS denied the permissions required for {context}."
        action = "Check the IAM policy/role used by the selected profile and confirm it allows the requested AWS API operation."
    elif "could not connect" in text or "endpoint connection" in text or "connection timeout" in text:
        summary = f"AWS could not be reached while attempting {context}."
        action = "Check network connectivity, proxy settings and the selected AWS region."
    elif "validation" in context.lower():
        summary = "The AWS session could not be validated."
        action = "Check the selected profile, region and active AWS/SSO session, then try again."
    else:
        summary = f"{context} could not be completed."
        action = "Review the details below and verify the selected AWS context before retrying."

    return summary, f"{action}\n\nTechnical details:\n{message}"


def show_dialog(parent, level: QMessageBox.Icon, title: str, summary: str,
                details: str | None = None) -> None:
    box = QMessageBox(level, title, summary, QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def show_exception(parent, title: str, context: str, exc: Exception,
                   level: QMessageBox.Icon = QMessageBox.Icon.Critical) -> None:
    summary, details = _exception_summary(context, exc)
    show_dialog(parent, level, title, summary, details)


def shadow(widget, blur=28, y=8, alpha=75):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def _open_csv_dialog(parent, title: str) -> str:
    """Open the Qt widget-based CSV picker so it follows the SpiceConex theme."""
    dialog = QFileDialog(parent, title, str(Path.home()), "CSV files (*.csv);;All files (*)")
    # The native picker is outside our stylesheet. Force the Qt widget dialog
    # so the file browser uses the same Arrakis/SpiceConex visual system.
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.resize(900, 600)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        files = dialog.selectedFiles()
        return files[0] if files else ""
    return ""


class DuneBackdrop(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.texture = QPixmap(str(TEXTURE)) if TEXTURE.exists() else QPixmap()
        self.skin = QPixmap(str(SKIN)) if SKIN.exists() else QPixmap()
        self.setObjectName("backdrop")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(VOID))
        if not self.texture.isNull():
            texture = self.texture.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(0.14)
            painter.drawPixmap((self.width() - texture.width()) // 2, (self.height() - texture.height()) // 2, texture)
        if not self.skin.isNull():
            skin = self.skin.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(0.13)
            painter.drawPixmap((self.width() - skin.width()) // 2, (self.height() - skin.height()) // 2, skin)
        glow = QRadialGradient(self.width() * 0.82, self.height() * 0.78, max(self.width(), self.height()) * 0.42)
        glow.setColorAt(0.0, QColor(211, 145, 77, 28))
        glow.setColorAt(1.0, QColor(211, 145, 77, 0))
        painter.setOpacity(1.0)
        painter.fillRect(self.rect(), glow)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 58))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 28))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 18))
        painter.fillRect(self.rect(), gradient)
        painter.end()


class Sidebar(QFrame):
    pageRequested = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(224)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 22, 14, 18)
        layout.setSpacing(0)

        # The sidebar is intentionally treated like a Dune book-cover composition:
        # typography first, very little chrome, and the desert image only as texture.
        brand = QLabel(); brand.setObjectName("brandLogo")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setMinimumHeight(108)
        brand.setMaximumHeight(108)
        if LOGO_SIDEBAR.exists():
            pixmap = QPixmap(str(LOGO_SIDEBAR))
            brand.setPixmap(pixmap.scaled(204, 104, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            brand.setText("SSM\nSPICECONEX\nARRAKIS")
        layout.addWidget(brand)

        brand_rule = QFrame(); brand_rule.setObjectName("brandRule"); brand_rule.setFixedHeight(1)
        layout.addWidget(brand_rule)
        layout.addSpacing(22)

        section = QLabel("ARRAKIS OPERATIONS"); section.setObjectName("sidebarSection")
        layout.addWidget(section)
        layout.addSpacing(8)

        self.buttons = []
        entries = [("◆", "TERMINAL"), ("◇", "TUNNELING"), ("◇", "COMMAND"), ("◇", "PERFORMANCE"), ("◇", "CONNECTIVITY"), ("◇", "CERTIFICATES"), ("◇", "ARCHITECTURE")]
        for index, (glyph, title) in enumerate(entries):
            button = QToolButton(); button.setText(f"{glyph}   {title}"); button.setCheckable(True); button.setAutoExclusive(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly); button.setObjectName("navButton")
            button.setFixedHeight(36)
            button.clicked.connect(lambda checked, idx=index: self.pageRequested.emit(idx))
            layout.addWidget(button)
            self.buttons.append(button)
            if index < len(entries) - 1:
                layout.addSpacing(4)
        self.buttons[0].setChecked(True)
        layout.addStretch()

        divider = QFrame(); divider.setObjectName("divider"); divider.setFixedHeight(1); layout.addWidget(divider)
        layout.addSpacing(10)
        profile_label = QLabel("AWS CONTEXT"); profile_label.setObjectName("eyebrow"); layout.addWidget(profile_label)
        self.profile = QLabel("Not connected"); self.profile.setObjectName("sideProfile"); self.profile.setWordWrap(True); layout.addWidget(self.profile)
        self.status = QLabel("●  SYSTEM READY"); self.status.setObjectName("sideStatus"); layout.addWidget(self.status)
        self.update_button = QPushButton("CHECK FOR UPDATES")
        self.update_button.setObjectName("sidebarUpdateButton")
        self.update_button.setToolTip(f"Current version: {APP_VERSION}")
        layout.addSpacing(8)
        layout.addWidget(self.update_button)

    def set_profile(self, profile, region):
        self.profile.setText(f"{profile or 'default'}\n{region}")


class CommandRail(QFrame):
    def __init__(self):
        super().__init__(); self.setObjectName("commandRail")
        layout = QHBoxLayout(self); layout.setContentsMargins(18, 14, 18, 14); layout.setSpacing(10)
        left = QVBoxLayout(); left.setSpacing(2); label = QLabel("AWS PROFILE"); label.setObjectName("microLabel")
        self.profile = QComboBox()
        self.profile.setMinimumWidth(245)
        self.profile.setEditable(True)
        self.profile.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.profile.lineEdit().setPlaceholderText("Search AWS account / profile…")
        self.profile.setCompleter(QCompleter(self.profile.model(), self.profile))
        completer = self.profile.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        left.addWidget(label); left.addWidget(self.profile); layout.addLayout(left)
        region = QVBoxLayout(); region.setSpacing(2); region_label = QLabel("REGION"); region_label.setObjectName("microLabel")
        self.region = QComboBox(); self.region.setEditable(True); self.region.addItems(["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1"])
        self.region.setCurrentText(DEFAULT_REGION)
        region.addWidget(region_label); region.addWidget(self.region); layout.addLayout(region)
        self.refresh = QPushButton("↻  REFRESH"); self.refresh.setObjectName("quietButton"); self.refresh.setMinimumHeight(42); layout.addWidget(self.refresh, 0, Qt.AlignmentFlag.AlignBottom)
        self.validate = QPushButton("✓  VALIDATE"); self.validate.setObjectName("quietButton"); self.validate.setMinimumHeight(42); layout.addWidget(self.validate, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addStretch(1)
        search = QVBoxLayout(); search.setSpacing(2); search_label = QLabel("FLEET SEARCH"); search_label.setObjectName("microLabel")
        self.search = QLineEdit(); self.search.setPlaceholderText("Name, instance ID or private IP"); self.search.setClearButtonEnabled(True); self.search.setMinimumWidth(320)
        search.addWidget(search_label); search.addWidget(self.search); layout.addLayout(search)
        self.connect = QPushButton("CONNECT  →"); self.connect.setObjectName("primaryButton"); self.connect.setMinimumHeight(42); layout.addWidget(self.connect, 0, Qt.AlignmentFlag.AlignBottom)
        shadow(self, 24, 7, 55)


class MetricStrip(QFrame):
    def __init__(self):
        super().__init__(); self.setObjectName("metricStrip")
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0); self.values = {}
        for index, (key, title, value, suffix) in enumerate([("instances", "FLEET", "0", "INSTANCES"), ("running", "HEALTH", "0", "RUNNING"), ("selected", "TARGETS", "0", "SELECTED"), ("region", "SCOPE", DEFAULT_REGION, "REGION")]):
            block = QFrame(); block.setObjectName("metricBlock"); bl = QVBoxLayout(block); bl.setContentsMargins(18, 9, 18, 9); bl.setSpacing(1)
            caption = QLabel(title); caption.setObjectName("metricCaption"); number = QLabel(value); number.setObjectName("metricNumber"); detail = QLabel(suffix); detail.setObjectName("metricDetail")
            bl.addWidget(caption); bl.addWidget(number); bl.addWidget(detail); layout.addWidget(block, 1); self.values[key] = number
            if index < 3:
                separator = QFrame(); separator.setObjectName("metricSeparator"); separator.setFixedWidth(1); layout.addWidget(separator)
    def set_value(self, key, value): self.values[key].setText(str(value))


class InstanceRow(QFrame):
    changed = Signal(bool)
    def __init__(self, instance: EC2Instance):
        super().__init__(); self.instance = instance; self.setObjectName("instanceRow"); self.setProperty("selected", False); self.setMinimumHeight(74)
        layout = QHBoxLayout(self); layout.setContentsMargins(14, 10, 16, 10); layout.setSpacing(12)
        self.check = QCheckBox(); self.check.setObjectName("rowCheck"); self.check.setFixedWidth(22); self.check.toggled.connect(self._toggle); layout.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        identity = QVBoxLayout(); identity.setSpacing(2); name = QLabel(instance.name); name.setObjectName("instanceName"); iid = QLabel(instance.instance_id); iid.setObjectName("instanceId"); identity.addWidget(name); identity.addWidget(iid); layout.addLayout(identity, 2)
        for title, value in [("PRIVATE IP", instance.private_ip or "—"), ("PLATFORM", instance.platform_details or "Unknown")]:
            box = QVBoxLayout(); box.setSpacing(2); a = QLabel(title); a.setObjectName("rowMeta"); b = QLabel(value); b.setObjectName("rowValue"); box.addWidget(a); box.addWidget(b); layout.addLayout(box, 1)
        state = QLabel("●  RUNNING" if instance.state.lower() == "running" else "○  STOPPED"); state.setObjectName("stateBadge"); state.setProperty("running", instance.state.lower() == "running"); state.style().unpolish(state); state.style().polish(state); layout.addWidget(state, 0, Qt.AlignmentFlag.AlignVCenter)
    def _toggle(self, checked):
        self.setProperty("selected", checked); self.style().unpolish(self); self.style().polish(self); self.changed.emit(checked)
    @property
    def selected(self): return self.check.isChecked()


class FleetPanel(QFrame):
    selectionChanged = Signal(int)
    def __init__(self):
        super().__init__(); self.setObjectName("fleetPanel"); self.rows = []
        root = QVBoxLayout(self); root.setContentsMargins(20, 16, 20, 16); root.setSpacing(10)
        header = QHBoxLayout(); title = QLabel("EC2 FLEET"); title.setObjectName("sectionTitle"); header.addWidget(title); header.addStretch(); self.counter = QLabel("00 TARGETS"); self.counter.setObjectName("sectionCounter"); header.addWidget(self.counter); root.addLayout(header)
        line = QFrame(); line.setObjectName("sectionLine"); line.setFixedHeight(1); root.addWidget(line)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame); self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setObjectName("fleetContent"); self.content_layout = QVBoxLayout(content); self.content_layout.setContentsMargins(0, 4, 2, 4); self.content_layout.setSpacing(7); self.scroll.setWidget(content); root.addWidget(self.scroll, 1); shadow(self, 32, 9, 65)
    def set_instances(self, instances):
        # Rebuild the inventory without retaining visibility state from old widgets.
        selected_ids = {row.instance.instance_id for row in self.rows if row.selected}
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
        for instance in instances:
            row = InstanceRow(instance)
            row.changed.connect(self._selection_changed)
            row.check.setChecked(instance.instance_id in selected_ids)
            self.content_layout.addWidget(row)
            self.rows.append(row)
        self.content_layout.addStretch()
        self.filter(getattr(self, "_filter_query", ""))
        self._selection_changed()

    def _selection_changed(self, *_):
        count = sum(row.selected for row in self.rows)
        self.counter.setText(f"{count:02d} TARGETS")
        self.selectionChanged.emit(count)

    def selected_instances(self):
        return [row.instance for row in self.rows if row.selected]

    def filter(self, query):
        self._filter_query = (query or "").strip().casefold()
        for row in self.rows:
            instance = row.instance
            matches = not self._filter_query or any(
                self._filter_query in str(value or "").casefold()
                for value in (instance.name, instance.instance_id,
                              instance.private_ip, instance.platform_details)
            )
            # setHidden is stable even when the parent scroll area is not yet shown.
            row.setHidden(not matches)


class Worker(QObject):
    finished = Signal(object); failed = Signal(object)
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
    @Slot()
    def run(self):
        try:
            logger.info("Worker started")
            result = self.fn()
            logger.info("Worker completed")
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("Worker failed")
            self.failed.emit(exc)


def _save_csv_dialog(parent, title: str, default_name: str) -> str:
    dialog = QFileDialog(parent, title, str(Path.home() / default_name), "CSV files (*.csv);;All files (*)")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setDefaultSuffix("csv")
    dialog.resize(900, 600)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        files = dialog.selectedFiles()
        return files[0] if files else ""
    return ""


def _export_filtered_table(parent, table, title: str, default_name: str) -> bool:
    rows = table.filtered_rows()
    if not rows:
        show_dialog(parent, QMessageBox.Icon.Information, "CSV export", "There are no rows matching the current filters.")
        return False
    path = _save_csv_dialog(parent, title, default_name)
    if not path:
        return False
    headers = table.headers
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({header: row.get(table._key(header), "") for header in headers})
        logger.info("CSV export completed | file=%s | rows=%d", path, len(rows))
        show_dialog(parent, QMessageBox.Icon.Information, "CSV export", f"Exported {len(rows)} rows.", f"File: {path}")
        return True
    except Exception as exc:
        show_exception(parent, "CSV export failed", "writing the filtered CSV export", exc)
        return False


def demo_instances():
    return [
        EC2Instance(instance_id="i-0123456789abcde01", name="web-01", private_ip="192.0.2.21", platform_details="Linux/UNIX", state="running"),
        EC2Instance(instance_id="i-0123456789abcde02", name="web-02", private_ip="192.0.2.22", platform_details="Linux/UNIX", state="running"),
        EC2Instance(instance_id="i-0123456789abcde03", name="db-01", private_ip="192.0.2.31", platform_details="Linux/UNIX", state="running"),
        EC2Instance(instance_id="i-0123456789abcde04", name="worker-01", private_ip="192.0.2.41", platform_details="Linux/UNIX", state="stopped"),
        EC2Instance(instance_id="i-0123456789abcde05", name="gateway-01", private_ip="192.0.2.51", platform_details="Linux/UNIX", state="running"),
    ]


class BasePage(DuneBackdrop):
    def page_header(self, eyebrow, title, subtitle, status="◆  READY"):
        root = self.layout = QVBoxLayout(self); root.setContentsMargins(34, 28, 34, 22); root.setSpacing(16)
        header = QHBoxLayout(); box = QVBoxLayout(); box.setSpacing(2)
        for text, obj in [(eyebrow, "pageEyebrow"), (title, "pageTitle"), (subtitle, "pageSubtitle")]:
            label = QLabel(text); label.setObjectName(obj); box.addWidget(label)
        header.addLayout(box); header.addStretch(); self.status = QLabel(status); self.status.setObjectName("topStatus"); header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop); root.addLayout(header)
        return root


def _session_unavailable(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "expiredtoken", "expired token", "unauthorizedsso",
        "sso session", "token has expired", "unable to locate credentials",
        "no credentials", "credential should be refreshed", "invalidclienttokenid", "unrecognizedclientexception",
        "the security token included in the request is invalid", "security token is invalid",
    )
    return any(marker in text for marker in markers)


def _run_with_sso(profile: str, operation):
    """Run an AWS operation, refreshing an unavailable SSO session once."""
    try:
        return operation()
    except Exception as exc:
        if not _session_unavailable(exc):
            raise
        logger.warning("AWS session unavailable | profile=%r | starting SSO login", profile)
        login_profile(profile)
        clear_session_cache()
        return operation()


class PowerCon(BasePage):
    instancesChanged = Signal(object)
    def __init__(self, sidebar, demo=False):
        super().__init__(); self.sidebar = sidebar; self.demo = demo; self.instances=[]; self.thread=None
        root = self.page_header("SECURE INSTANCE ACCESS / TERMINAL", "Terminal", "Control access to your EC2 fleet from one operational surface.", "◆  DEMO MODE" if demo else "◆  READY")
        self.commands=CommandRail(); root.addWidget(self.commands); self.metrics=MetricStrip(); root.addWidget(self.metrics); self.fleet=FleetPanel(); root.addWidget(self.fleet,1)
        footer=QHBoxLayout(); tip=QLabel("COMMAND PALETTE    CTRL + K"); tip.setObjectName("footerText"); footer.addWidget(tip); footer.addStretch(); mark=QLabel("ARRAKIS  /  AWS"); mark.setObjectName("footerMark"); footer.addWidget(mark); root.addLayout(footer)
        self.commands.refresh.clicked.connect(self.load); self.commands.validate.clicked.connect(self.validate_profile); self.commands.search.textChanged.connect(self.fleet.filter)
        self.commands.region.currentTextChanged.connect(lambda v:self.metrics.set_value("region",v)); self.commands.profile.currentTextChanged.connect(lambda v:self.sidebar.set_profile(v,self.commands.region.currentText()))
        self.fleet.selectionChanged.connect(lambda c:self.metrics.set_value("selected",c)); self.commands.connect.clicked.connect(self.connect_target); self.load_profiles()
        if demo: self.set_instances(demo_instances())
    def load_profiles(self):
        logger.info("Loading AWS profiles")
        try:
            profiles = ["example-profile"] if self.demo else discover_profiles()
            self.commands.profile.clear(); self.commands.profile.addItems(profiles)
            completer = QCompleter(self.commands.profile.model(), self.commands.profile)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.commands.profile.setCompleter(completer)
            logger.info("AWS profiles discovered | count=%d", len(profiles))
            if profiles: self.commands.profile.setCurrentIndex(0); self.sidebar.set_profile(profiles[0],self.commands.region.currentText())
        except Exception as exc:
            logger.exception("Failed to discover AWS profiles")
            show_exception(self, "AWS profiles", "loading the available AWS profiles", exc)
    def set_instances(self, instances):
        logger.info("Updating fleet UI | instances=%d", len(instances))
        self.instances=list(instances); self.fleet._filter_query = self.commands.search.text(); self.fleet.set_instances(self.instances); self.instancesChanged.emit(self.instances); self.metrics.set_value("instances",len(self.instances)); self.metrics.set_value("running",sum(i.state.lower()=="running" for i in self.instances))
    def load(self):
        profile=self.commands.profile.currentText().strip()
        if not profile:
            show_dialog(self, QMessageBox.Icon.Warning, "AWS profile", "No AWS profile is selected.", "Select an AWS profile before continuing.")
            return
        region=self.commands.region.currentText().strip()
        logger.info("Starting EC2 fleet load | profile=%r | region=%s | demo=%s", profile, region, self.demo)
        self.commands.refresh.setEnabled(False); self.status.setText("●  QUERYING AWS"); logger.info("UI entered QUERYING AWS state")
        self.thread=QThread(); self.worker=Worker(lambda: demo_instances() if self.demo else _run_with_sso(profile, lambda: list_instances(profile,region))); self.worker.moveToThread(self.thread); worker=self.worker
        self.thread.started.connect(worker.run); worker.finished.connect(self.loaded); worker.failed.connect(self.failed); worker.finished.connect(self.thread.quit); worker.failed.connect(self.thread.quit); self.thread.finished.connect(self.cleanup); self.thread.start()
    @Slot(object)
    def loaded(self, instances):
        logger.info("EC2 fleet loaded into UI | instances=%d", len(instances))
        self.set_instances(instances); self.commands.refresh.setEnabled(True); self.status.setText("●  DEMO MODE" if self.demo else "●  AWS CONNECTED")
    @Slot(object)
    def failed(self, exc):
        if not isinstance(exc, Exception):
            exc = RuntimeError(str(exc))
        logger.error("EC2 fleet load failed | %s: %s", type(exc).__name__, exc)
        self.commands.refresh.setEnabled(True); self.status.setText("◆  AWS ERROR")
        show_exception(self, "AWS error", "loading the EC2 fleet", exc)
    def validate_profile(self):
        profile=self.commands.profile.currentText().strip(); region=self.commands.region.currentText().strip()
        if not profile:
            show_dialog(self, QMessageBox.Icon.Warning, "AWS profile", "No AWS profile is selected.", "Select an AWS profile before continuing.")
            return
        if self.demo:
            self.status.setText("◆  DEMO SESSION VALID")
            show_dialog(self, QMessageBox.Icon.Information, "AWS validation", "Demo mode is active; no AWS STS request was made.", "The application is using sample EC2 data. Switch to a real AWS profile to validate an AWS session.")
            return
        self.commands.validate.setEnabled(False)
        try:
            identity=_run_with_sso(profile, lambda: get_caller_identity(profile,region)); account=identity.get("Account","unknown"); masked=f"••••{account[-4:]}" if account!="unknown" else "unknown"; logger.info("AWS validation succeeded | profile=%r | region=%s", profile, region); self.status.setText("◆  AWS SESSION VALID")
            show_dialog(self, QMessageBox.Icon.Information, "AWS validation succeeded", "The selected AWS session is valid. The EC2 fleet will now be loaded.", f"Account: {masked}\nARN: {identity.get('Arn','unknown')}\nRegion: {region}")
            self.load()
        except Exception as exc:
            logger.exception("AWS validation failed | profile=%r | region=%s", profile, region)
            self.status.setText("◆  AWS SESSION INVALID")
            show_exception(self, "AWS validation failed", "validating the selected AWS session", exc)
        finally: self.commands.validate.setEnabled(True)
    def cleanup(self):
        logger.info("AWS worker thread finished")
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
        self.worker = None
    def connect_target(self):
        selected=self.fleet.selected_instances()
        if not selected:
            show_dialog(self, QMessageBox.Icon.Warning, "No targets selected", "No EC2 instances are selected for the terminal session.", "Select one or more instances in the EC2 fleet and then press CONNECT.")
            return
        profile=self.commands.profile.currentText().strip(); region=self.commands.region.currentText().strip()
        try:
            for instance in selected:
                command=build_start_session_command("aws",instance.instance_id,profile,region); open_command(command,title=instance.name)
        except Exception as exc: show_exception(self, "Terminal error", "opening the SSM terminal session", exc)


class Tunneling(BasePage):
    def __init__(self, source):
        super().__init__(); self.source=source; root=self.page_header("SECURE PORT FORWARDING / TUNNELING","Tunneling","Open an AWS Systems Manager port-forwarding session against a selected target.")
        panel=QFrame(); panel.setObjectName("tunnelPanel"); shadow(panel,30,9,60); form=QVBoxLayout(panel); form.setContentsMargins(22,20,22,22); form.setSpacing(14)
        label=QLabel("TARGET INSTANCE"); label.setObjectName("microLabel"); self.target=QComboBox(); self.target.setMinimumHeight(42); form.addWidget(label); form.addWidget(self.target)
        ports=QHBoxLayout(); ports.setSpacing(12)
        for title, attr, ph in [("LOCAL PORT","local_port","e.g. 8080"),("REMOTE PORT","remote_port","e.g. 8080")]:
            box=QVBoxLayout(); l=QLabel(title); l.setObjectName("microLabel"); e=QLineEdit(); e.setPlaceholderText(ph); e.setMinimumHeight(42); box.addWidget(l); box.addWidget(e); ports.addLayout(box,1); setattr(self,attr,e)
        form.addLayout(ports); footer=QHBoxLayout(); self.target_info=QLabel("No target loaded"); self.target_info.setObjectName("tunnelInfo"); footer.addWidget(self.target_info); footer.addStretch(); self.open_button=QPushButton("OPEN TUNNEL  →"); self.open_button.setObjectName("primaryButton"); self.open_button.setMinimumHeight(42); footer.addWidget(self.open_button); form.addLayout(footer); root.addWidget(panel,0,Qt.AlignmentFlag.AlignTop)
        hint=QLabel("AWS document: AWS-StartPortForwardingSession"); hint.setObjectName("footerText"); root.addWidget(hint); root.addStretch()
        source.instancesChanged.connect(self.set_instances); self.target.currentIndexChanged.connect(self._target_changed); self.open_button.clicked.connect(self.open_tunnel); self.set_instances(source.instances)
    def set_instances(self,instances):
        self.target.blockSignals(True); self.target.clear()
        for i in instances: self.target.addItem(f"{i.name}   ·   {i.instance_id}",i)
        self.target.blockSignals(False); self._target_changed()
    def _target_changed(self):
        i=self.target.currentData(); self.target_info.setText(f"{i.private_ip or 'No private IP'}   ·   {i.platform_details}   ·   {i.state.upper()}" if i else "No target loaded")
    def open_tunnel(self):
        i=self.target.currentData(); profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip()
        if not i:
            show_dialog(self, QMessageBox.Icon.Warning, "No target selected", "There is no EC2 target available for this operation.", "Go to Terminal, load the EC2 fleet and select a target before opening a tunnel.")
            return
        try:
            lp=validate_port(self.local_port.text().strip(),"LocalPort"); rp=validate_port(self.remote_port.text().strip(),"RemotePort")
            open_command(build_port_forward_command("aws",i.instance_id,profile,region,lp,rp),title=f"{i.name}-tunnel"); self.status.setText("●  TUNNEL OPENED")
        except Exception as exc:
            self.status.setText("◆  TUNNEL ERROR")
            show_exception(self, "Tunnel error", "opening the SSM port-forwarding session", exc)


class CommandPage(BasePage):
    def __init__(self, source):
        super().__init__(); self.source=source; self.instances=[]; root=self.page_header("REMOTE EXECUTION / AWS SYSTEMS MANAGER","Command","Execute AWS Systems Manager Run Command across selected targets.")
        panel=QFrame(); panel.setObjectName("operationPanel"); form=QVBoxLayout(panel); form.setContentsMargins(22,20,22,22); form.setSpacing(12)
        row=QHBoxLayout(); self.os_family=QComboBox(); self.os_family.addItems(["Linux","Windows"]); self.os_family.setMinimumHeight(42); row.addWidget(self._field("OS FAMILY",self.os_family),1)
        self.target_summary=QLabel("0 targets selected"); self.target_summary.setObjectName("tunnelInfo"); row.addWidget(self.target_summary,0,Qt.AlignmentFlag.AlignBottom); form.addLayout(row)
        l=QLabel("COMMAND"); l.setObjectName("microLabel"); form.addWidget(l); self.command=QPlainTextEdit(); self.command.setPlaceholderText("uname -a\n# or any command supported by the selected OS"); self.command.setMinimumHeight(150); form.addWidget(self.command)
        footer=QHBoxLayout(); self.output_status=QLabel("READY"); self.output_status.setObjectName("footerText"); footer.addWidget(self.output_status); footer.addStretch(); self.execute=QPushButton("EXECUTE  →"); self.execute.setObjectName("primaryButton"); self.execute.setMinimumHeight(42); footer.addWidget(self.execute); form.addLayout(footer); root.addWidget(panel)
        out_label=QLabel("COMMAND OUTPUT"); out_label.setObjectName("sectionTitle"); root.addWidget(out_label); self.output=QPlainTextEdit(); self.output.setReadOnly(True); self.output.setObjectName("outputBox"); root.addWidget(self.output,1)
        self.fleet_selection=lambda: self.source.fleet.selected_instances(); source.fleet.selectionChanged.connect(self._selection); self.execute.clicked.connect(self.run_command); self._selection(0)
    def _field(self,title,widget): box=QVBoxLayout(); box.setSpacing(2); l=QLabel(title); l.setObjectName("microLabel"); box.addWidget(l); box.addWidget(widget); w=QWidget(); w.setLayout(box); return w
    def _selection(self,count): self.target_summary.setText(f"{count} target{'s' if count!=1 else ''} selected")
    def run_command(self):
        selected=self.fleet_selection(); profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip(); cmd=self.command.toPlainText().strip(); os_family=self.os_family.currentText()
        if not selected:
            show_dialog(self, QMessageBox.Icon.Warning, "No targets selected", "No EC2 instances are selected for Run Command.", "Return to Terminal, select the target instances and then execute the command.")
            return
        if not cmd:
            show_dialog(self, QMessageBox.Icon.Warning, "Empty command", "There is no command to execute.", "Enter a shell command in the command editor before pressing EXECUTE.")
            return
        self.execute.setEnabled(False); self.output_status.setText("EXECUTING…"); self.output.clear()
        ids=[i.instance_id for i in selected]
        self.thread=QThread(); self.worker=Worker(lambda: _run_with_sso(profile, lambda: send_command(profile,region,ids,cmd,os_family=os_family))); self.worker.moveToThread(self.thread); worker=self.worker
        self.thread.started.connect(worker.run); worker.finished.connect(self.finished); worker.failed.connect(self.failed); worker.finished.connect(self.thread.quit); worker.failed.connect(self.thread.quit); self.thread.finished.connect(self.cleanup); self.thread.start()
    @Slot(object)
    def finished(self,results):
        blocks=[]
        for r in results:
            blocks.append(f"INSTANCE {r.instance_id}\nSTATUS  {r.status}\nCOMMAND {r.command_id}\n\nSTDOUT\n{r.stdout or '(empty)'}\n\nSTDERR\n{r.stderr or '(empty)'}\n{'─'*88}")
        self.output.setPlainText("\n".join(blocks)); self.output_status.setText("COMPLETE"); self.execute.setEnabled(True)
    @Slot(object)
    def failed(self,exc):
        if not isinstance(exc, Exception): exc = RuntimeError(str(exc))
        self.output_status.setText("ERROR"); self.execute.setEnabled(True)
        show_exception(self, "SSM command failed", "executing the AWS Systems Manager Run Command", exc)
    def cleanup(self):
        logger.info("AWS worker thread finished")
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
        self.worker = None


class PerformancePage(BasePage):
    def __init__(self, source):
        super().__init__(); self.source=source; root=self.page_header("EBS / PERFORMANCE ANALYSIS","Performance","Inspect attached EBS volumes, publish CloudWatch dashboards and calculate EBS baselines from CSV data.")
        grid=QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        ebs=QFrame(); ebs.setObjectName("operationPanel"); ef=QVBoxLayout(ebs); ef.setContentsMargins(20,18,20,18); ef.setSpacing(10)
        self.ebs_target=QComboBox(); self.ebs_target.setMinimumHeight(42); ef.addWidget(self._label("INSTANCE")); ef.addWidget(self.ebs_target)
        buttons=QHBoxLayout(); self.load_ebs=QPushButton("LOAD EBS"); self.load_ebs.setObjectName("quietButton"); self.dashboard=QPushButton("CREATE CLOUDWATCH DASHBOARD"); self.dashboard.setObjectName("primaryButton"); buttons.addWidget(self.load_ebs); buttons.addWidget(self.dashboard); ef.addLayout(buttons)
        self.ebs_output=QPlainTextEdit(); self.ebs_output.setReadOnly(True); self.ebs_output.setMinimumHeight(180); ef.addWidget(self.ebs_output); grid.addWidget(ebs,0,0)
        csv=QFrame(); csv.setObjectName("operationPanel"); cf=QVBoxLayout(csv); cf.setContentsMargins(20,18,20,18); cf.setSpacing(10); cf.addWidget(self._label("CSV BASELINE ANALYSIS")); self.csv_path=QLineEdit(); self.csv_path.setPlaceholderText("CSV with Timestamp, IOPS and Bytes/s columns"); cf.addWidget(self.csv_path); cr=QHBoxLayout(); self.browse=QPushButton("BROWSE"); self.browse.setObjectName("quietButton"); self.analyze=QPushButton("ANALYZE BASELINE"); self.analyze.setObjectName("primaryButton"); cr.addWidget(self.browse); cr.addWidget(self.analyze); cf.addLayout(cr); self.csv_output=QPlainTextEdit(); self.csv_output.setReadOnly(True); self.csv_output.setMinimumHeight(180); cf.addWidget(self.csv_output); grid.addWidget(csv,0,1)
        root.addLayout(grid); root.addStretch()
        source.instancesChanged.connect(self.set_instances); self.load_ebs.clicked.connect(self.load_ebs_data); self.dashboard.clicked.connect(self.create_dashboard); self.browse.clicked.connect(self.choose_csv); self.analyze.clicked.connect(self.analyze_csv); self.set_instances(source.instances)
    def _label(self,t): l=QLabel(t); l.setObjectName("microLabel"); return l
    def set_instances(self,instances): self.ebs_target.clear(); [self.ebs_target.addItem(f"{i.name} · {i.instance_id}",i) for i in instances]
    def load_ebs_data(self):
        i=self.ebs_target.currentData(); profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip()
        if not i:
            show_dialog(self, QMessageBox.Icon.Warning, "No target selected", "No EC2 instance is available for EBS analysis.", "Load the EC2 fleet in Terminal and select an instance first.")
            return
        try:
            volumes=list_instance_volumes(profile,region,i.instance_id); self.ebs_output.setPlainText("\n".join(f"{v['Device']}  {v['VolumeId']}  {v['Type']}  {v['Size']} GiB  IOPS={v['Iops']}  Throughput={v['Throughput']} MB/s" for v in volumes) or "No EBS volumes attached."); self.ebs_target.setProperty("volumes",volumes); self._volumes=volumes
        except Exception as exc: show_exception(self, "EBS error", "loading EBS volume information", exc)
    def create_dashboard(self):
        i=self.ebs_target.currentData(); profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip(); volumes=getattr(self,"_volumes",[])
        if not i or not volumes:
            show_dialog(self, QMessageBox.Icon.Warning, "EBS data unavailable", "There is no loaded EBS volume data for the selected instance.", "Load the EBS data first. A CloudWatch dashboard can only be created after at least one volume has been discovered.")
            return
        try:
            name=create_cloudwatch_dashboard(profile,region,i.name,volumes)
            show_dialog(self, QMessageBox.Icon.Information, "CloudWatch dashboard created", f"The dashboard for {i.name} was created successfully.", f"Dashboard: {name}\nRegion: {region}")
        except Exception as exc:
            show_exception(self, "CloudWatch error", "creating the EBS CloudWatch dashboard", exc)
    def choose_csv(self):
        path = _open_csv_dialog(self, "Select CSV")
        if path: self.csv_path.setText(path)
    def analyze_csv(self):
        path=self.csv_path.text().strip()
        if not path: self.choose_csv(); path=self.csv_path.text().strip()
        if not path: return
        try:
            data=calculate_baseline(path); lines=[]
            for key,value in data.items():
                if isinstance(value,list): lines.append(key + ":\n" + "\n".join(f"  {item}" for item in value))
                else: lines.append(f"{key}: {value:.2f}" if isinstance(value,float) else f"{key}: {value}")
            self.csv_output.setPlainText("\n".join(lines))
        except Exception as exc: show_exception(self, "Baseline analysis error", "calculating the EBS performance baseline", exc)


class FilterableTable(QFrame):
    """Compact dark inventory table with text and enum filters per column."""

    BALANCER_FILTERS = {
        "Type": ["application", "network"],
        "Scheme": ["internet-facing", "internal"],
        "Protocol": ["HTTPS", "TLS"],
        "CertStatus": [
            "PENDING_VALIDATION", "ISSUED", "INACTIVE", "EXPIRED",
            "VALIDATION_TIMED_OUT", "REVOKED", "FAILED",
        ],
        "Expiry": ["EXPIRED", "0-3M", "3-6M", "6M+", "N/A"],
        "Match": ["MATCH", "MISMATCH", "LISTENER"],
    }

    CERTIFICATE_FILTERS = {
        "Status": [
            "PENDING_VALIDATION", "ISSUED", "INACTIVE", "EXPIRED",
            "VALIDATION_TIMED_OUT", "REVOKED", "FAILED",
        ],
        "Type": ["AMAZON_ISSUED", "IMPORTED", "PRIVATE"],
        "Expiry": ["EXPIRED", "0-3M", "3-6M", "6M+", "N/A"],
        "InUse": ["YES", "NO"],
    }

    filterChanged = Signal()

    def __init__(self, headers, object_name="inventoryTable", filter_options=None):
        super().__init__()
        self.setObjectName("filterTable")
        self.headers = list(headers)
        self.rows = []
        self.keys = [self._key(header) for header in self.headers]
        self.sort_column = -1
        self.sort_order = Qt.SortOrder.AscendingOrder
        self.filter_options = filter_options or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.filter_scroll = QScrollArea()
        self.filter_scroll.setObjectName("columnFilterScroll")
        self.filter_scroll.setWidgetResizable(False)
        self.filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        filter_host = QWidget()
        filter_host.setObjectName("columnFilterHost")
        self.filter_layout = QHBoxLayout(filter_host)
        self.filter_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_layout.setSpacing(5)
        self.filters = []

        for header, key in zip(self.headers, self.keys):
            options = self.filter_options.get(key)
            if options:
                control = QComboBox()
                control.setObjectName("columnFilterCombo")
                control.setMinimumHeight(30)
                control.setFixedWidth(150)
                control.addItem("ALL")
                for value in options:
                    control.addItem(str(value), str(value))
                control.currentIndexChanged.connect(self._apply_filters)
            else:
                control = QLineEdit()
                control.setObjectName("columnFilter")
                control.setPlaceholderText(f"Filter {header}")
                control.setClearButtonEnabled(True)
                control.setMinimumHeight(30)
                control.setFixedWidth(150)
                control.textChanged.connect(self._apply_filters)
            self.filter_layout.addWidget(control)
            self.filters.append(control)

        self.filter_layout.addStretch(1)
        self.filter_scroll.setWidget(filter_host)
        self.filter_host = filter_host
        root.addWidget(self.filter_scroll)

        self.table = QTableWidget(0, len(self.headers))
        self.table.setObjectName(object_name)
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(31)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setSortingEnabled(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for column in range(len(self.headers)):
            self.table.setColumnWidth(column, 150)
        header_view.sectionClicked.connect(self._sort)
        self.table.horizontalScrollBar().valueChanged.connect(
            self.filter_scroll.horizontalScrollBar().setValue
        )
        root.addWidget(self.table, 1)

    @staticmethod
    def _key(header):
        return "".join(part if index == 0 else part.title() for index, part in enumerate(header.replace("/", " ").split()))

    def set_rows(self, rows):
        self.rows = list(rows)
        self.sort_column = -1
        self.sort_order = Qt.SortOrder.AscendingOrder
        self._render()

    def clear_filters(self):
        changed = False
        for control in self.filters:
            if isinstance(control, QComboBox):
                if control.currentIndex() != 0:
                    control.setCurrentIndex(0)
                    changed = True
            else:
                if control.text():
                    control.clear()
                    changed = True
        if not changed:
            self._render()

    def _apply_filters(self):
        self._render()

    def _filter_value(self, control):
        if isinstance(control, QComboBox):
            return control.currentData()
        return control.text().strip()

    def _matches(self, row):
        for control, key in zip(self.filters, self.keys):
            value = self._filter_value(control)
            if value in (None, ""):
                continue
            actual = row.get(key, "")
            if isinstance(actual, bool):
                actual = "YES" if actual else "NO"
            if isinstance(control, QComboBox):
                if str(actual).casefold() != str(value).casefold():
                    return False
            elif str(value).casefold() not in str(actual).casefold():
                return False
        return True

    def filtered_rows(self):
        rows = [row for row in self.rows if self._matches(row)]
        if self.sort_column >= 0:
            key = self.keys[self.sort_column]
            rows.sort(key=lambda item: str(item.get(key, "")).casefold())
            if self.sort_order == Qt.SortOrder.DescendingOrder:
                rows.reverse()
        return rows

    def _sort(self, column):
        if self.sort_column == column:
            self.sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.AscendingOrder
        self._render()

    def _render(self):
        rows = [row for row in self.rows if self._matches(row)]

        if self.sort_column >= 0:
            key = self.keys[self.sort_column]
            rows.sort(key=lambda item: str(item.get(key, "")).casefold())
            if self.sort_order == Qt.SortOrder.DescendingOrder:
                rows.reverse()

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        for row in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            for column, key in enumerate(self.keys):
                value = row.get(key, "")
                if isinstance(value, bool):
                    value = "YES" if value else "NO"
                item = QTableWidgetItem(str(value))
                if key in {"Status", "CertStatus"}:
                    status = str(value).upper()
                    if status == "ISSUED":
                        item.setForeground(QColor(GREEN))
                    elif status in {"EXPIRED", "REVOKED", "FAILED", "INACTIVE"}:
                        item.setForeground(QColor(RED))
                if key in {"Expiry", "Match"}:
                    marker = str(value).upper()
                    if marker in {"EXPIRED", "MISMATCH"}:
                        item.setForeground(QColor(RED))
                    elif marker in {"0-3M", "LISTENER"}:
                        item.setForeground(QColor(MELANGE_GLOW))
                    elif marker in {"6M+", "MATCH"}:
                        item.setForeground(QColor(GREEN))
                self.table.setItem(index, column, item)
        self.table.setUpdatesEnabled(True)
        self.filterChanged.emit()

    def count_text(self, label):
        visible = self.table.rowCount()
        return f"{visible} / {len(self.rows)} {label}"


class CertificatesPage(BasePage):
    """ACM inventory and current ALB/NLB TLS certificate associations."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.thread = None
        self.worker = None
        self.inventory = {"certificates": [], "balancers": []}
        root = self.page_header(
            "CERTIFICATE INTELLIGENCE / ACM",
            "Certificates",
            "Discover ACM certificates and the ALB/NLB resources currently using them in the selected AWS account.",
        )

        panel = QFrame()
        panel.setObjectName("operationPanel")
        form = QHBoxLayout(panel)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)
        context = QVBoxLayout()
        context.setSpacing(2)
        label = QLabel("AWS CONTEXT")
        label.setObjectName("microLabel")
        self.context = QLabel("Select an account in Terminal")
        self.context.setObjectName("inventoryContext")
        context.addWidget(label)
        context.addWidget(self.context)
        form.addLayout(context, 1)
        self.discover = QPushButton("DISCOVER  →")
        self.discover.setObjectName("primaryButton")
        self.discover.setMinimumHeight(42)
        form.addWidget(self.discover)
        root.addWidget(panel)

        toolbar = QHBoxLayout()
        self.summary = QLabel("No certificate inventory loaded.")
        self.summary.setObjectName("tunnelInfo")
        toolbar.addWidget(self.summary)
        toolbar.addStretch()
        clear = QPushButton("CLEAR FILTERS")
        clear.setObjectName("quietButton")
        clear.clicked.connect(self._clear_filters)
        toolbar.addWidget(clear)
        root.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("inventoryTabs")
        cert_page = QWidget()
        cert_layout = QVBoxLayout(cert_page)
        cert_layout.setContentsMargins(8, 12, 8, 8)
        cert_layout.setSpacing(8)
        cert_header = QHBoxLayout()
        cert_title = QLabel("CERTIFICATES")
        cert_title.setObjectName("sectionTitle")
        cert_header.addWidget(cert_title)
        cert_header.addStretch()
        self.cert_count = QLabel("0 / 0 certificates")
        self.cert_count.setObjectName("sectionCounter")
        cert_header.addWidget(self.cert_count)
        self.cert_export = QPushButton("EXPORT CSV")
        self.cert_export.setObjectName("quietButton")
        self.cert_export.clicked.connect(lambda: _export_filtered_table(self, self.cert_table, "Export certificate inventory", "certificates.csv"))
        cert_header.addWidget(self.cert_export)
        cert_layout.addLayout(cert_header)
        self.cert_table = FilterableTable(CERTIFICATE_COLUMNS, "certificateTable", FilterableTable.CERTIFICATE_FILTERS)
        self.cert_table.filterChanged.connect(lambda: self.cert_count.setText(self.cert_table.count_text("certificates")))
        cert_layout.addWidget(self.cert_table, 1)
        self.tabs.addTab(cert_page, "CERTIFICATES")

        lb_page = QWidget()
        lb_layout = QVBoxLayout(lb_page)
        lb_layout.setContentsMargins(8, 12, 8, 8)
        lb_layout.setSpacing(8)
        lb_header = QHBoxLayout()
        lb_title = QLabel("BALANCERS")
        lb_title.setObjectName("sectionTitle")
        lb_header.addWidget(lb_title)
        lb_header.addStretch()
        self.lb_count = QLabel("0 / 0 correlation rows")
        self.lb_count.setObjectName("sectionCounter")
        lb_header.addWidget(self.lb_count)
        self.lb_export = QPushButton("EXPORT CSV")
        self.lb_export.setObjectName("quietButton")
        self.lb_export.clicked.connect(lambda: _export_filtered_table(self, self.lb_table, "Export ALB/NLB correlation", "load-balancers.csv"))
        lb_header.addWidget(self.lb_export)
        lb_layout.addLayout(lb_header)
        self.lb_table = FilterableTable(BALANCER_COLUMNS, "balancerTable", FilterableTable.BALANCER_FILTERS)
        self.lb_table.filterChanged.connect(lambda: self.lb_count.setText(self.lb_table.count_text("correlation rows")))
        lb_layout.addWidget(self.lb_table, 1)
        self.tabs.addTab(lb_page, "BALANCERS")
        root.addWidget(self.tabs, 1)

        self.discover.clicked.connect(self.run_discovery)
        self.source.commands.profile.currentTextChanged.connect(lambda _value: self._update_context())
        self.source.commands.region.currentTextChanged.connect(lambda _value: self._update_context())
        self._update_context()

    def _update_context(self):
        profile = self.source.commands.profile.currentText().strip()
        region = self.source.commands.region.currentText().strip() or DEFAULT_REGION
        if profile:
            self.context.setText(f"{profile}  ·  {region}")
        else:
            self.context.setText("Select an account in Terminal")

    def run_discovery(self):
        profile = self.source.commands.profile.currentText().strip()
        region = self.source.commands.region.currentText().strip()
        if not profile:
            show_dialog(
                self,
                QMessageBox.Icon.Warning,
                "No AWS account selected",
                "No AWS profile is selected in Terminal.",
                "Go to Terminal, select the account/profile and return to Certificates.",
            )
            return
        if not region:
            show_dialog(
                self,
                QMessageBox.Icon.Warning,
                "No AWS region selected",
                "No region is configured in Terminal.",
                "Select an AWS region in Terminal and retry the discovery.",
            )
            return

        self._update_context()
        self.discover.setEnabled(False)
        self.summary.setText(f"Discovering ACM and ALB/NLB resources in {region}…")
        self.status.setText("●  QUERYING AWS")
        self.thread = QThread()
        self.worker = Worker(lambda: _run_with_sso(profile, lambda: discover_certificate_inventory(profile, region)))
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup)
        self.thread.start()

    @Slot(object)
    def finished(self, inventory):
        self.inventory = inventory
        certificates = inventory.get("certificates", [])
        balancers = inventory.get("balancers", [])
        self.cert_table.set_rows(certificates)
        self.lb_table.set_rows(balancers)
        self.cert_count.setText(self.cert_table.count_text("certificates"))
        self.lb_count.setText(self.lb_table.count_text("correlation rows"))
        self.summary.setText(
            f"Account {inventory.get('account_id', 'unknown')} · {len(certificates)} certificates · {len(balancers)} ALB/NLB correlation rows · Region {inventory.get('region', '')}"
        )
        self.status.setText("●  INVENTORY READY")
        self.discover.setEnabled(True)
        logger.info(
            "Certificates UI updated | certificates=%d | balancer_rows=%d",
            len(certificates), len(balancers),
        )

    @Slot(object)
    def failed(self, exc):
        if not isinstance(exc, Exception):
            exc = RuntimeError(str(exc))
        self.status.setText("◆  AWS ERROR")
        self.summary.setText("Certificate discovery failed.")
        self.discover.setEnabled(True)
        logger.error("Certificate discovery failed | %s: %s", type(exc).__name__, exc)
        show_exception(
            self,
            "Certificate discovery failed",
            "discovering ACM certificates and ALB/NLB resources",
            exc,
        )

    def _clear_filters(self):
        self.cert_table.clear_filters()
        self.lb_table.clear_filters()

    def cleanup(self):
        logger.info("Certificate discovery worker thread finished")
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
        self.worker = None


class ConnectivityMap(QGraphicsView):
    """A compact, result-aware topology for a loaded connectivity matrix."""
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor(9, 13, 14))
        self.setFrameShape(QFrame.Shape.NoFrame)

    def wheelEvent(self, event):
        self.scale(1.15 if event.angleDelta().y() > 0 else 1 / 1.15, 1.15 if event.angleDelta().y() > 0 else 1 / 1.15)

    def render_connections(self, source_name, rows, results=None):
        self.scene.clear()
        result_map = {(str(item.get("service", "")), str(item.get("destination", "")), str(item.get("port", "")), str(item.get("scope", ""))): item for item in (results or [])}
        if not rows:
            hint = self.scene.addText("Attach a connectivity CSV to render the connection map.")
            hint.setDefaultTextColor(QColor(MUTED)); hint.setPos(28, 28); self.scene.setSceneRect(0, 0, 820, 380); return
        source_x, source_y, source_w, source_h = 48, 80, 245, 92
        source = self.scene.addRect(source_x, source_y, source_w, source_h)
        source.setBrush(QColor(23, 31, 31)); source.setPen(QPen(QColor(SPICE), 2))
        source.setToolTip("Managed EC2 instance used as the source of every SSM TCP test.")
        label = self.scene.addText("SSM TEST SOURCE"); label.setDefaultTextColor(QColor(SAND)); label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold)); label.setPos(source_x + 14, source_y + 12)
        label = self.scene.addText((source_name or "Select a source instance")[:31]); label.setDefaultTextColor(QColor(TEXT)); label.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold)); label.setPos(source_x + 14, source_y + 38)
        groups = {"internal": [], "external": [], "unknown": []}
        for row in rows: groups.get(str(row.get("scope", "")).lower(), groups["unknown"]).append(row)
        columns = [("internal", "INTERNAL DESTINATIONS"), ("external", "EXTERNAL DESTINATIONS"), ("unknown", "OTHER DESTINATIONS")]
        target_x, max_rows = 405, max((len(groups[name]) for name, _ in columns), default=1)
        for column, (scope, heading) in enumerate(columns):
            column_x = target_x + column * 305
            title = self.scene.addText(heading); title.setDefaultTextColor(QColor(SAND if scope != "external" else SPICE)); title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold)); title.setPos(column_x, 18)
            for index, row in enumerate(groups[scope]):
                y = 62 + index * 104
                result = result_map.get((row["service"], row["destination"], row["port"], row["scope"]), {})
                status = str(result.get("status", "")).upper()
                color = QColor(GREEN if status == "OK" else RED if status in {"KO", "FAILED", "ERROR", "TIMEOUT", "REFUSED", "UNREACHABLE"} else MUTED)
                line = QGraphicsLineItem(source_x + source_w, source_y + source_h / 2, column_x, y + 39); line.setPen(QPen(color, 2 if status else 1.2)); line.setToolTip(f"{row['service']} → {row['destination']}:{row['port']} · {status or 'PENDING'}"); self.scene.addItem(line)
                card = self.scene.addRect(column_x, y, 265, 78); card.setBrush(QColor(18, 23, 24)); card.setPen(QPen(color, 1.6)); card.setToolTip(f"Service: {row['service']}\nDestination: {row['destination']}:{row['port']}\nScope: {row['scope']}\nProtocol: {row['protocol']}\nStatus: {status or 'Not tested'}\n{result.get('detail', '')}")
                text = self.scene.addText(row["service"][:30]); text.setDefaultTextColor(QColor(TEXT)); text.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold)); text.setPos(column_x + 12, y + 10)
                text = self.scene.addText(f"{row['destination']}:{row['port']}  ·  {row['protocol']}"); text.setDefaultTextColor(QColor(MUTED)); text.setFont(QFont("Segoe UI", 8)); text.setPos(column_x + 12, y + 32)
                text = self.scene.addText(status or "PENDING"); text.setDefaultTextColor(color); text.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold)); text.setPos(column_x + 12, y + 54)
        legend = self.scene.addText("CONNECTION MAP  ·  green: reachable  ·  red: failed or timed out  ·  grey: not tested")
        legend.setDefaultTextColor(QColor(MUTED)); legend.setFont(QFont("Segoe UI", 8)); legend.setPos(24, 14)
        self.scene.setSceneRect(0, 0, target_x + len(columns) * 305, max(290, 92 + max_rows * 104))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class ConnectivityPage(BasePage):
    """CSV-driven connectivity validation migrated from the legacy UI."""
    def __init__(self, source):
        super().__init__(); self.source=source; self.rows=[]; self.thread=None; self.worker=None
        root=self.page_header("NETWORK PATH VALIDATION / SSM","Connectivity","Load a service matrix CSV and validate TCP reachability from a managed EC2 instance.")

        panel=QFrame(); panel.setObjectName("operationPanel"); form=QVBoxLayout(panel); form.setContentsMargins(22,20,22,22); form.setSpacing(12)
        top=QHBoxLayout(); self.target=QComboBox(); self.target.setMinimumHeight(42); top.addWidget(self._field("SOURCE INSTANCE",self.target),2)
        self.timeout=QLineEdit("120"); self.timeout.setMinimumHeight(42); top.addWidget(self._field("TIMEOUT (S)",self.timeout),0)
        self.csv_path=QLineEdit(); self.csv_path.setReadOnly(True); self.csv_path.setPlaceholderText("No connectivity CSV loaded"); top.addWidget(self._field("CONNECTIVITY MATRIX",self.csv_path),3)
        self.browse=QPushButton("ATTACH CSV"); self.browse.setObjectName("quietButton"); self.browse.setMinimumHeight(42); top.addWidget(self.browse,0,Qt.AlignmentFlag.AlignBottom)
        self.validate=QPushButton("RUN VALIDATION  →"); self.validate.setObjectName("primaryButton"); self.validate.setMinimumHeight(42); top.addWidget(self.validate,0,Qt.AlignmentFlag.AlignBottom)
        form.addLayout(top)
        self.summary=QLabel("Load a CSV and select a source instance to begin."); self.summary.setObjectName("tunnelInfo"); form.addWidget(self.summary)
        root.addWidget(panel)

        splitter=QHBoxLayout(); splitter.setSpacing(14)
        left=QFrame(); left.setObjectName("operationPanel"); lf=QVBoxLayout(left); lf.setContentsMargins(16,14,16,14); lf.addWidget(self._label("INPUT SERVICES"))
        self.input_table=QTableWidget(0,5); self._setup_table(self.input_table,["Service","Destination","Port","Scope","Protocol"]); lf.addWidget(self.input_table,1)
        splitter.addWidget(left,1)
        right=QFrame(); right.setObjectName("operationPanel"); rf=QVBoxLayout(right); rf.setContentsMargins(16,14,16,14); rf.addWidget(self._label("VALIDATION RESULTS"))
        self.results_table=QTableWidget(0,7); self._setup_table(self.results_table,["Service","Destination","Port","Scope","Status","Duration","Detail"]); rf.addWidget(self.results_table,1)
        splitter.addWidget(right,2)
        matrix_page=QWidget(); matrix_layout=QVBoxLayout(matrix_page); matrix_layout.setContentsMargins(0,0,0,0); matrix_layout.addLayout(splitter,1)
        self.tabs=QTabWidget(); self.tabs.setObjectName("inventoryTabs"); self.tabs.addTab(matrix_page,"MATRIX & RESULTS")
        map_page=QWidget(); map_layout=QVBoxLayout(map_page); map_layout.setContentsMargins(0,0,0,0)
        map_hint=QLabel("Drag to pan · mouse wheel to zoom · hover paths for test details"); map_hint.setObjectName("footerText"); map_layout.addWidget(map_hint)
        self.connection_map=ConnectivityMap(); map_layout.addWidget(self.connection_map,1); self.tabs.addTab(map_page,"CONNECTION MAP")
        root.addWidget(self.tabs,1)
        self.detail=QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(150); self.detail.setPlaceholderText("Select a validation result for details."); root.addWidget(self.detail)

        source.instancesChanged.connect(self.set_instances); self.browse.clicked.connect(self.choose_csv); self.validate.clicked.connect(self.run); self.results_table.itemSelectionChanged.connect(self.show_detail); self.set_instances(source.instances)

    def _label(self,t): l=QLabel(t); l.setObjectName("microLabel"); return l
    def _field(self,title,widget):
        box=QVBoxLayout(); box.setSpacing(2); l=QLabel(title); l.setObjectName("microLabel"); box.addWidget(l); box.addWidget(widget); w=QWidget(); w.setLayout(box); return w
    def _setup_table(self,table,headers):
        table.setHorizontalHeaderLabels(headers); table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); table.setAlternatingRowColors(False); table.verticalHeader().setVisible(False); table.horizontalHeader().setStretchLastSection(True); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setMinimumHeight(220); table.verticalHeader().setDefaultSectionSize(30)
    def set_instances(self,instances):
        self.target.blockSignals(True); self.target.clear()
        for i in instances: self.target.addItem(f"{i.name} · {i.instance_id}",i)
        self.target.blockSignals(False)
        if self.rows: self.connection_map.render_connections(self._source_name(),self.rows)
    def _source_name(self):
        instance=self.target.currentData()
        return f"{instance.name} · {instance.instance_id}" if instance else ""
    def choose_csv(self):
        path = _open_csv_dialog(self, "Attach connectivity CSV")
        if not path: return
        try:
            rows=parse_connectivity_csv(path); self.rows=rows; self.csv_path.setText(path); self.input_table.setRowCount(0)
            for row in rows:
                r=self.input_table.rowCount(); self.input_table.insertRow(r)
                for c,key in enumerate(("service","destination","port","scope","protocol")): self.input_table.setItem(r,c,QTableWidgetItem(row[key]))
            self.connection_map.render_connections(self._source_name(),rows)
            self.summary.setText(f"Loaded {len(rows)} services from {Path(path).name}")
            logger.info("Connectivity CSV attached | file=%s | services=%d", path, len(rows))
        except Exception as exc:
            logger.exception("Connectivity CSV load failed")
            show_exception(self,"Connectivity CSV","loading the connectivity CSV",exc,QMessageBox.Icon.Warning)
    def run(self):
        i=self.target.currentData(); profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip()
        if not i:
            show_dialog(self,QMessageBox.Icon.Warning,"No source instance","No EC2 source instance is available.","Load the EC2 fleet in Terminal first and select an instance for the connectivity test."); return
        if not self.rows:
            show_dialog(self,QMessageBox.Icon.Warning,"No connectivity CSV","No connectivity matrix is loaded.","Use ATTACH CSV to load the service matrix before running validation."); return
        try: timeout=int(self.timeout.text().strip());
        except ValueError: timeout=0
        if timeout < 1 or timeout > 300:
            show_dialog(self,QMessageBox.Icon.Warning,"Invalid timeout","The connectivity timeout must be between 1 and 300 seconds.","Enter a valid timeout and retry."); return
        self.validate.setEnabled(False); self.browse.setEnabled(False); self.summary.setText(f"Running validation from {i.name} in {region}…"); self.results_table.setRowCount(0); self.detail.clear()
        self.connection_map.render_connections(self._source_name(),self.rows)
        self.thread=QThread(); self.worker=Worker(lambda: _run_with_sso(profile,lambda: validate_connectivity_csv(profile,region,i.instance_id,self.rows,timeout))); self.worker.moveToThread(self.thread); worker=self.worker
        self.thread.started.connect(worker.run); worker.finished.connect(self.finished); worker.failed.connect(self.failed); worker.finished.connect(self.thread.quit); worker.failed.connect(self.thread.quit); self.thread.finished.connect(self.cleanup); self.thread.start()
    @Slot(object)
    def finished(self,payload):
        results,command_result=payload; self.results_table.setRowCount(0)
        for row in results:
            r=self.results_table.rowCount(); self.results_table.insertRow(r)
            for c,key in enumerate(("service","destination","port","scope","status","duration","detail")):
                item=QTableWidgetItem(str(row.get(key,"")))
                if key == "status":
                    status=str(row.get(key,"" )).upper()
                    if status == "OK": item.setForeground(QColor(GREEN))
                    elif status in {"KO", "FAILED", "ERROR", "TIMEOUT"}: item.setForeground(QColor(RED))
                    else: item.setForeground(QColor(SAND))
                self.results_table.setItem(r,c,item)
        ok=sum(1 for r in results if r.get("status","").upper()=="OK"); ko=len(results)-ok
        self.connection_map.render_connections(self._source_name(),self.rows,results)
        self.summary.setText(f"Validated {len(results)} services · OK {ok} · KO {ko} · Source {self.target.currentData().name if self.target.currentData() else ''} · Region {self.source.commands.region.currentText()}")
        self.validate.setEnabled(True); self.browse.setEnabled(True)
        logger.info("Connectivity UI updated | results=%d | ok=%d | ko=%d",len(results),ok,ko)
    @Slot(object)
    def failed(self,exc):
        if not isinstance(exc,Exception): exc=RuntimeError(str(exc))
        self.summary.setText("◆  VALIDATION FAILED"); self.validate.setEnabled(True); self.browse.setEnabled(True); logger.error("Connectivity validation failed | %s: %s", type(exc).__name__, exc)
        show_exception(self,"Connectivity validation failed","running the CSV connectivity validation",exc)
    def show_detail(self):
        row=self.results_table.currentRow()
        if row < 0: return
        values=[self.results_table.item(row,c).text() if self.results_table.item(row,c) else "" for c in range(self.results_table.columnCount())]
        self.detail.setPlainText("Service: %s\nDestination: %s\nPort: %s\nScope: %s\nStatus: %s\nDuration: %s ms\nDetail: %s" % tuple(values))
    def cleanup(self):
        logger.info("Connectivity worker thread finished")
        if self.thread: self.thread.deleteLater(); self.thread=None
        self.worker=None


STYLE = """
* { font-family: "Segoe UI", "Inter", sans-serif; color: #eee9df; }
QMainWindow { background: #080a0b; }
QToolTip { background: #151b1c; color: #eee9df; border: 1px solid #3b3930; padding: 6px; }
#sidebar { background: #090b0c; border-right: 1px solid #252b2a; }
#brandLogo { background: transparent; padding: 0; }
#brandRule { background: rgba(185,104,54,0.48); margin: 0 8px; }
#sidebarSection { color: #766b59; font-size: 7px; font-weight: 780; letter-spacing: 2.2px; padding-left: 11px; }
#eyebrow, #microLabel { color: #9a8666; font-size: 8px; font-weight: 760; letter-spacing: 1.9px; }
#navButton { text-align: left; border: 0; border-left: 2px solid transparent; border-radius: 0; padding: 8px 10px; color: #777d79; font-size: 10px; font-weight: 700; background: transparent; }
#navButton:hover { background: rgba(199,165,106,0.035); color: #d7d0c4; }
#navButton:checked { background: rgba(185,104,54,0.035); color: #e0c68e; border-left: 2px solid #b96836; padding-left: 10px; }
#divider { background: #252b2a; }
#sideProfile { color: #c9c0ae; font-size: 10px; line-height: 1.4; }
#sideStatus { color: #7ea67a; font-size: 9px; font-weight: 760; letter-spacing: 1.2px; margin-top: 5px; }
#backdrop { background: #080a0b; }
#pageEyebrow { color: #777d79; font-size: 9px; font-weight: 760; letter-spacing: 2.1px; }
#pageTitle { font-size: 31px; font-weight: 690; color: #eee9df; }
#pageSubtitle { color: #8b918d; font-size: 11px; }
#topStatus { background: rgba(185,104,54,0.10); border: 1px solid rgba(185,104,54,0.28); border-radius: 14px; color: #d47a3f; padding: 7px 12px; font-size: 9px; font-weight: 760; letter-spacing: 1px; }
#commandRail, #operationPanel, #tunnelPanel { background: rgba(17,22,23,0.93); border: 1px solid #29302f; border-top: 2px solid rgba(185,104,54,0.52); border-radius: 11px; }
QComboBox, QLineEdit, QPlainTextEdit { background: #0e1314; border: 1px solid #28302f; border-radius: 7px; padding: 9px 11px; color: #eee9df; selection-background-color: #3a2d1d; }
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { border-color: #424946; }
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border-color: #b96836; }
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox::down-arrow { image: none; width: 6px; height: 6px; border-right: 1px solid #c7a56a; border-bottom: 1px solid #c7a56a; margin-right: 10px; }
QComboBox QAbstractItemView { background: #101516; border: 1px solid #4a3529; selection-background-color: #352217; selection-color: #d47a3f; padding: 6px; outline: 0; }
QComboBox QLineEdit { background: #0e1314; color: #eee9df; border: 0; padding: 0; selection-background-color: #3a2d1d; selection-color: #eee9df; }
QComboBox QLineEdit:focus { background: #0e1314; color: #eee9df; border: 0; }
#quietButton, #primaryButton { border-radius: 7px; padding: 10px 14px; font-weight: 760; font-size: 10px; letter-spacing: .8px; }
#quietButton { background: #171d1e; border: 1px solid #303735; color: #c9c0ae; }
#quietButton:hover { background: #1d2425; border-color: #4a4c45; }
#primaryButton { background: #c7a56a; color: #16120d; }
#primaryButton:hover { background: #dfbd7f; }
#metricStrip { background: rgba(13,17,18,0.78); border-top: 1px solid #252c2b; border-bottom: 1px solid #252c2b; }
#metricBlock { background: transparent; }
#metricSeparator { background: #252c2b; }
#metricCaption { color: #777d79; font-size: 8px; font-weight: 760; letter-spacing: 1.7px; }
#metricNumber { color: #eee9df; font-size: 23px; font-weight: 680; }
#metricDetail { color: #656c68; font-size: 8px; font-weight: 650; letter-spacing: 1px; }
#fleetPanel { background: rgba(15,20,21,0.90); border: 1px solid #29302f; border-radius: 11px; border-top-color: rgba(185,104,54,0.35); }
#fleetContent { background: transparent; }
#sectionTitle { color: #d7d0c4; font-size: 10px; font-weight: 780; letter-spacing: 1.8px; }
#sectionCounter { color: #7e8480; font-size: 9px; font-weight: 760; letter-spacing: 1.2px; }
#sectionLine { background: rgba(185,104,54,0.34); }
#instanceRow { background: rgba(18,24,25,0.88); border: 1px solid #242c2b; border-radius: 8px; }
#instanceRow:hover { background: rgba(27,34,34,0.95); border-color: #3a403d; }
#instanceRow[selected="true"] { background: rgba(185,104,54,0.085); border-color: #7f4b2f; }
#instanceName { color: #eee9df; font-size: 12px; font-weight: 670; }
#instanceId { color: #69716c; font-size: 9px; }
#rowMeta { color: #626a66; font-size: 7px; font-weight: 760; letter-spacing: 1.2px; }
#rowValue { color: #c9c4b9; font-size: 10px; }
#stateBadge { color: #777d79; font-size: 9px; font-weight: 760; letter-spacing: .7px; min-width: 92px; }
#stateBadge[running="true"] { color: #7ea67a; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid #4a504d; background: #0d1213; }
QCheckBox::indicator:hover { border-color: #9f824f; }
QCheckBox::indicator:checked { background: #b96836; border-color: #b96836; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #343b38; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #555b56; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }

/* ------------------------------------------------------------------
   Dialogs / transient windows
   Keep every application-owned popup inside the Arrakis visual system.
   The OS title bar may remain platform-controlled, but the dialog body,
   labels, controls and buttons must never fall back to Fusion white.
   ------------------------------------------------------------------ */
QDialog, QMessageBox {
    background: #0d1112;
    color: #eee9df;
}
QDialog QLabel, QMessageBox QLabel {
    color: #c9c4b9;
    background: transparent;
}
QDialog QPlainTextEdit, QDialog QTextEdit, QMessageBox QPlainTextEdit, QMessageBox QTextEdit {
    background: #090d0e;
    color: #c9c4b9;
    border: 1px solid #29302f;
    border-radius: 6px;
    selection-background-color: #3a2b1e;
    selection-color: #eee9df;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 10px;
}
QDialog QPushButton, QMessageBox QPushButton {
    background: #171d1e;
    border: 1px solid #303735;
    border-radius: 6px;
    color: #c9c0ae;
    padding: 8px 14px;
    min-width: 78px;
}
QDialog QPushButton:hover, QMessageBox QPushButton:hover {
    background: #1d2425;
    border-color: #4a4c45;
    color: #eee9df;
}
QDialog QPushButton:default, QDialog QPushButton:focus,
QMessageBox QPushButton:default, QMessageBox QPushButton:focus {
    border-color: #b96836;
}
QMessageBox QCheckBox, QDialog QCheckBox {
    color: #858a86;
}
QMessageBox QTextBrowser {
    background: #090d0e;
    color: #a9aaa3;
    border: 1px solid #29302f;
}

/* ------------------------------------------------------------------
   File dialogs
   Force the widget-based QFileDialog into the same obsidian/melange
   visual system as the main application.
   ------------------------------------------------------------------ */
QFileDialog {
    background: #0d1112;
    color: #eee9df;
    border: 1px solid #29302f;
}
QFileDialog QLabel {
    color: #858a86;
}
QFileDialog QLineEdit,
QFileDialog QComboBox {
    background: #0e1314;
    border: 1px solid #29302f;
    border-radius: 6px;
    color: #eee9df;
    selection-background-color: #3a2b1e;
    padding: 7px 9px;
}
QFileDialog QLineEdit:focus,
QFileDialog QComboBox:focus {
    border-color: #b96836;
}
QFileDialog QAbstractItemView {
    background: #0d1112;
    alternate-background-color: #111617;
    color: #c9c4b9;
    border: 1px solid #29302f;
    selection-background-color: #3a2b1e;
    selection-color: #eee9df;
    outline: none;
}
QFileDialog QAbstractItemView::item {
    padding: 6px 8px;
}
QFileDialog QAbstractItemView::item:hover {
    background: #171d1e;
}
QFileDialog QAbstractItemView::item:selected {
    background: #3a2b1e;
    color: #eee9df;
}
QFileDialog QHeaderView {
    background: #0d1112;
}
QFileDialog QHeaderView::section {
    background: #171d1e;
    color: #858a86;
    border: 0;
    border-right: 1px solid #29302f;
    border-bottom: 1px solid #29302f;
    padding: 7px 8px;
    font-size: 9px;
    font-weight: 700;
}
QFileDialog QPushButton {
    background: #171d1e;
    border: 1px solid #303735;
    border-radius: 6px;
    color: #c9c0ae;
    padding: 8px 14px;
    min-width: 72px;
}
QFileDialog QPushButton:hover {
    background: #1d2425;
    border-color: #4a4c45;
    color: #eee9df;
}
QFileDialog QPushButton:default,
QFileDialog QPushButton:enabled:focus {
    border-color: #b96836;
}
QFileDialog QToolButton {
    background: #111617;
    border: 1px solid transparent;
    border-radius: 5px;
    color: #c9c0ae;
    padding: 5px;
}
QFileDialog QToolButton:hover {
    background: #1d2425;
    border-color: #303735;
}
QFileDialog QSplitter::handle {
    background: #29302f;
}
QFileDialog QScrollBar:vertical {
    background: #0d1112;
    width: 8px;
}
QFileDialog QScrollBar::handle:vertical {
    background: #343b38;
    border-radius: 4px;
    min-height: 28px;
}
QFileDialog QScrollBar::add-line,
QFileDialog QScrollBar::sub-line {
    height: 0;
}

/* ------------------------------------------------------------------
   Data tables
   Keep item views inside the Arrakis/SpiceConex dark visual system.
   Qt's Fusion defaults otherwise paint QTableWidget with the platform
   palette, which produces the white blocks visible on Connectivity.
   ------------------------------------------------------------------ */
QTableWidget {
    background-color: #0d1112;
    alternate-background-color: #111617;
    color: #c9c4b9;
    border: 1px solid #29302f;
    border-radius: 5px;
    gridline-color: #202725;
    selection-background-color: #3a2b1e;
    selection-color: #eee9df;
    outline: none;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 9px;
}
QTableWidget::item {
    padding: 5px 7px;
    border: 0;
}
QTableWidget::item:hover {
    background-color: #171d1e;
}
QTableWidget::item:selected {
    background-color: #3a2b1e;
    color: #f0e8d8;
}
QHeaderView {
    background: #0d1112;
}
QHeaderView::section {
    background-color: #171d1e;
    color: #858a86;
    border: 0;
    border-right: 1px solid #29302f;
    border-bottom: 1px solid #343a37;
    padding: 6px 7px;
    min-height: 25px;
    font-size: 8px;
    font-weight: 700;
}
QHeaderView::section:first {
    border-left: 0;
}
QHeaderView::section:hover {
    background-color: #202625;
    color: #c7a56a;
}
QTableCornerButton::section {
    background-color: #171d1e;
    border: 0;
    border-bottom: 1px solid #343a37;
}
#tunnelInfo, #footerText { color: #858a86; font-size: 9px; }
#footerMark { color: #b96836; font-size: 8px; font-weight: 760; letter-spacing: 1.8px; }
#sidebarUpdateButton { background: #111617; color: #c7a56a; border: 1px solid #3b3027; border-radius: 4px; padding: 7px 9px; font-size: 8px; font-weight: 760; letter-spacing: 1.1px; }
#sidebarUpdateButton:hover { background: #1b2122; border-color: #b96836; color: #eee9df; }
#sidebarUpdateButton:disabled { color: #686d68; border-color: #252b2a; }
#outputBox { font-family: "Cascadia Mono", "Consolas", monospace; font-size: 10px; }
#logViewer { background: #0d1112; }
#logViewer #logOutput { background: #090d0e; border: 1px solid #29302f; border-radius: 6px; color: #c9c4b9; selection-background-color: #3a2b1e; }
#inventoryContext { color: #c9c0ae; font-size: 11px; font-weight: 650; }
#inventoryTabs { background: transparent; border: 0; }
#inventoryTabs::pane { background: rgba(15,20,21,0.90); border: 1px solid #29302f; border-top: 2px solid rgba(185,104,54,0.35); border-radius: 0 9px 9px 9px; }
#inventoryTabs QTabBar::tab { background: #111617; color: #777d79; border: 1px solid #29302f; border-bottom: 0; padding: 9px 18px; margin-right: 4px; min-width: 130px; font-size: 9px; font-weight: 760; letter-spacing: 1.2px; }
#inventoryTabs QTabBar::tab:selected { background: #171d1e; color: #d9b979; border-top: 2px solid #b96836; }
#inventoryTabs QTabBar::tab:hover { color: #eee9df; background: #1b2122; }
#filterTable { background: transparent; border: 0; }
#columnFilterScroll { background: transparent; border: 0; }
#columnFilterHost { background: transparent; }
#columnFilter { background: #0e1314; border: 1px solid #29302f; border-radius: 5px; color: #a9aaa3; padding: 5px 7px; font-size: 8px; }
#columnFilter:hover { border-color: #424946; }
#columnFilter:focus { border-color: #b96836; color: #eee9df; }
#columnFilterCombo { background: #0e1314; border: 1px solid #29302f; border-radius: 5px; color: #a9aaa3; padding: 5px 7px; font-size: 8px; }
#columnFilterCombo:hover { border-color: #424946; }
#columnFilterCombo:focus { border-color: #b96836; color: #eee9df; }
#columnFilterCombo::drop-down { border: 0; width: 22px; }
#columnFilterCombo::down-arrow { image: none; width: 5px; height: 5px; border-right: 1px solid #c7a56a; border-bottom: 1px solid #c7a56a; margin-right: 8px; }
#columnFilterCombo QAbstractItemView { background: #101516; border: 1px solid #4a3529; selection-background-color: #352217; selection-color: #d47a3f; padding: 5px; outline: 0; }
"""


class AwsIconProvider(QObject):
    """Lazy cache for AWS Architecture Icons generated from the AWS Labs icon set.

    The upstream icon set is based on the AWS-approved Architecture Icons. Icons are
    fetched only for services actually visible in the architecture projection and are
    cached locally so subsequent renders are offline-friendly.
    """

    changed = Signal()
    BASE_URL = "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/v23.1/dist"
    ICON_PATHS = {
        "ALB": "NetworkingContentDelivery/ElasticLoadBalancingApplicationLoadBalancer.png",
        "NLB": "NetworkingContentDelivery/ElasticLoadBalancingNetworkLoadBalancer.png",
        "EC2": "Compute/EC2.png",
        "Lambda": "Compute/Lambda.png",
        "ECS": "Containers/ElasticContainerService.png",
        "EKS": "Containers/ElasticKubernetesService.png",
        "RDS": "Database/RDS.png",
        "ElastiCache": "Database/ElastiCacheElastiCacheforRedis.png",
        "DynamoDB": "Database/DynamoDB.png",
        "EFS": "Storage/ElasticFileSystem.png",
        "FSx": "Storage/FSx.png",
        "S3": "Storage/SimpleStorageService.png",
        "Route53HostedZone": "NetworkingContentDelivery/Route53HostedZone.png",
        "CloudFront": "NetworkingContentDelivery/CloudFront.png",
        "GlobalAccelerator": "NetworkingContentDelivery/GlobalAccelerator.png",
        "TransitGateway": "NetworkingContentDelivery/TransitGateway.png",
        "VPCEndpoint": "NetworkingContentDelivery/VPCEndpoints.png",
        "InternetGateway": "NetworkingContentDelivery/VPCInternetGateway.png",
        "NATGateway": "NetworkingContentDelivery/VPCNATGateway.png",
        "VPC": "NetworkingContentDelivery/VirtualPrivateCloud.png",
        "WAF": "SecurityIdentityCompliance/WAF.png",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache_dir = Path(os.path.expanduser("~/.cache/ssm-spiceconex/aws-icons-v23.1"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manager = QNetworkAccessManager(self)
        self.pending = {}
        self.failed = set()
        self.manager.finished.connect(self._finished)

    def path(self, resource_type: str) -> Path | None:
        rel = self.ICON_PATHS.get(resource_type)
        if not rel:
            return None
        path = self.cache_dir / rel.replace("/", "__")
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    def request(self, resource_type: str):
        rel = self.ICON_PATHS.get(resource_type)
        if not rel or resource_type in self.failed or resource_type in self.pending:
            return
        path = self.cache_dir / rel.replace("/", "__")
        if path.exists() and path.stat().st_size > 0:
            return
        self.pending[resource_type] = path
        url = QUrl(f"{self.BASE_URL}/{rel}")
        request = QNetworkRequest(url)
        request.setRawHeader(b"User-Agent", b"SSM-SpiceConex/1.0")
        reply = self.manager.get(request)
        reply.setProperty("resource_type", resource_type)

    def _finished(self, reply):
        resource_type = reply.property("resource_type")
        path = self.pending.pop(resource_type, None)
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError and path:
                data = bytes(reply.readAll())
                if data:
                    path.write_bytes(data)
                    self.changed.emit()
                    return
            if resource_type:
                self.failed.add(resource_type)
        finally:
            reply.deleteLater()


class ArchitectureMap(QGraphicsView):
    """High-level architecture projection of the deterministic infrastructure graph."""

    ARCHITECTURE_TYPES = {
        "VPC", "ALB", "NLB", "Listener", "TargetGroup", "EC2", "AutoScalingGroup", "ECS", "EKS", "Lambda",
        "RDS", "ElastiCache", "DynamoDB", "EFS", "FSx", "S3", "Route53HostedZone", "CloudFront",
        "GlobalAccelerator", "WAF", "TransitGateway", "VPCPeering", "VPCEndpoint", "InternetGateway", "NATGateway",
    }
    HIDDEN_ARCH_TYPES = {"NetworkInterface", "SecurityGroup", "RouteTable", "RouteTarget", "Subnet", "EBS", "ACMCertificate"}
    LANE_ORDER = ("EDGE", "NETWORK", "TRAFFIC", "COMPUTE", "DATA & STORAGE")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("architectureMap")
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(9, 13, 14))
        self.setMinimumHeight(430)
        self.icons = AwsIconProvider(self)
        self.icons.changed.connect(self._icons_changed)
        self._last_render = None

    def wheelEvent(self, event):
        factor = 1.16 if event.angleDelta().y() > 0 else 1 / 1.16
        self.scale(factor, factor)

    def clear_map(self):
        self.scene.clear()
        self.resetTransform()

    def export_png(self, parent=None):
        """Export the currently rendered map as a standalone PNG image."""
        rect = self.scene.sceneRect()
        if rect.isNull() or rect.width() <= 1 or rect.height() <= 1:
            QMessageBox.information(parent or self, "Architecture export", "There is no architecture map to export yet.")
            return False

        application = (self._last_render[1] if self._last_render else None) or "all-products"
        detail = (self._last_render[3] if self._last_render else "architecture").lower().replace(" ", "-")
        safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(application).lower()).strip("-") or "all-products"
        default_path = str(Path.home() / f"spiceconex-{detail}-{safe_name}.png")

        dialog = QFileDialog(parent or self, "Export architecture PNG", default_path, "PNG images (*.png)")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        path = dialog.selectedFiles()[0]
        if not path.lower().endswith(".png"):
            path += ".png"

        # Render above the viewport resolution so exported diagrams remain useful
        # in documentation, tickets and DR reviews. Cap the largest dimension to
        # avoid pathological images when the full resource graph is very large.
        margin = 24.0
        source = rect.adjusted(-margin, -margin, margin, margin)
        scale = 2.0
        max_dimension = 6000
        scale = min(scale, max_dimension / max(source.width(), source.height()))
        scale = max(scale, 0.25)
        width = max(1, int(round(source.width() * scale)))
        height = max(1, int(round(source.height() * scale)))

        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(9, 13, 14))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        target = image.rect().toRectF()
        self.scene.render(painter, target, source, Qt.AspectRatioMode.KeepAspectRatio)
        painter.end()

        if not image.save(path, "PNG"):
            QMessageBox.warning(parent or self, "Architecture export", f"Could not save the PNG file:\n{path}")
            return False
        return True

    def _icons_changed(self):
        if self._last_render:
            graph, application, tag_key, detail = self._last_render
            self.render_graph(graph, application, tag_key, detail)

    @staticmethod
    def _lane(resource_type: str) -> str:
        t = resource_type.lower()
        if t in {"route53hostedzone", "cloudfront", "globalaccelerator", "waf"}:
            return "EDGE"
        if t in {"vpc", "transitgateway", "vpcpeering", "vpcendpoint", "internetgateway", "natgateway"}:
            return "NETWORK"
        if t in {"alb", "nlb", "listener", "targetgroup"}:
            return "TRAFFIC"
        if t in {"ec2", "autoscalinggroup", "ecs", "eks", "lambda"}:
            return "COMPUTE"
        return "DATA & STORAGE"

    @staticmethod
    def _tag_value(node: ResourceNode, tag_key: str) -> str:
        return node.tags.get(tag_key, "").strip() or "SHARED / UNTAGGED"

    def _build_projection(self, graph: InfrastructureGraph, tag_key: str):
        """Collapse discovered resources into the AWS service concepts in use.

        This is intentionally not an inventory layout: an EC2 fleet becomes one
        EC2 concept, for example, and parallel AWS API relationships become one
        labelled path between service concepts.
        """
        groups = {}
        member_to_group = {}
        for node in graph.nodes.values():
            if node.resource_type in self.HIDDEN_ARCH_TYPES or node.resource_type not in self.ARCHITECTURE_TYPES:
                continue
            gid = "service::" + node.resource_type
            group = groups.setdefault(gid, {
                "id": gid, "resource_type": node.resource_type, "name": node.resource_type,
                "region": node.region, "members": [], "tag": self._tag_value(node, tag_key), "azs": set()
            })
            group["members"].append(node)
            if node.metadata.get("AvailabilityZone"):
                group["azs"].add(node.metadata["AvailabilityZone"])
            member_to_group[node.id] = gid

        relation_counts = defaultdict(int)
        for edge in graph.edges:
            source = member_to_group.get(edge.source); target = member_to_group.get(edge.target)
            if not source or not target or source == target:
                continue
            relation_counts[(source, target)] += 1
        projected_edges = [{"source": s, "target": t, "count": c} for (s, t), c in relation_counts.items()]
        return groups, projected_edges

    @staticmethod
    def _group_title(group):
        return group["resource_type"]

    @staticmethod
    def _group_subtitle(group):
        regions = sorted({m.region for m in group["members"] if m.region})
        return f"{len(group['members'])} resource{'s' if len(group['members']) != 1 else ''} · {', '.join(regions) or 'global'}"

    def render_graph(self, graph: InfrastructureGraph, application: str | None = None,
                     tag_key: str = DEFAULT_TAG_KEY, detail: str = "ARCHITECTURE"):
        self._last_render = (graph, application, tag_key, detail)
        self.clear_map()
        if not graph.nodes:
            return
        if detail.upper().startswith("FULL"):
            # Keep full graph as the forensic view; architecture view is the human-oriented map.
            nodes = list(graph.nodes.values())
            visible_ids = {n.id for n in nodes}
            self._render_full(graph, nodes, [e for e in graph.edges if e.source in visible_ids and e.target in visible_ids], tag_key, application)
            return
        groups, projected_edges = self._build_projection(graph, tag_key)
        self._render_architecture(graph, groups, projected_edges, tag_key, application)

    def _add_icon(self, resource_type, x, y, size=52):
        path = self.icons.path(resource_type)
        if not path:
            self.icons.request(resource_type)
            return None
        pix = QPixmap(str(path))
        if pix.isNull():
            return None
        pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        item = self.scene.addPixmap(pix)
        item.setPos(x, y)
        return item

    def _card(self, group, x, y, w=270, h=102, tag_key=""):
        tagged = group["tag"] != "SHARED / UNTAGGED"
        fill = QColor(31, 30, 24, 248) if tagged else QColor(20, 26, 27, 248)
        border = QColor(211, 145, 77, 235) if tagged else QColor(78, 88, 86, 210)
        card = QGraphicsRectItem(x, y, w, h); card.setBrush(fill); card.setPen(border); card.setToolTip(self._group_tooltip(group, tag_key)); self.scene.addItem(card)
        icon = self._add_icon(group["resource_type"], x + 13, y + 28, 58)
        if icon:
            icon.setZValue(3)
        tx = x + 82
        type_text = self.scene.addText(group["resource_type"].upper()); type_text.setDefaultTextColor(QColor(SPICE if tagged else SAND)); type_text.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold)); type_text.setPos(tx, y + 10); type_text.setZValue(2)
        title = self.scene.addText(f"{len(group['members'])} discovered resource{'s' if len(group['members']) != 1 else ''}"); title.setDefaultTextColor(QColor(TEXT)); title.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold)); title.setPos(tx, y + 30); title.setZValue(2)
        subtitle = self.scene.addText(self._group_subtitle(group)); subtitle.setDefaultTextColor(QColor(MUTED)); subtitle.setFont(QFont("Segoe UI", 7)); subtitle.setPos(tx, y + 52); subtitle.setZValue(2)
        foot = self.scene.addText("Hover for discovered members"); foot.setDefaultTextColor(QColor(SPICE if tagged else SAND)); foot.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold)); foot.setPos(tx, y + 77); foot.setZValue(2)
        return card

    @staticmethod
    def _group_tooltip(group, tag_key):
        lines = [group["resource_type"], ArchitectureMap._group_title(group), ArchitectureMap._group_subtitle(group), group["tag"]]
        if len(group["members"]) > 1:
            lines.append(f"Members: {len(group['members'])}")
        for member in group["members"][:25]:
            lines.append(f"• {member.name or member.id}")
        if len(group["members"]) > 25:
            lines.append(f"… +{len(group['members']) - 25} more")
        return "\n".join(lines)

    def _render_conceptual(self, graph, groups, projected_edges, tag_key):
        """Draw the selected architecture as service concepts in layered lanes."""
        node_w, node_h, lane_w, gap_y = 220, 112, 272, 28
        lanes = defaultdict(list)
        for group in groups.values():
            lanes[self._lane(group["resource_type"])].append(group)
        max_rows = max((len(lanes[lane]) for lane in self.LANE_ORDER), default=1)
        diagram_h = max(430, 128 + max_rows * (node_h + gap_y))
        positions = {}
        for index, lane in enumerate(self.LANE_ORDER):
            x = 46 + index * lane_w
            band = self.scene.addRect(x - 18, 54, node_w + 36, diagram_h - 72)
            band.setBrush(QColor(16, 22, 23, 190)); band.setPen(QPen(QColor(62, 72, 69, 190), 1)); band.setZValue(-20)
            title = self.scene.addText(lane)
            title.setDefaultTextColor(QColor(SPICE if lane in {"EDGE", "TRAFFIC"} else SAND))
            title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold)); title.setPos(x, 68)
            for row, group in enumerate(sorted(lanes[lane], key=lambda item: item["resource_type"])):
                positions[group["id"]] = (x, 108 + row * (node_h + gap_y))
        for rel in projected_edges:
            if rel["source"] not in positions or rel["target"] not in positions:
                continue
            x1, y1 = positions[rel["source"]]; x2, y2 = positions[rel["target"]]
            if x1 <= x2: sx, sy, tx, ty = x1 + node_w, y1 + node_h / 2, x2, y2 + node_h / 2
            else: sx, sy, tx, ty = x1, y1 + node_h / 2, x2 + node_w, y2 + node_h / 2
            bend = (sx + tx) / 2
            path = QPainterPath(QPointF(sx, sy)); path.cubicTo(bend, sy, bend, ty, tx, ty)
            line = QGraphicsPathItem(path); line.setPen(QPen(QColor(199, 165, 106, 185), 2.1)); line.setZValue(-2)
            line.setToolTip(f"Observed relationships: {rel['count']}"); self.scene.addItem(line)
        for group in groups.values():
            x, y = positions[group["id"]]
            card = self._card(group, x, y, w=node_w, h=node_h, tag_key=tag_key); card.setZValue(1)
        legend = self.scene.addText(f"CONCEPT MAP  |  {len(groups)} AWS service types  |  {len(projected_edges)} service relationships  |  {len(graph.nodes)} discovered resources consolidated")
        legend.setDefaultTextColor(QColor(SAND)); legend.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold)); legend.setPos(24, 16)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-30, -28, 40, 35))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _render_architecture(self, graph, groups, projected_edges, tag_key, application):
        """Region/VPC topology, with AZ-aware compute grouping and direct API edges."""
        self._render_conceptual(graph, groups, projected_edges, tag_key)
        return
        from collections import Counter
        from math import ceil

        # The VPC/AZ placement uses only metadata actually returned by discovery.
        def location(group):
            members = group["members"]
            regions = {n.region or "global" for n in members}
            region = next(iter(regions)) if len(regions) == 1 else "MULTI-REGION"
            vpcs = set()
            for n in members:
                md = n.metadata
                vpc = md.get("VpcId") or md.get("vpc_id") or md.get("VPCId")
                if vpc:
                    vpcs.add(str(vpc))
            vpc = next(iter(vpcs)) if len(vpcs) == 1 else ("MULTIPLE VPCS" if vpcs else "GLOBAL / UNRESOLVED VPC")
            return region, vpc

        buckets = defaultdict(list)
        for group in groups.values():
            buckets[location(group)].append(group)

        positions = {}
        boxes = []
        region_buckets = defaultdict(list)
        for (region, vpc), items in buckets.items():
            region_buckets[region].append((vpc, items))

        cursor_y = 88
        node_w, node_h = 270, 102
        col_gap, row_gap = 54, 35
        region_width = 4 * (node_w + col_gap) + 80
        for region in sorted(region_buckets):
            region_y = cursor_y
            cursor_y += 64
            for vpc, items in sorted(region_buckets[region], key=lambda item: item[0]):
                start_y = cursor_y
                # Keep network/data-flow layers while separating actual network boundaries.
                lanes = defaultdict(list)
                for group in items:
                    lanes[self._lane(group["resource_type"])].append(group)
                max_rows = max((len(values) for values in lanes.values()), default=1)
                for col, lane in enumerate(self.LANE_ORDER):
                    for row, group in enumerate(sorted(lanes[lane], key=lambda g: (g["resource_type"], g["name"]))):
                        x = 68 + col * (node_w + col_gap)
                        y = start_y + 64 + row * (node_h + row_gap)
                        positions[group["id"]] = (x, y)
                box_height = 88 + max_rows * (node_h + row_gap)
                boxes.append(("vpc", 36, start_y, region_width, box_height, vpc))
                cursor_y += box_height + 26
            boxes.append(("region", 12, region_y, region_width + 48, cursor_y - region_y - 8, region))
            cursor_y += 34

        # Nested boundaries are intentionally behind links and resource symbols.
        for kind in ("region", "vpc"):
            for box_kind, x, y, w, h, title in boxes:
                if kind != box_kind:
                    continue
                outer = self.scene.addRect(x, y, w, h)
                outer.setBrush(QColor(18, 24, 25, 130) if kind == "region" else QColor(27, 30, 28, 160))
                outer.setPen(QPen(QColor(197, 140, 77, 175) if kind == "region" else QColor(87, 104, 100, 165), 1.5))
                outer.setZValue(-20 if kind == "region" else -10)
                label = self.scene.addText(("AWS REGION  /  " if kind == "region" else "NETWORK SCOPE  /  ") + title)
                label.setDefaultTextColor(QColor(SPICE if kind == "region" else SAND))
                label.setFont(QFont("Segoe UI", 10 if kind == "region" else 8, QFont.Weight.Bold))
                label.setPos(x + 16, y + 9)

        # Show only observed AWS API relationships. Do not imply dependencies
        # from colocation in a VPC or AZ.
        for rel in projected_edges:
            if rel["source"] not in positions or rel["target"] not in positions:
                continue
            x1, y1 = positions[rel["source"]]
            x2, y2 = positions[rel["target"]]
            if x1 <= x2:
                sx, sy, tx, ty = x1 + node_w, y1 + node_h / 2, x2, y2 + node_h / 2
            else:
                sx, sy, tx, ty = x1, y1 + node_h / 2, x2 + node_w, y2 + node_h / 2
            mid = (sx + tx) / 2
            path = QPainterPath(QPointF(sx, sy))
            path.cubicTo(mid, sy, mid, ty, tx, ty)
            pen = QPen(QColor(189, 143, 89, 185), 1.8)
            line = QGraphicsPathItem(path)
            line.setPen(pen)
            line.setZValue(-2)
            line.setToolTip(f"AWS relation: {rel['relation']}\nObserved links: {rel['count']}")
            self.scene.addItem(line)

        for group in groups.values():
            if group["id"] not in positions:
                continue
            x, y = positions[group["id"]]
            card = self._card(group, x, y, tag_key=tag_key)
            card.setZValue(1)

        legend = self.scene.addText(
            f"AWS TOPOLOGY  |  {len(region_buckets)} regions  |  {len(groups)} components  |  "
            f"{len(projected_edges)} observed relations  |  VPC/AZ shown only when discovery supplies metadata"
        )
        legend.setDefaultTextColor(QColor(SAND))
        legend.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        legend.setPos(18, 10)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-30, -30, 40, 40))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _render_full(self, graph, nodes, edges, tag_key, application):
        # Full graph intentionally retains the existing forensic layout.
        lane_x = {"EDGE": 0, "LOAD BALANCING": 290, "COMPUTE": 580, "DATA": 870, "NETWORK": 290, "SECURITY": 580, "OTHER": 870}
        positions = {}; lane_rows = defaultdict(int)
        ordered = sorted(nodes, key=lambda n: (self._lane(n.resource_type), n.resource_type, n.region, n.name or n.id))
        for node in ordered:
            lane = self._lane(node.resource_type); row = lane_rows[lane]; lane_rows[lane] += 1
            positions[node.id] = (lane_x.get(lane, 870), (45 if lane in {"EDGE", "LOAD BALANCING", "COMPUTE", "DATA"} else 430) + row * 88)
        for e in edges:
            if e.source not in positions or e.target not in positions: continue
            x1, y1 = positions[e.source]; x2, y2 = positions[e.target]; w, h = 235, 82
            line = QGraphicsLineItem(x1+w, y1+h/2, x2, y2+h/2); line.setPen(QPen(QColor(118,89,65,145), 1.0)); line.setToolTip(f"{e.relation} · {e.confidence}"); self.scene.addItem(line)
        for node in nodes:
            x,y=positions[node.id]; w,h=235,82
            item=QGraphicsRectItem(x,y,w,h); item.setBrush(QColor(18,23,24,248)); item.setPen(QPen(QColor(57,66,64,225))); item.setToolTip(self._tooltip(node)); self.scene.addItem(item)
            type_text=self.scene.addText(node.resource_type.upper()); type_text.setDefaultTextColor(QColor(SPICE if node.tags.get(tag_key," ").strip() else SAND)); type_text.setFont(QFont("Segoe UI",6,QFont.Weight.Bold)); type_text.setPos(x+10,y+7)
            title=self.scene.addText((node.name or node.id)[:42]); title.setDefaultTextColor(QColor(TEXT)); title.setFont(QFont("Segoe UI",8,QFont.Weight.DemiBold)); title.setPos(x+10,y+23)
            subtitle=self.scene.addText(node.region); subtitle.setDefaultTextColor(QColor(MUTED)); subtitle.setFont(QFont("Segoe UI",7)); subtitle.setPos(x+10,y+46)
            footer=self.scene.addText((node.tags.get(tag_key," ").strip() or "SHARED / UNTAGGED")[:48]); footer.setDefaultTextColor(QColor(SPICE if node.tags.get(tag_key," ").strip() else SAND)); footer.setFont(QFont("Segoe UI",7,QFont.Weight.DemiBold)); footer.setPos(x+10,y+62)
        legend=self.scene.addText(f"FULL RESOURCE GRAPH · {len(nodes)} resources · {len(edges)} relationships · Resource Inventory is the authoritative inventory")
        legend.setDefaultTextColor(QColor(92,99,95)); legend.setFont(QFont("Segoe UI",7)); legend.setPos(-145,-12)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-170,-45,60,60)); self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    @staticmethod
    def _tooltip(node: ResourceNode) -> str:
        lines = [node.resource_type, node.name or node.id]
        if node.arn: lines.append(node.arn)
        for key, value in sorted(node.tags.items()): lines.append(f"{key}={value}")
        for key, value in sorted(node.metadata.items()):
            if value not in (None, "", [], {}): lines.append(f"{key}={value}")
        return "\n".join(lines)


class ArchitecturePage(BasePage):
    RESOURCE_COLUMNS = ["Type", "Name", "Region", "Application", "ARN", "Details"]

    def __init__(self, source, demo=False):
        super().__init__()
        self.source = source
        self.demo = demo
        self.graph: InfrastructureGraph | None = None
        self.thread = None
        root = self.page_header(
            "INFRASTRUCTURE DISCOVERY / ARCHITECTURE",
            "Architecture",
            "Discover AWS resources, map deterministic relationships and scope the topology by application tags.",
        )

        controls = QFrame(); controls.setObjectName("operationPanel")
        form = QGridLayout(controls); form.setContentsMargins(18, 16, 18, 16); form.setHorizontalSpacing(12); form.setVerticalSpacing(8)
        tag_label = QLabel("SCOPE TAG KEY"); tag_label.setObjectName("microLabel")
        self.tag_key = QComboBox(); self.tag_key.setEditable(True); self.tag_key.setMinimumHeight(38)
        self.tag_key.addItems(SUPPORTED_TAG_KEYS)
        self.tag_key.setCurrentText(DEFAULT_TAG_KEY)
        self.tag_key.lineEdit().setPlaceholderText("AWS tag key")
        scope_label = QLabel("DISCOVERY SCOPE"); scope_label.setObjectName("microLabel")
        self.scope = QComboBox(); self.scope.addItems(["CURRENT REGION", "ALL REGIONS"]); self.scope.setMinimumHeight(38)
        app_label = QLabel("APPLICATION / COMPONENT"); app_label.setObjectName("microLabel")
        self.application = QComboBox(); self.application.setEditable(False); self.application.addItem("ALL PRODUCTS"); self.application.setMinimumHeight(38)
        detail_label = QLabel("MAP DETAIL"); detail_label.setObjectName("microLabel")
        self.map_detail = QComboBox(); self.map_detail.addItems(["CONCEPT MAP"]); self.map_detail.setMinimumHeight(38)
        self.discover = QPushButton("DISCOVER INFRASTRUCTURE  →"); self.discover.setObjectName("primaryButton"); self.discover.setMinimumHeight(38)
        self.refresh_view = QPushButton("REFRESH VIEW  ↻"); self.refresh_view.setObjectName("quietButton"); self.refresh_view.setMinimumHeight(38); self.refresh_view.setEnabled(False)
        self.export_csv = QPushButton("EXPORT CSV"); self.export_csv.setObjectName("quietButton"); self.export_csv.setMinimumHeight(38); self.export_csv.setEnabled(False)
        self.clear = QPushButton("CLEAR"); self.clear.setObjectName("quietButton"); self.clear.setMinimumHeight(38)
        form.addWidget(tag_label, 0, 0); form.addWidget(self.tag_key, 1, 0)
        form.addWidget(scope_label, 0, 1); form.addWidget(self.scope, 1, 1)
        form.addWidget(app_label, 0, 2); form.addWidget(self.application, 1, 2)
        form.addWidget(detail_label, 0, 3); form.addWidget(self.map_detail, 1, 3)
        buttons = QHBoxLayout(); buttons.setSpacing(6)
        buttons.addWidget(self.discover); buttons.addWidget(self.refresh_view); buttons.addWidget(self.export_csv); buttons.addWidget(self.clear)
        form.addLayout(buttons, 1, 4)
        root.addWidget(controls)

        summary = QHBoxLayout(); summary.setSpacing(8)
        self.context = QLabel("No infrastructure discovered"); self.context.setObjectName("inventoryContext"); summary.addWidget(self.context); summary.addStretch()
        self.count = QLabel("0 RESOURCES"); self.count.setObjectName("sectionCounter"); summary.addWidget(self.count)
        root.addLayout(summary)

        self.inventory = FilterableTable(self.RESOURCE_COLUMNS, filter_options={"Type": [], "Region": [], "Application": []})
        map_page = QWidget(); map_layout = QVBoxLayout(map_page); map_layout.setContentsMargins(0, 0, 0, 0); map_layout.setSpacing(8)
        map_toolbar = QHBoxLayout(); map_toolbar.setSpacing(8)
        map_hint = QLabel("AWS service concept map · layers represent the runtime path · hover a service or path for discovered members and relationships")
        map_hint.setObjectName("footerText"); map_toolbar.addWidget(map_hint); map_toolbar.addStretch()
        self.export_png = QPushButton("EXPORT PNG"); self.export_png.setObjectName("quietButton"); self.export_png.setMinimumHeight(34); self.export_png.setEnabled(False)
        map_toolbar.addWidget(self.export_png)
        map_layout.addLayout(map_toolbar)
        self.map = ArchitectureMap(); map_layout.addWidget(self.map, 1)
        root.addWidget(map_page, 1)

        self.status_detail = QLabel("Discovery has not been run."); self.status_detail.setObjectName("footerText"); self.status_detail.setWordWrap(True); root.addWidget(self.status_detail)
        self.discover.clicked.connect(self.run_discovery)
        self.refresh_view.clicked.connect(self.refresh_view_only)
        self.export_csv.clicked.connect(lambda: _export_filtered_table(self, self.inventory, "Export resource inventory", "resources.csv"))
        self.export_png.clicked.connect(lambda: self.map.export_png(self))
        self.application.currentIndexChanged.connect(self.apply_scope)
        self.map_detail.currentIndexChanged.connect(self.apply_scope)
        self.tag_key.currentTextChanged.connect(self._tag_key_changed)
        self.clear.clicked.connect(self.clear_view)
        if demo:
            self.graph = self._demo_graph(); self._populate(self.graph)

    def run_discovery(self):
        profile = self.source.commands.profile.currentText().strip()
        region = self.source.commands.region.currentText().strip() or DEFAULT_REGION
        tag_key = self.tag_key.currentText().strip() or DEFAULT_TAG_KEY
        scope = self.scope.currentText()
        logger.info(
            "Architecture discovery requested | profile=%r | region=%s | scope=%s | tag=%s | demo=%s",
            profile, region, scope, tag_key, self.demo,
        )
        if not profile:
            show_dialog(self, QMessageBox.Icon.Warning, "AWS profile", "No AWS profile is selected.", "Select an AWS profile in Terminal before running Architecture Discovery.")
            return
        self.discover.setEnabled(False)
        self.status.setText("●  DISCOVERING INFRASTRUCTURE")
        self.status_detail.setText(f"Querying AWS · {profile} · {region} · {scope} · tag {tag_key}")
        QApplication.processEvents()
        if self.demo:
            self._populate(self._demo_graph()); self.discover.setEnabled(True); return
        self.thread = QThread()
        self.worker = Worker(lambda: _run_with_sso(profile, lambda: discover_infrastructure(profile, region, scope, tag_key)))
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._discovery_finished)
        self.worker.failed.connect(self._discovery_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)
        self.thread.start()

    @Slot(object)
    def _discovery_finished(self, graph):
        self.graph = graph
        self._populate(graph)
        self.discover.setEnabled(True)
        self.refresh_view.setEnabled(bool(graph.nodes))
        self.export_csv.setEnabled(bool(graph.nodes))
        self.export_png.setEnabled(bool(graph.nodes))
        if graph.nodes:
            self.status.setText("●  DISCOVERY COMPLETE")
            logger.info("Architecture discovery completed | resources=%d | relationships=%d | warnings=%d", len(graph.nodes), len(graph.edges), len(graph.warnings))
        else:
            self.status.setText("◆  DISCOVERY RETURNED NO RESOURCES")
            detail = " · ".join(graph.warnings[:4]) if graph.warnings else "AWS returned no discoverable resources for the selected scope."
            self.status_detail.setText(detail)
            logger.warning("Architecture discovery returned no resources | warnings=%s", graph.warnings)
            show_dialog(
                self, QMessageBox.Icon.Warning, "Architecture discovery",
                "Discovery completed but returned no resources.",
                detail,
            )

    @Slot(object)
    def _discovery_failed(self, exc):
        self.discover.setEnabled(True); self.status.setText("◆  DISCOVERY ERROR")
        if not isinstance(exc, Exception): exc = RuntimeError(str(exc))
        self.status_detail.setText(f"Discovery failed: {exc}")
        logger.exception("Architecture discovery failed | %s: %s", type(exc).__name__, exc)
        show_exception(self, "Architecture discovery failed", "discovering AWS infrastructure", exc)

    def _cleanup(self):
        if self.thread:
            self.thread.deleteLater(); self.thread = None
        self.worker = None

    def _populate(self, graph):
        tag_key = self.tag_key.currentText().strip() or DEFAULT_TAG_KEY
        self.export_png.setEnabled(bool(graph.nodes))
        self.application.blockSignals(True); self.application.clear(); self.application.addItem("ALL PRODUCTS")
        for value in graph.applications(tag_key): self.application.addItem(value)
        self.application.blockSignals(False)
        self._render_scope(graph, tag_key)
        warning_text = ""
        if graph.warnings:
            warning_text = " · ".join(graph.warnings[:3])
            if len(graph.warnings) > 3: warning_text += f" · +{len(graph.warnings)-3} more"
        self.status_detail.setText(f"Regions: {', '.join(graph.regions) or '—'} · Tagged applications: {len(graph.applications(tag_key))} · {warning_text or 'No discovery warnings.'}")

    def _tag_key_changed(self, _value):
        if self.graph:
            self._populate(self.graph)

    def refresh_view_only(self):
        """Re-render the current scoped view without performing a new AWS discovery."""
        if not self.graph:
            return
        self._render_scope(self.graph, self.tag_key.currentText().strip() or DEFAULT_TAG_KEY)
        self.status.setText("●  VIEW REFRESHED")
        self.status_detail.setText("Re-rendered from the existing discovery graph · no AWS discovery performed.")

    def apply_scope(self):
        if self.graph:
            self._render_scope(self.graph, self.tag_key.currentText().strip() or DEFAULT_TAG_KEY)

    def _render_scope(self, graph, tag_key):
        application = self.application.currentText() or "ALL PRODUCTS"
        scoped = graph.scoped(application, tag_key)
        rows = []
        for node in sorted(scoped.nodes.values(), key=lambda n: (n.region, n.resource_type, n.name or n.id)):
            details = "; ".join(f"{k}={v}" for k, v in list(node.metadata.items())[:4])
            rows.append({"Type": node.resource_type, "Name": node.name or node.id, "Region": node.region, "Application": node.tags.get(tag_key, "") or "SHARED", "ARN": node.arn or "", "Details": details})
        self.inventory.set_rows(rows)
        self.count.setText(f"{len(scoped.nodes)} / {len(graph.nodes)} RESOURCES")
        self.context.setText(f"{graph.account_name} · {graph.account_id} · {application} · {len(scoped.edges)} relationships")
        self.map.render_graph(scoped, application, tag_key, detail=self.map_detail.currentText())

    def clear_view(self):
        self.graph = None; self.inventory.set_rows([]); self.inventory.clear_filters(); self.application.clear(); self.application.addItem("ALL PRODUCTS"); self.tag_key.setCurrentText(DEFAULT_TAG_KEY); self.map_detail.setCurrentText("CONCEPT MAP"); self.count.setText("0 RESOURCES"); self.context.setText("No infrastructure discovered"); self.status_detail.setText("Discovery has not been run."); self.map.clear_map(); self.refresh_view.setEnabled(False); self.export_csv.setEnabled(False); self.export_png.setEnabled(False); self.status.setText("◆  READY")

    @staticmethod
    def _demo_graph():
        from services.discovery.models import InfrastructureGraph, ResourceNode, ResourceEdge
        graph = InfrastructureGraph("000000000000", "demo-account", ["eu-west-1"])
        def n(i, t, name, app="", region="eu-west-1"):
            return ResourceNode(i, i if i.startswith("arn:") else None, t, name, region, "000000000000", ({DEFAULT_TAG_KEY: app} if app else {}), {})
        for item in [
            n("vpc-01", "VPC", "vpc-prod"), n("subnet-public", "Subnet", "public-a", "payments"),
            n("alb-01", "ALB", "payments-alb", "payments"), n("listener-01", "Listener", "HTTPS:443", "payments"),
            n("tg-01", "TargetGroup", "payments-tg", "payments"), n("i-01", "EC2", "payments-01", "payments"),
            n("rds-01", "RDS", "payments-db", "payments"), n("tgw-01", "TransitGateway", "shared-tgw"),
        ]: graph.add_node(item)
        for e in [("vpc-01","subnet-public","contains"),("subnet-public","alb-01","contains"),("alb-01","listener-01","contains"),("listener-01","tg-01","targets"),("tg-01","i-01","targets"),("i-01","rds-01","depends_on"),("i-01","tgw-01","routes_to")]: graph.add_edge(ResourceEdge(*e))
        return graph


class LogViewer(QDialog):
    """Read-only application log viewer using the application theme."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SSM-SpiceConex · Application logs")
        self.setModal(True)
        self.resize(1040, 680)
        self.setObjectName("logViewer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        title = QLabel("APPLICATION LOGS")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        editor = QPlainTextEdit()
        editor.setObjectName("logOutput")
        editor.setReadOnly(True)
        editor.setMinimumHeight(500)
        try:
            content = log_path().read_text(encoding="utf-8") if log_path().exists() else "No log entries yet."
        except OSError as exc:
            content = f"Unable to read log file: {exc}"
        editor.setPlainText(content)
        layout.addWidget(editor, 1)

        path_label = QLabel(
            f"Log file: {log_path()}\nLog directory: {log_directory()}"
        )
        path_label.setObjectName("footerText")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        open_button = QPushButton("OPEN LOG FOLDER")
        open_button.setObjectName("quietButton")
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(log_directory().as_uri()))
        buttons.addWidget(open_button)
        close_button = QPushButton("CLOSE")
        close_button.setObjectName("quietButton")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)


class MainWindow(QMainWindow):
    def __init__(self, demo=False):
        super().__init__(); logger.info("Application window created | demo=%s", demo); self.setWindowTitle("SSM-SpiceConex · Arrakis Operations"); self.resize(1400,860); self.setMinimumSize(1120,720)
        container=QWidget(); layout=QHBoxLayout(container); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0); self.setCentralWidget(container)
        self.sidebar=Sidebar(); layout.addWidget(self.sidebar); self.stack=QStackedWidget(); layout.addWidget(self.stack,1)
        self.power=PowerCon(self.sidebar,demo); self.tunnel=Tunneling(self.power); self.command=CommandPage(self.power); self.performance=PerformancePage(self.power); self.connectivity=ConnectivityPage(self.power); self.certificates=CertificatesPage(self.power); self.architecture=ArchitecturePage(self.power,demo)
        for page in (self.power,self.tunnel,self.command,self.performance,self.connectivity,self.certificates,self.architecture): self.stack.addWidget(page)
        self.sidebar.pageRequested.connect(self.stack.setCurrentIndex)
        logs_action = QAction(self); logs_action.setShortcut(QKeySequence("Ctrl+L")); logs_action.triggered.connect(lambda: LogViewer(self).exec()); self.addAction(logs_action)
        self.sidebar.update_button.clicked.connect(self.check_updates)
        self.update_thread = None
        self.update_worker = None

    def check_updates(self):
        if self.update_thread is not None:
            return
        self.sidebar.update_button.setEnabled(False)
        self.sidebar.update_button.setText("CHECKING…")
        self.update_thread = QThread()
        self.update_worker = Worker(lambda: check_for_update(ROOT.parent))
        self.update_worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(self.update_worker.run)
        self.update_worker.finished.connect(self._update_check_finished)
        self.update_worker.failed.connect(self._update_check_failed)
        self.update_worker.finished.connect(self.update_thread.quit)
        self.update_worker.failed.connect(self.update_thread.quit)
        self.update_thread.finished.connect(self._update_check_cleanup)
        self.update_thread.start()

    @Slot(object)
    def _update_check_finished(self, info):
        try:
            if not info.get("available"):
                if info.get("available_version") is None:
                    show_dialog(self, QMessageBox.Icon.Information, "No release published yet", f"Installed development build: {info['installed_version']}", "Update checking is working. Publish a semantic-version Git tag (for example v0.5.0) on the update branch to enable automatic upgrades.")
                    return
                detail = info.get("reason", "")
                if info.get("available_version"):
                    detail = f"Installed: {info['installed_version']}\nLatest published: {info['available_version']}\n{detail}"
                show_dialog(self, QMessageBox.Icon.Information, "SpiceConex update status", detail or f"Version {APP_VERSION} is up to date.")
                return
            if not info.get("can_apply"):
                show_dialog(self, QMessageBox.Icon.Warning, "Update available but blocked", f"Installed: {info['installed_version']}\nAvailable: {info['available_version']}", info.get("reason", ""))
                return
            answer = QMessageBox.question(
                self, "SpiceConex update available",
                f"A newer published version is available.\n\nInstalled: {info['installed_version']}\nAvailable: {info['available_version']} ({info.get('tag', '')})\n\nUpdate now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            launch_update_process(ROOT.parent, os.getpid(), info["tag"])
            show_dialog(self, QMessageBox.Icon.Information, "Updating SpiceConex", "SpiceConex will close, update from GitHub and restart automatically.", "The independent updater refuses local changes, prepares dependencies before changing source files, then fast-forwards the Git branch.")
            QApplication.instance().quit()
        except Exception as exc:
            logger.exception("Update action failed | %s: %s", type(exc).__name__, exc)
            show_exception(self, "SpiceConex update", "applying the application update", exc, QMessageBox.Icon.Warning)

    @Slot(object)
    def _update_check_failed(self, exc):
        if not isinstance(exc, Exception):
            exc = RuntimeError(str(exc))
        logger.exception("Update check failed | %s: %s", type(exc).__name__, exc)
        show_exception(self, "SpiceConex update", "checking for application updates", exc, QMessageBox.Icon.Warning)

    def _update_check_cleanup(self):
        if self.update_thread:
            self.update_thread.deleteLater()
        self.update_thread = None
        self.update_worker = None
        self.sidebar.update_button.setEnabled(True)
        self.sidebar.update_button.setText("CHECK FOR UPDATES")


def main():
    get_logger()
    logger.info("Application starting | log_file=%s", log_path())
    parser=argparse.ArgumentParser(); parser.add_argument("--demo",action="store_true"); args=parser.parse_args()
    app=QApplication([]); app.setStyle("Fusion"); app.setStyleSheet(STYLE); app.setFont(QFont("Segoe UI",10))
    if LOGO_SIDEBAR.exists():
        app.setWindowIcon(QIcon(str(LOGO_SIDEBAR))); window=MainWindow(args.demo); window.show()
        update_error = ROOT.parent / "update-error.log"
        if update_error.exists():
            details = update_error.read_text(encoding="utf-8", errors="replace").strip()
            update_error.unlink(missing_ok=True)
            show_dialog(window, QMessageBox.Icon.Warning, "SpiceConex update failed", "SpiceConex has restarted normally. Review the details before trying the update again.", details)
        app.exec()

if __name__ == "__main__": main()
