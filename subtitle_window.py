"""
Transparent floating subtitle window.

- Frameless, always on top, semi-transparent black background
- Polls .subtitle_text file and displays current subtitle
- Draggable by mouse
- Resizable via corner handle
- Double-click to hide/show
"""
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QSizeGrip

SUBTITLE_FILE = os.path.join(_BASE, ".subtitle_text")
POLL_MS       = 150   # polling interval


class SubtitleWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(400, 60)

        # ── initial position: bottom-center ──────────────────────────────────
        screen = QApplication.primaryScreen().availableGeometry()
        w, h   = 900, 100
        self.setGeometry(
            (screen.width() - w) // 2,
            screen.height() - h - 80,
            w, h
        )

        # ── label ─────────────────────────────────────────────────────────────
        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        self._label.setStyleSheet(
            "color: white;"
            "background: transparent;"
            "padding: 4px 12px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._label)

        # ── resize grip (bottom-right) ────────────────────────────────────────
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        # ── drag state ────────────────────────────────────────────────────────
        self._drag_pos: QPoint | None = None

        # ── polling timer ─────────────────────────────────────────────────────
        self._last_text = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_MS)

    # ── painting: rounded semi-transparent background ─────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 12, 12)

    # ── subtitle polling ──────────────────────────────────────────────────────
    def _poll(self):
        try:
            with open(SUBTITLE_FILE, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            text = ""
        except Exception:
            return

        if text != self._last_text:
            self._last_text = text
            self._label.setText(text)
            # auto-hide if empty
            if text:
                self.show()
            else:
                self._label.setText("")

    # ── drag to move ──────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        """Double-click toggles always-on-top."""
        flags = self.windowFlags()
        if flags & Qt.WindowType.WindowStaysOnTopHint:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
            self.setWindowTitle("字幕 (通常)")
        else:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
            self.setWindowTitle("字幕")
        self.show()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Sound2Text 字幕")
    win = SubtitleWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
