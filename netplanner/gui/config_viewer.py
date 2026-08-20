"""Read-only viewer for device configuration files.

Renders a stored ConfigFile with line numbers and light vendor-aware
highlighting. Deliberately read-only: NetPlanner stores configs as
documentation attached to a device, and silently editing a captured
running-config would make the plan disagree with the hardware it
describes. Import a fresh copy to update one.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from netplanner.domain.entities import ConfigFile, ConfigFormat

# Colors chosen to match the card/link palette used elsewhere.
COMMENT_COLOR = "#6a737d"
KEYWORD_COLOR = "#1a56db"
IP_COLOR = "#137333"
STRING_COLOR = "#b02a37"
GUTTER_BG = "#f4f4f6"
GUTTER_FG = "#9aa0a6"

# Leading keywords worth emphasising per vendor. These are the words that
# start a stanza, so highlighting them makes the block structure of a long
# config scannable without parsing it properly.
_KEYWORDS: dict[ConfigFormat, tuple[str, ...]] = {
    ConfigFormat.CISCO_IOS: (
        "interface", "router", "ip", "no", "hostname", "vlan", "switchport",
        "spanning-tree", "line", "access-list", "description", "shutdown",
        "version", "service", "enable", "username", "crypto", "snmp-server",
    ),
    ConfigFormat.MIKROTIK: (
        "add", "set", "remove", "print", "enable", "disable", "comment",
    ),
    ConfigFormat.UBIQUITI: (
        "set", "delete", "show", "interfaces", "firewall", "service",
        "system", "protocols",
    ),
    ConfigFormat.PLAIN_TEXT: (),
}

_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b")
_QUOTED_RE = re.compile(r'"[^"]*"')
_MIKROTIK_PATH_RE = re.compile(r"^/[\w\- /]+")


class ConfigHighlighter(QSyntaxHighlighter):
    """Line-oriented highlighter; no real parsing, just useful emphasis."""

    def __init__(self, document, config_format: ConfigFormat):
        super().__init__(document)
        self.config_format = config_format

        self._comment_fmt = self._make_format(COMMENT_COLOR, italic=True)
        self._keyword_fmt = self._make_format(KEYWORD_COLOR, bold=True)
        self._ip_fmt = self._make_format(IP_COLOR)
        self._string_fmt = self._make_format(STRING_COLOR)

        keywords = _KEYWORDS.get(config_format, ())
        # Anchored at line start (after indentation) so "ip" inside a
        # description isn't highlighted as a command.
        self._keyword_re = (
            re.compile(r"^\s*(" + "|".join(re.escape(k) for k in keywords) + r")\b")
            if keywords
            else None
        )

    @staticmethod
    def _make_format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def highlightBlock(self, text: str) -> None:
        """Called by Qt for each visible line."""
        stripped = text.lstrip()

        # A comment line is styled as a whole and nothing else applies.
        if stripped.startswith(self.config_format.comment_prefixes):
            self.setFormat(0, len(text), self._comment_fmt)
            return

        if self._keyword_re:
            match = self._keyword_re.match(text)
            if match:
                self.setFormat(match.start(1), len(match.group(1)), self._keyword_fmt)

        # MikroTik configs are organised by /path headers rather than indentation.
        if self.config_format is ConfigFormat.MIKROTIK:
            path = _MIKROTIK_PATH_RE.match(text)
            if path:
                self.setFormat(0, len(path.group(0)), self._keyword_fmt)

        for match in _IP_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._ip_fmt)
        for match in _QUOTED_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._string_fmt)


class _LineNumberGutter(QWidget):
    """Narrow strip painted to the left of the editor with line numbers."""

    def __init__(self, editor: "ConfigTextView"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)


class ConfigTextView(QPlainTextEdit):
    """Monospace, read-only text view with a line-number gutter."""

    def __init__(self, config: ConfigFile, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        font = QFont("DejaVu Sans Mono, Consolas, monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.setFont(font)

        self.setPlainText(config.content)
        self._highlighter = ConfigHighlighter(self.document(), config.config_format)

        self._gutter = _LineNumberGutter(self)
        self.blockCountChanged.connect(lambda _: self._update_gutter_width())
        self.updateRequest.connect(self._on_update_request)
        self._update_gutter_width()

    # ------------------------------------------------------------- gutter
    def gutter_width(self) -> int:
        """Width needed for the widest line number, plus padding."""
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        """Draw line numbers for the currently visible blocks."""
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(GUTTER_BG))
        painter.setPen(QColor(GUTTER_FG))

        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, int(top), self._gutter.width() - 6, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            number += 1


class ConfigViewerDialog(QDialog):
    """Full-window read-only view of one config, with find-as-you-type."""

    def __init__(self, config: ConfigFile, device_name: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(f"{config.filename} — {device_name}")
        self.resize(900, 640)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>{config.filename}</b> &nbsp;·&nbsp; {config.config_format.label}"
            f" &nbsp;·&nbsp; {config.line_count} lines &nbsp;·&nbsp; {config.size_label}"
        )
        layout.addWidget(header)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find in config… (Enter for next match)")
        self.search_edit.returnPressed.connect(self._find_next)
        find_btn = QPushButton("Find next")
        find_btn.clicked.connect(self._find_next)
        search_row.addWidget(self.search_edit)
        search_row.addWidget(find_btn)
        layout.addLayout(search_row)

        self.view = ConfigTextView(config)
        layout.addWidget(self.view)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.status)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)

    def _find_next(self) -> None:
        """Search forward, wrapping to the top once at the end."""
        needle = self.search_edit.text()
        if not needle:
            return
        if self.view.find(needle):
            self.status.setText("")
            return
        # Wrap: restart from the top and try once more.
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.view.setTextCursor(cursor)
        if self.view.find(needle):
            self.status.setText("Wrapped to the top of the file.")
        else:
            self.status.setText(f"No match for “{needle}”.")
