"""PyQt6 widgets for history list, replay, and manual correction."""
from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QTextCharFormat, QTextCursor, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # pragma: no cover - depends on local Qt multimedia install
    QAudioOutput = None
    QMediaPlayer = None

from i18n import _LANG, t
from history_store import STAMP_RE, parse_transcript_segments


def _label(key: str) -> str:
    labels = {
        "history": {"zh": "\u5386\u53f2\u8bb0\u5f55", "ja": "\u5c65\u6b74", "en": "History"},
        "back": {"zh": "\u2190 \u8fd4\u56de", "ja": "\u2190 \u623b\u308b", "en": "\u2190 Back"},
        "finish": {"zh": "\u7ed3\u675f", "ja": "\u7d42\u4e86", "en": "Finish"},
        "play": {"zh": "\u25b6 \u64ad\u653e", "ja": "\u25b6 \u518d\u751f", "en": "\u25b6 Play"},
        "pause": {"zh": "\u23f8 \u6682\u505c", "ja": "\u23f8 \u4e00\u6642\u505c\u6b62", "en": "\u23f8 Pause"},
        "save_correction": {"zh": "\u4fdd\u5b58\u8ba2\u6b63", "ja": "\u8a02\u6b63\u3092\u4fdd\u5b58", "en": "Save Correction"},
        "learn_rules": {"zh": "\u5c06\u5b66\u4e60\u4ee5\u4e0b\u8ba2\u6b63\u89c4\u5219", "ja": "\u4ee5\u4e0b\u306e\u8a02\u6b63\u30eb\u30fc\u30eb\u3092\u5b66\u7fd2\u3057\u307e\u3059", "en": "Learn these correction rules"},
        "no_history": {"zh": "\u6ca1\u6709\u5386\u53f2\u8bb0\u5f55", "ja": "\u5c65\u6b74\u304c\u3042\u308a\u307e\u305b\u3093", "en": "No history yet"},
        "missing_audio": {"zh": "\u7f3a\u5c11\u97f3\u9891", "ja": "\u97f3\u58f0\u306a\u3057", "en": "Missing audio"},
        "missing_text": {"zh": "\u7f3a\u5c11\u7ea0\u9519\u6587\u672c", "ja": "\u8a02\u6b63\u6587\u306a\u3057", "en": "Missing corrected text"},
        "saved": {"zh": "\u8ba2\u6b63\u5df2\u4fdd\u5b58", "ja": "\u8a02\u6b63\u3092\u4fdd\u5b58\u3057\u307e\u3057\u305f", "en": "Correction saved"},
        "player_missing": {"zh": "\u5f53\u524d PyQt6 \u7f3a\u5c11 QtMultimedia\uff0c\u65e0\u6cd5\u64ad\u653e\u3002", "ja": "PyQt6 QtMultimedia \u304c\u306a\u3044\u305f\u3081\u518d\u751f\u3067\u304d\u307e\u305b\u3093\u3002", "en": "PyQt6 QtMultimedia is unavailable, so playback cannot start."},
        "find_placeholder": {"zh": "\u5728\u6587\u5b57\u4e2d\u68c0\u7d22", "ja": "\u6587\u5b57\u3092\u691c\u7d22", "en": "Find in text"},
        "find_prev": {"zh": "\u2191", "ja": "\u2191", "en": "\u2191"},
        "find_next": {"zh": "\u2193", "ja": "\u2193", "en": "\u2193"},
        "find_none": {"zh": "\u65e0\u7ed3\u679c", "ja": "\u7d50\u679c\u306a\u3057", "en": "No results"},
    }
    return labels.get(key, {}).get(_LANG, labels.get(key, {}).get("en", key))


def _nav_button_style(top: str = "#2E5A8A", bottom: str = "#1D4168",
                      hover_top: str = "#37699D", hover_bottom: str = "#245078") -> str:
    return (
        "QPushButton {"
        f" background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom});"
        " color: #F0F6FC; border: 1px solid #3B6F9F; border-radius: 17px;"
        " padding: 7px 18px; font-size: 13px; font-weight: 700; min-height: 34px;"
        "}"
        "QPushButton:hover {"
        f" background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {hover_top}, stop:1 {hover_bottom});"
        " border-color: #4AA8FF;"
        "}"
        "QPushButton:pressed { background-color: #17324F; }"
    )


class LearnRulesDialog(QDialog):
    def __init__(self, pairs: list[tuple[str, str]], terms: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_label("learn_rules"))
        self._pair_checks: list[tuple[QCheckBox, tuple[str, str]]] = []
        self._term_checks: list[tuple[QCheckBox, str]] = []

        layout = QVBoxLayout(self)
        title = QLabel(_label("learn_rules"), self)
        title.setStyleSheet("font-weight: 600; color: #E6EDF3;")
        layout.addWidget(title)

        for pair in pairs:
            cb = QCheckBox(f"{pair[0]} => {pair[1]}", self)
            cb.setChecked(True)
            self._pair_checks.append((cb, pair))
            layout.addWidget(cb)
        for term in terms:
            cb = QCheckBox(term, self)
            cb.setChecked(True)
            self._term_checks.append((cb, term))
            layout.addWidget(cb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> tuple[list[tuple[str, str]], list[str]]:
        pairs = [pair for cb, pair in self._pair_checks if cb.isChecked()]
        terms = [term for cb, term in self._term_checks if cb.isChecked()]
        return pairs, terms


class EditSegmentDialog(QDialog):
    def __init__(self, stamp: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(stamp)
        self.resize(620, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        label = QLabel(f"[{stamp}]", self)
        label.setStyleSheet("color: #9FD0FF; font-weight: 700;")
        layout.addWidget(label)

        self._edit = QTextEdit(self)
        self._edit.setPlainText(text)
        self._edit.setStyleSheet(
            "QTextEdit { background-color: #0D1117; color: #E6EDF3;"
            " border: 1px solid #3B4A5C; border-radius: 5px; padding: 8px;"
            " font-size: 13px; line-height: 1.8; }"
        )
        layout.addWidget(self._edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return " ".join(self._edit.toPlainText().splitlines()).strip()


class HistoryListWidget(QWidget):
    sessionSelected = pyqtSignal(str)
    backRequested = pyqtSignal()

    def __init__(self, presenter, parent=None):
        super().__init__(parent)
        self._presenter = presenter
        self.setObjectName("historyListPage")
        self.setStyleSheet(
            "QWidget#historyListPage { background-color: #0E1218; }"
            "QListWidget { background-color: #0D1117; border: 1px solid #232B36;"
            " border-radius: 5px; padding: 6px; color: #DDE3EA; outline: none; }"
            "QListWidget::item { padding: 10px 12px; border-bottom: 1px solid #18202B; }"
            "QListWidget::item:selected { background-color: #16324A; color: #FFFFFF; }"
            "QListWidget::item:hover { background-color: #17212E; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self._back = QPushButton(_label("back"), self)
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.setStyleSheet(_nav_button_style())
        self._back.clicked.connect(self.backRequested.emit)
        top.addWidget(self._back)
        title = QLabel(_label("history"), self)
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #E6EDF3;")
        top.addWidget(title)
        top.addStretch()
        layout.addLayout(top)

        self._list = QListWidget(self)
        self._list.itemActivated.connect(self._on_item)
        self._list.itemClicked.connect(self._on_item)
        layout.addWidget(self._list, 1)

    def refresh(self) -> None:
        self._list.clear()
        sessions = self._presenter.history_sessions()
        if not sessions:
            item = QListWidgetItem(_label("no_history"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            return
        for session in sessions:
            status = []
            if not session.audio_path:
                status.append(_label("missing_audio"))
            if not session.corrected_path:
                status.append(_label("missing_text"))
            suffix = f"  ({', '.join(status)})" if status else ""
            item = QListWidgetItem(f"{session.display_time}  {session.title}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, session.ts)
            if not session.can_replay:
                item.setForeground(QColor("#6B7480"))
            self._list.addItem(item)

    def _on_item(self, item: QListWidgetItem) -> None:
        ts = item.data(Qt.ItemDataRole.UserRole)
        if ts:
            self.sessionSelected.emit(ts)


class HistoryDetailWidget(QWidget):
    finished = pyqtSignal()

    def __init__(self, presenter, parent=None):
        super().__init__(parent)
        self._presenter = presenter
        self._session = None
        self._segments = []
        self._original_text = ""
        self._current_index = -1
        self._search_matches: list[tuple[int, int, int]] = []
        self._search_index = -1
        self._block_to_segment: dict[int, int] = {}
        self._segment_to_block: dict[int, int] = {}
        self.setObjectName("historyDetailPage")
        self.setStyleSheet(
            "QWidget#historyDetailPage { background-color: #0E1218; }"
            "QLineEdit { background-color: #0D1117; color: #E6EDF3;"
            " border: 1px solid #2A2F3A; border-radius: 5px; padding: 6px 9px; }"
            "QLineEdit:focus { border-color: #4AA8FF; }"
        )

        self._player = None
        self._audio_output = None
        if QMediaPlayer and QAudioOutput:
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._finish = QPushButton(_label("finish"), self)
        self._finish.setCursor(Qt.CursorShape.PointingHandCursor)
        self._finish.setStyleSheet(_nav_button_style("#5A4630", "#3F301F", "#6B553A", "#4B3924"))
        self._finish.clicked.connect(self._finish_clicked)
        top.addWidget(self._finish)
        self._title = QLabel("", self)
        self._title.setStyleSheet("font-size: 16px; font-weight: 700; color: #E6EDF3;")
        top.addWidget(self._title)
        top.addStretch()
        layout.addLayout(top)

        controls = QHBoxLayout()
        self._play = QPushButton(_label("play"), self)
        self._play.clicked.connect(self._toggle_play)
        controls.addWidget(self._play)
        self._save = QPushButton(_label("save_correction"), self)
        self._save.clicked.connect(self._save_clicked)
        controls.addWidget(self._save)
        self._status = QLabel("", self)
        self._status.setStyleSheet("color: #9AA3B2;")
        controls.addWidget(self._status)
        controls.addStretch()
        layout.addLayout(controls)

        find_bar = QHBoxLayout()
        find_bar.setSpacing(6)
        self._find = QLineEdit(self)
        self._find.setPlaceholderText(_label("find_placeholder"))
        self._find.textChanged.connect(self._on_find_text)
        self._find.returnPressed.connect(self._find_next)
        find_bar.addWidget(self._find, 1)
        self._find_count = QLabel("0/0", self)
        self._find_count.setMinimumWidth(58)
        self._find_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._find_count.setStyleSheet("color: #9AA3B2;")
        find_bar.addWidget(self._find_count)
        self._find_prev_btn = QPushButton(_label("find_prev"), self)
        self._find_prev_btn.setFixedWidth(34)
        self._find_prev_btn.clicked.connect(self._find_prev)
        find_bar.addWidget(self._find_prev_btn)
        self._find_next_btn = QPushButton(_label("find_next"), self)
        self._find_next_btn.setFixedWidth(34)
        self._find_next_btn.clicked.connect(self._find_next)
        find_bar.addWidget(self._find_next_btn)
        layout.addLayout(find_bar)

        self._editor = QTextEdit(self)
        self._editor.setReadOnly(True)
        self._editor.setStyleSheet(
            "QTextEdit { font-family: 'JetBrains Mono', 'Noto Sans JP', 'Meiryo UI', 'Microsoft YaHei';"
            " font-size: 13px; line-height: 1.95; padding: 12px;"
            " background-color: #0D1117; border: 1px solid #232B36; border-radius: 5px; }"
        )
        self._editor.viewport().installEventFilter(self)
        layout.addWidget(self._editor, 1)

    def eventFilter(self, obj, event) -> bool:
        mouse_events = {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick}
        if obj is self._editor.viewport() and event.type() in mouse_events and event.button() == Qt.MouseButton.LeftButton:
            cursor = self._editor.cursorForPosition(event.position().toPoint())
            segment_index = self._block_to_segment.get(cursor.blockNumber())
            if segment_index is None:
                return super().eventFilter(obj, event)
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._edit_segment(segment_index)
                return True
            if event.type() == QEvent.Type.MouseButtonPress:
                self._select_segment(segment_index, seek=True, play=False)
                return True
        return super().eventFilter(obj, event)

    def load(self, ts: str) -> None:
        self._stop_player()
        self._session, self._segments, self._original_text = self._presenter.history_load_session(ts)
        self._current_index = -1
        self._search_matches = []
        self._search_index = -1
        self._title.setText(f"{self._session.display_time}  {self._session.title}")
        self._editor.setPlainText(self._original_text)
        self._editor.setReadOnly(True)
        self._rebuild_block_segment_map()
        self._find.clear()
        self._update_find_count()
        self._status.setText(os.path.basename(self._session.corrected_path))
        self._play.setText(_label("play"))
        self._play.setEnabled(bool(self._session.audio_path and self._player))
        self._save.setEnabled(bool(self._session.corrected_path))
        if self._player and self._session.audio_path:
            self._player.setSource(QUrl.fromLocalFile(self._session.audio_path))
        elif not self._player:
            self._status.setText(_label("player_missing"))

    def _on_duration(self, duration_ms: int) -> None:
        if self._session and duration_ms > 0:
            self._segments = parse_transcript_segments(
                self._session.ts, self._original_text, duration_ms / 1000.0)

    def _toggle_play(self) -> None:
        if not self._player:
            QMessageBox.warning(self, _label("history"), _label("player_missing"))
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._editor.setReadOnly(True)
            self._play.setText(_label("play"))
        else:
            self._editor.setReadOnly(True)
            self._player.play()
            self._play.setText(_label("pause"))

    def _on_position(self, position_ms: int) -> None:
        pos = position_ms / 1000.0
        index = -1
        for i, segment in enumerate(self._segments):
            if segment.start_sec <= pos < segment.end_sec:
                index = i
                break
        if index != self._current_index:
            self._select_segment(index, seek=False, play=False)

    def _select_segment(self, segment_index: int, seek: bool, play: bool) -> None:
        if not (0 <= segment_index < len(self._segments)):
            return
        self._current_index = segment_index
        self._highlight_current()
        if seek:
            self._seek_to_segment(segment_index, play=play)

    def _highlight_current(self) -> None:
        selections = []
        for pos, length, _line in self._search_matches:
            cursor = QTextCursor(self._editor.document())
            cursor.setPosition(pos)
            cursor.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#5A4218"))
            fmt.setForeground(QColor("#FFFFFF"))
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
        if 0 <= self._search_index < len(self._search_matches):
            pos, length, _line = self._search_matches[self._search_index]
            cursor = QTextCursor(self._editor.document())
            cursor.setPosition(pos)
            cursor.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#C68A12"))
            fmt.setForeground(QColor("#0D1117"))
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
        if self._current_index >= 0:
            block_number = self._segment_to_block.get(self._current_index, self._current_index)
            block = self._editor.document().findBlockByNumber(block_number)
            cursor = QTextCursor(block)
            cursor.setPosition(block.position())
            cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#FFD54A"))
            fmt.setForeground(QColor("#000000"))
            fmt.setFontWeight(900)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
            scroll_cursor = QTextCursor(cursor)
            scroll_cursor.setPosition(cursor.selectionStart())
            self._editor.setTextCursor(scroll_cursor)
            self._editor.ensureCursorVisible()
        self._editor.setExtraSelections(selections)

    def _edit_segment(self, segment_index: int) -> None:
        if not (0 <= segment_index < len(self._segments)):
            return
        if self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._play.setText(_label("play"))
        self._select_segment(segment_index, seek=True, play=False)

        segment = self._segments[segment_index]
        dialog = EditSegmentDialog(segment.stamp, segment.text, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_body = dialog.text()
        block_number = self._segment_to_block.get(segment_index)
        if block_number is None:
            return
        lines = self._editor.toPlainText().splitlines()
        if not (0 <= block_number < len(lines)):
            return
        lines[block_number] = f"[{segment.stamp}] {new_body}"
        self._editor.setPlainText("\n".join(lines))
        self._segments = parse_transcript_segments(
            self._session.ts, self._editor.toPlainText()) if self._session else self._segments
        self._rebuild_block_segment_map()
        self._current_index = min(segment_index, len(self._segments) - 1)
        self._on_find_text(self._find.text(), play_first=False)
        self._highlight_current()

    def _on_find_text(self, text: str, play_first: bool = True) -> None:
        self._search_matches = []
        self._search_index = -1
        needle = text.strip()
        if not needle:
            self._update_find_count()
            self._highlight_current()
            return
        haystack = self._editor.toPlainText()
        haystack_fold = haystack.casefold()
        needle_fold = needle.casefold()
        start = 0
        while True:
            pos = haystack_fold.find(needle_fold, start)
            if pos < 0:
                break
            block = self._editor.document().findBlock(pos)
            segment_index = self._block_to_segment.get(block.blockNumber(), -1)
            self._search_matches.append((pos, len(needle), segment_index))
            start = pos + max(1, len(needle))
        if self._search_matches:
            self._search_index = 0
            self._jump_to_search_result(play=play_first)
        else:
            self._update_find_count()
            self._highlight_current()

    def _find_next(self) -> None:
        if not self._search_matches:
            self._update_find_count()
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        self._jump_to_search_result(play=True)

    def _find_prev(self) -> None:
        if not self._search_matches:
            self._update_find_count()
            return
        self._search_index = (self._search_index - 1) % len(self._search_matches)
        self._jump_to_search_result(play=True)

    def _jump_to_search_result(self, play: bool) -> None:
        if not (0 <= self._search_index < len(self._search_matches)):
            self._update_find_count()
            return
        pos, length, segment_index = self._search_matches[self._search_index]
        cursor = QTextCursor(self._editor.document())
        cursor.setPosition(pos)
        cursor.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
        self._editor.setTextCursor(cursor)
        self._editor.ensureCursorVisible()
        self._update_find_count()
        if 0 <= segment_index < len(self._segments):
            self._select_segment(segment_index, seek=True, play=play)
        else:
            self._current_index = -1
            self._highlight_current()

    def _seek_to_segment(self, segment_index: int, play: bool) -> None:
        if not (0 <= segment_index < len(self._segments)) or not self._player:
            return
        self._player.setPosition(int(self._segments[segment_index].start_sec * 1000))
        if play:
            self._editor.setReadOnly(True)
            self._player.play()
            self._play.setText(_label("pause"))
        elif self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._play.setText(_label("play"))

    def _rebuild_block_segment_map(self) -> None:
        self._block_to_segment = {}
        self._segment_to_block = {}
        segment_index = 0
        for block_index, line in enumerate(self._editor.toPlainText().splitlines()):
            if STAMP_RE.match(line):
                self._block_to_segment[block_index] = segment_index
                self._segment_to_block[segment_index] = block_index
                segment_index += 1

    def _update_find_count(self) -> None:
        total = len(self._search_matches)
        if total:
            self._find_count.setText(f"{self._search_index + 1}/{total}")
            self._find_count.setStyleSheet("color: #D7B56D;")
        elif self._find.text().strip():
            self._find_count.setText(_label("find_none"))
            self._find_count.setStyleSheet("color: #FF8A82;")
        else:
            self._find_count.setText("0/0")
            self._find_count.setStyleSheet("color: #9AA3B2;")

    def _save_clicked(self) -> None:
        if not self._session:
            return
        new_text = self._editor.toPlainText()
        try:
            pairs, terms = self._presenter.history_learning_candidates(self._original_text, new_text)
        except Exception as exc:
            QMessageBox.warning(self, _label("save_correction"), str(exc))
            return
        if pairs or terms:
            dialog = LearnRulesDialog(pairs, terms, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            pairs, terms = dialog.selected()
        try:
            self._presenter.history_save_session(self._session.corrected_path, new_text, pairs, terms)
        except Exception as exc:
            QMessageBox.warning(self, _label("save_correction"), str(exc))
            return
        self._original_text = new_text
        if self._session:
            self._segments = parse_transcript_segments(self._session.ts, self._original_text)
        self._rebuild_block_segment_map()
        self._on_find_text(self._find.text(), play_first=False)
        QMessageBox.information(self, _label("save_correction"), _label("saved"))

    def _finish_clicked(self) -> None:
        self._stop_player()
        self.finished.emit()

    def _stop_player(self) -> None:
        if self._player:
            self._player.stop()
        self._editor.setReadOnly(True)
        self._play.setText(_label("play"))
