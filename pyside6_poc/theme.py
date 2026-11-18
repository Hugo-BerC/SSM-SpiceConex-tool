"""Shared asset locations, palette and stylesheet."""
from pathlib import Path

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


STYLE = """
* { font-family: "Segoe UI", "Inter", sans-serif; color: #eee9df; }
QMainWindow { background: #080a0b; }
QToolTip { background: #151b1c; color: #eee9df; border: 1px solid #3b3930; padding: 6px; }
#sidebar { background: #090b0c; border-right: 1px solid #252b2a; }
#brandLogo { background: transparent; padding: 0; }
#brandRule { background: rgba(185,104,54,0.48); margin: 0 8px; }
#sidebarSection { color: #9a8666; font-size: 9px; font-weight: 780; letter-spacing: 1.7px; padding-left: 11px; }
#eyebrow, #microLabel { color: #b5a078; font-size: 9px; font-weight: 760; letter-spacing: 1.5px; }
#navButton { text-align: left; border: 0; border-left: 2px solid transparent; border-radius: 0; padding: 8px 10px; color: #777d79; font-size: 10px; font-weight: 700; background: transparent; }
#navButton:hover { background: rgba(199,165,106,0.035); color: #d7d0c4; }
#navButton:checked { background: rgba(185,104,54,0.035); color: #e0c68e; border-left: 2px solid #b96836; padding-left: 10px; }
#divider { background: #252b2a; }
#sideProfile { color: #c9c0ae; font-size: 10px; line-height: 1.4; }
#sideStatus { color: #7ea67a; font-size: 9px; font-weight: 760; letter-spacing: 1.2px; margin-top: 5px; }
#sideStatus[connectionState="context"], #sideStatus[connectionState="connecting"] { color: #c7a56a; }
#sideStatus[connectionState="error"] { color: #c56f60; }
#sidebarVersion { color: #84785f; font-size: 8px; font-weight: 720; letter-spacing: 1.35px; padding: 2px 0; }
#backdrop { background: #080a0b; }
#homeContext { color: #c9c0ae; font-size: 11px; background: rgba(8,10,11,0.50); border: 1px solid rgba(199,165,106,0.16); border-radius: 6px; padding: 10px 16px; }
#terminalContext { color: #d7d0c4; font-size: 11px; font-weight: 650; }
#pageEyebrow { color: #777d79; font-size: 9px; font-weight: 760; letter-spacing: 2.1px; }
#pageTitle { font-size: 31px; font-weight: 690; color: #eee9df; }
#pageSubtitle { color: #8b918d; font-size: 11px; }
#topStatus { background: rgba(185,104,54,0.10); border: 1px solid rgba(185,104,54,0.28); border-radius: 14px; color: #d47a3f; padding: 7px 12px; font-size: 9px; font-weight: 760; letter-spacing: 1px; }
#commandRail, #operationPanel, #tunnelPanel { background: rgba(17,22,23,0.93); border: 1px solid #29302f; border-top: 2px solid rgba(185,104,54,0.52); border-radius: 11px; }
QComboBox, QLineEdit, QPlainTextEdit { background: #0e1314; border: 1px solid #28302f; border-radius: 7px; padding: 9px 11px; color: #eee9df; selection-background-color: #3a2d1d; }
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { border-color: #424946; }
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border: 2px solid #d3914d; padding: 8px 10px; }
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
#quietButton:focus, #primaryButton:focus, #navButton:focus, QToolButton:focus { border: 2px solid #d3914d; outline: 0; }
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
#fleetEmpty { color: #858a86; font-size: 11px; padding: 42px 18px; }
#rowMeta { color: #858a86; font-size: 9px; font-weight: 760; letter-spacing: 1px; }
#rowValue { color: #c9c4b9; font-size: 10px; }
#stateBadge { color: #777d79; font-size: 9px; font-weight: 760; letter-spacing: .7px; min-width: 92px; }
#stateBadge[running="true"] { color: #7ea67a; }
#ssmBadge { font-size: 9px; font-weight: 760; letter-spacing: .6px; color: #c7a56a; }
#ssmBadge[ssmState="online"] { color: #7ea67a; }
#ssmBadge[ssmState="connectionlost"], #ssmBadge[ssmState="inactive"], #ssmBadge[ssmState="unmanaged"] { color: #c56f60; }
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
QTableWidget, QTableView {
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
QTableWidget::item, QTableView::item {
    padding: 5px 7px;
    border: 0;
}
QTableWidget::item:hover, QTableView::item:hover {
    background-color: #171d1e;
}
QTableWidget::item:selected, QTableView::item:selected {
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
