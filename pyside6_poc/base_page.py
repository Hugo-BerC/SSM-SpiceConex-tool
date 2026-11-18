"""Reusable page surface and AWS context binding."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QLinearGradient, QRadialGradient
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from pyside6_poc.tasks import AwsContext
from pyside6_poc.theme import TEXTURE, SKIN, VOID

class DuneBackdrop(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.texture = QPixmap(str(TEXTURE)) if TEXTURE.exists() else QPixmap()
        self.skin = QPixmap(str(SKIN)) if SKIN.exists() else QPixmap()
        # Pages can tune their atmosphere without changing the shared artwork.
        self.texture_opacity = 0.14
        self.skin_opacity = 0.13
        self.shade_opacity = (58, 28, 18)
        self.setObjectName("backdrop")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(VOID))
        if not self.texture.isNull():
            texture = self.texture.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(self.texture_opacity)
            painter.drawPixmap((self.width() - texture.width()) // 2, (self.height() - texture.height()) // 2, texture)
        if not self.skin.isNull():
            skin = self.skin.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(self.skin_opacity)
            painter.drawPixmap((self.width() - skin.width()) // 2, (self.height() - skin.height()) // 2, skin)
        glow = QRadialGradient(self.width() * 0.82, self.height() * 0.78, max(self.width(), self.height()) * 0.42)
        glow.setColorAt(0.0, QColor(211, 145, 77, 28))
        glow.setColorAt(1.0, QColor(211, 145, 77, 0))
        painter.setOpacity(1.0)
        painter.fillRect(self.rect(), glow)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, self.shade_opacity[0]))
        gradient.setColorAt(0.55, QColor(0, 0, 0, self.shade_opacity[1]))
        gradient.setColorAt(1.0, QColor(0, 0, 0, self.shade_opacity[2]))
        painter.fillRect(self.rect(), gradient)
        painter.end()


class BasePage(DuneBackdrop):
    def bind_aws_context(self):
        source = getattr(self, "source", self)
        self.aws_context = AwsContext(self, source.commands, self.context_changed)

    def context_changed(self):
        """Pages with account-bound data override this to invalidate it."""

    def page_header(self, eyebrow, title, subtitle, status="◆  READY"):
        self.bind_aws_context()
        root = self.layout = QVBoxLayout(self); root.setContentsMargins(34, 28, 34, 22); root.setSpacing(16)
        header = QHBoxLayout(); box = QVBoxLayout(); box.setSpacing(2)
        for text, obj in [(eyebrow, "pageEyebrow"), (title, "pageTitle"), (subtitle, "pageSubtitle")]:
            label = QLabel(text); label.setObjectName(obj); box.addWidget(label)
        header.addLayout(box); header.addStretch(); self.status = QLabel(status); self.status.setObjectName("topStatus"); header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop); root.addLayout(header)
        return root
