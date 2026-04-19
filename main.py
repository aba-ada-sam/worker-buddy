import sys
import json
import os
import urllib.request
import urllib.error
from datetime import datetime

__version__ = "1.0.0"
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QPlainTextEdit, QPushButton, QLabel, QSizeGrip, QSystemTrayIcon,
    QMenu, QAction, QActionGroup, QMessageBox, QFrame, QFileDialog,
    QSizePolicy
)
from PyQt5.QtCore import Qt, QSettings, QTimer, QSize, QEvent
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter, QTextCursor, QFont, QPalette

# ── Palette ───────────────────────────────────────────────────────────────────
BG_HEADER     = "#128C7E"
BG_HEADER_BTN = "#0e6b62"
BG_CHAT       = "#ECE5DD"
BG_INPUT_AREA = "#F0F2F5"
BG_INPUT_BOX  = "#FFFFFF"
BUBBLE_USER   = "#D9FDD3"
BUBBLE_AGENT  = "#FFFFFF"
BUBBLE_STATUS = "#F0F0F0"
ACCENT_SEND   = "#00A884"
ACCENT_STOP   = "#E05252"
TEXT_DARK     = "#111B21"
TEXT_DIM      = "#667781"
BORDER_LINE   = "#D9D9D9"
DOT_RUNNING   = "#00A884"
DOT_DONE      = "#25D366"
DOT_ERROR     = "#E05252"


def _make_tray_icon(color: str) -> QIcon:
    px = QPixmap(22, 22)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(3, 3, 16, 16)
    p.end()
    return QIcon(px)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ts() -> str:
    t = datetime.now().strftime("%I:%M %p")
    return t.lstrip("0") or t


# ── Chat Header ───────────────────────────────────────────────────────────────
class ChatHeader(QWidget):
    """Green WhatsApp-style title bar. Uses palette so the color is reliable."""

    def __init__(self, main_win: "MainWindow"):
        super().__init__(main_win)
        self._main = main_win
        self._drag_pos = None
        self.setFixedHeight(68)

        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(BG_HEADER))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(12)

        # Avatar circle
        av = QLabel("WB")
        av.setFixedSize(46, 46)
        av.setAlignment(Qt.AlignCenter)
        av.setFont(QFont("Segoe UI", 14, QFont.Bold))
        av.setStyleSheet(
            "background: rgba(255,255,255,0.22); border-radius: 23px; color: white;"
        )
        row.addWidget(av)

        # Name + status
        col = QVBoxLayout()
        col.setSpacing(2)
        name_lbl = QLabel("Worker Buddy")
        name_lbl.setFont(QFont("Segoe UI", 15, QFont.Bold))
        name_lbl.setStyleSheet("color: white;")
        self.sub_lbl = QLabel("idle")
        self.sub_lbl.setFont(QFont("Segoe UI", 11))
        self.sub_lbl.setStyleSheet("color: rgba(255,255,255,0.78);")
        col.addWidget(name_lbl)
        col.addWidget(self.sub_lbl)
        row.addLayout(col)
        row.addStretch()

        # Mode chip — clickable to toggle Browser <-> Desktop
        self.mode_chip = QPushButton("browser")
        self.mode_chip.setFixedHeight(26)
        self.mode_chip.setMinimumWidth(78)
        self.mode_chip.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.mode_chip.setCursor(Qt.PointingHandCursor)
        self.mode_chip.setToolTip("Click to switch between Browser and Desktop modes")
        self.mode_chip.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.18);
                color: white; border: 1px solid rgba(255,255,255,0.35);
                border-radius: 12px; padding: 0 12px;
                text-transform: uppercase; letter-spacing: 0.05em;
            }
            QPushButton:hover { background: rgba(255,255,255,0.32); }
        """)
        self.mode_chip.clicked.connect(lambda: self._main.toggle_mode())
        row.addWidget(self.mode_chip)

        for text, slot in [("—", lambda: self._main.hide()), ("✕", lambda: self._main.hide())]:
            b = QPushButton(text)
            b.setFixedSize(32, 32)
            b.setFont(QFont("Segoe UI", 14))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.14);
                    border: none; color: white; border-radius: 16px;
                }}
                QPushButton:hover {{
                    background: rgba(255,255,255,0.30);
                }}
            """)
            b.clicked.connect(slot)
            row.addWidget(b)

    def set_status(self, text: str):
        self.sub_lbl.setText(text)

    def set_mode(self, mode: str):
        """Update the mode chip label/colour."""
        m = mode if mode in ("browser", "desktop") else "browser"
        self.mode_chip.setText(m)
        # Browser = white-on-translucent (matches header). Desktop = warm tint
        # so it's obvious you're handing the AI control of the whole machine.
        if m == "desktop":
            self.mode_chip.setStyleSheet("""
                QPushButton {
                    background: #FFB347; color: #2A1500;
                    border: 1px solid #E89A2C; border-radius: 12px; padding: 0 12px;
                    text-transform: uppercase; letter-spacing: 0.05em;
                    font-weight: bold;
                }
                QPushButton:hover { background: #FFC56E; }
            """)
        else:
            self.mode_chip.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.18);
                    color: white; border: 1px solid rgba(255,255,255,0.35);
                    border-radius: 12px; padding: 0 12px;
                    text-transform: uppercase; letter-spacing: 0.05em;
                }
                QPushButton:hover { background: rgba(255,255,255,0.32); }
            """)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self._main.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self._main.move(e.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


# ── Attachment chip ───────────────────────────────────────────────────────────
class AttachChip(QWidget):
    def __init__(self, path: str, on_remove):
        super().__init__()
        self._path = path
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 8, 5)
        row.setSpacing(6)

        name = os.path.basename(path)
        if len(name) > 26:
            name = name[:12] + "..." + name[-11:]

        lbl = QLabel(f"Attach: {name}")
        lbl.setFont(QFont("Segoe UI", 12))
        lbl.setStyleSheet(f"color: {TEXT_DARK};")
        row.addWidget(lbl)

        rm = QPushButton("x")
        rm.setFixedSize(20, 20)
        rm.setFont(QFont("Segoe UI", 10, QFont.Bold))
        rm.setStyleSheet(f"""
            QPushButton {{ background: {BORDER_LINE}; border: none;
                           color: {TEXT_DIM}; border-radius: 10px; }}
            QPushButton:hover {{ background: {ACCENT_STOP}; color: white; }}
        """)
        rm.clicked.connect(lambda: on_remove(self._path))
        row.addWidget(rm)

        self.setStyleSheet(f"""
            AttachChip {{
                background: {BG_INPUT_BOX};
                border-radius: 14px;
                border: 1px solid {BORDER_LINE};
            }}
        """)


# ── Main Window ───────────────────────────────────────────────────────────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings       = QSettings("LynnCove", "WorkerBuddy3")
        self._drag_pos      = None
        self._agent_thread  = None
        self._pending_files: list[str] = []
        self._log_buffer:    list[str] = []

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(380)
        self._flush_timer.timeout.connect(self._flush_log)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(440, 560)

        self._build_ui()
        self._build_tray()
        self._restore_settings()

        QApplication.instance().setStyleSheet(self._app_css())

    # ── Global CSS ────────────────────────────────────────────────────────────
    def _app_css(self) -> str:
        return f"""
            QMenu {{
                background: white; color: {TEXT_DARK};
                border: 1px solid {BORDER_LINE}; border-radius: 8px; padding: 4px;
                font-size: 14px;
            }}
            QMenu::item {{ padding: 8px 22px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {BG_CHAT}; }}
            QMenu::separator {{ height: 1px; background: {BORDER_LINE}; margin: 4px 8px; }}
            QScrollBar:vertical {{
                width: 7px; background: transparent; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(0,0,0,0.20); border-radius: 4px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QSizeGrip {{ background: transparent; }}
        """

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Outer layout provides the drop-shadow margin
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        # Container card
        self.container = QWidget()
        self.container.setObjectName("wb_card")

        # White background via palette (not stylesheet) to avoid cascade
        pal = self.container.palette()
        pal.setColor(QPalette.Window, QColor("#FFFFFF"))
        self.container.setPalette(pal)
        self.container.setAutoFillBackground(True)
        self.container.setStyleSheet("QWidget#wb_card { border-radius: 18px; }")

        outer.addWidget(self.container)

        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # ── Header
        self.header = ChatHeader(self)
        cl.addWidget(self.header)

        # ── Chat area
        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        pal2 = self.chat.palette()
        pal2.setColor(QPalette.Base, QColor(BG_CHAT))
        self.chat.setPalette(pal2)
        self.chat.setFont(QFont("Segoe UI", 13))
        self.chat.setStyleSheet("QTextBrowser { border: none; padding: 8px 6px; }")
        cl.addWidget(self.chat, 1)

        # ── Attachment chips (hidden when empty)
        self.attach_bar = QWidget()
        pal3 = self.attach_bar.palette()
        pal3.setColor(QPalette.Window, QColor(BG_INPUT_AREA))
        self.attach_bar.setPalette(pal3)
        self.attach_bar.setAutoFillBackground(True)
        self._chips_layout = QHBoxLayout(self.attach_bar)
        self._chips_layout.setContentsMargins(10, 8, 10, 6)
        self._chips_layout.setSpacing(8)
        self._chips_layout.addStretch()
        self.attach_bar.hide()
        cl.addWidget(self.attach_bar)

        # ── Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {BORDER_LINE}; border: none;")
        cl.addWidget(div)

        # ── Input row
        input_bg = QWidget()
        pal4 = input_bg.palette()
        pal4.setColor(QPalette.Window, QColor(BG_INPUT_AREA))
        input_bg.setPalette(pal4)
        input_bg.setAutoFillBackground(True)

        ia = QHBoxLayout(input_bg)
        ia.setContentsMargins(10, 10, 10, 12)
        ia.setSpacing(8)

        # Attach button — plain text "+" for maximum clarity
        self.attach_btn = QPushButton("+")
        self.attach_btn.setFixedSize(46, 46)
        self.attach_btn.setFont(QFont("Segoe UI", 22))
        self.attach_btn.setToolTip("Attach file")
        self.attach_btn.setStyleSheet(f"""
            QPushButton {{
                background: {BG_INPUT_BOX};
                border: 1px solid {BORDER_LINE};
                color: {TEXT_DIM};
                border-radius: 23px;
            }}
            QPushButton:hover {{
                background: {BORDER_LINE};
                color: {TEXT_DARK};
            }}
            QPushButton:disabled {{ color: #C0C0C0; }}
        """)
        self.attach_btn.clicked.connect(self._attach_file)
        ia.addWidget(self.attach_btn)

        # Text input
        self.task_input = QPlainTextEdit()
        self.task_input.setPlaceholderText("Message...")
        self.task_input.setMinimumHeight(50)
        self.task_input.setMaximumHeight(140)
        self.task_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.task_input.setFont(QFont("Segoe UI", 14))
        self.task_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {BG_INPUT_BOX};
                color: {TEXT_DARK};
                border: 1px solid {BORDER_LINE};
                border-radius: 24px;
                padding: 12px 18px;
                selection-background-color: #C3E8D5;
            }}
            QPlainTextEdit:focus {{ border: 1px solid {ACCENT_SEND}; }}
        """)
        self.task_input.installEventFilter(self)
        ia.addWidget(self.task_input, 1)

        # Send / Stop button
        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedSize(72, 46)
        self.send_btn.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.send_btn.setStyleSheet(self._send_css(ACCENT_SEND))
        self.send_btn.clicked.connect(self._on_run_stop)
        ia.addWidget(self.send_btn)

        cl.addWidget(input_bg)

        # Resize grip
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(18, 18)

    def _send_css(self, color: str) -> str:
        darker = color  # keep it simple
        return (
            f"QPushButton {{ background: {color}; border: none; "
            f"border-radius: 23px; color: white; }}"
            f"QPushButton:hover {{ background: {BG_HEADER}; }}"
        )

    # ── Paint drop shadow manually ────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shadow_color = QColor(0, 0, 0, 60)
        for i in range(10, 0, -1):
            p.setPen(Qt.NoPen)
            p.setBrush(Qt.NoBrush)
            r = self.rect().adjusted(i, i, -i, -i)
            shadow_color.setAlpha(int(60 * (1 - i / 10)))
            p.setPen(shadow_color)
            p.drawRoundedRect(r, 18, 18)
        p.end()

    # ── Attachments ───────────────────────────────────────────────────────────
    def _attach_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Attach Files", "",
            "All Files (*);;"
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;"
            "Documents (*.pdf *.docx *.txt *.csv *.xlsx *.md)"
        )
        for f in files:
            if f and f not in self._pending_files:
                self._pending_files.append(f)
                chip = AttachChip(f, self._remove_file)
                self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)
        self.attach_bar.setVisible(bool(self._pending_files))

    def _remove_file(self, path: str):
        if path in self._pending_files:
            self._pending_files.remove(path)
        for i in range(self._chips_layout.count()):
            item = self._chips_layout.itemAt(i)
            w = item.widget() if item else None
            if w and hasattr(w, "_path") and w._path == path:
                self._chips_layout.removeWidget(w)
                w.deleteLater()
                break
        self.attach_bar.setVisible(bool(self._pending_files))

    def _clear_chips(self):
        while self._chips_layout.count() > 1:
            item = self._chips_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._pending_files.clear()
        self.attach_bar.hide()

    # ── Chat bubbles ──────────────────────────────────────────────────────────
    def _bubble(self, role: str, text: str, attachments: list | None = None):
        ts  = _ts()
        safe = _html_escape(text).replace("\n", "<br>")

        if role == "user":
            attach_html = ""
            if attachments:
                for f in attachments:
                    attach_html += f"<br><i><font size='2'>[{_html_escape(os.path.basename(f))}]</font></i>"
            html = (
                f"<table width='100%' cellspacing='3' cellpadding='0'><tr>"
                f"<td width='16%'></td>"
                f"<td align='right' style='padding-right:12px;'>"
                f"<table cellspacing='0' cellpadding='10' bgcolor='{BUBBLE_USER}'>"
                f"<tr><td>"
                f"<font face='Segoe UI' size='4' color='{TEXT_DARK}'>{safe}{attach_html}</font>"
                f"<br><font size='2' color='{TEXT_DIM}'>{ts} &nbsp;&#10003;&#10003;</font>"
                f"</td></tr></table>"
                f"</td></tr></table>"
            )

        elif role == "agent":
            html = (
                f"<table width='100%' cellspacing='3' cellpadding='0'><tr>"
                f"<td align='left' style='padding-left:12px;'>"
                f"<table cellspacing='0' cellpadding='10' bgcolor='{BUBBLE_AGENT}'>"
                f"<tr><td>"
                f"<font face='Segoe UI' size='4' color='{TEXT_DARK}'>{safe}</font>"
                f"<br><font size='2' color='{TEXT_DIM}'>{ts}</font>"
                f"</td></tr></table>"
                f"</td><td width='16%'></td></tr></table>"
            )

        else:  # status log
            html = (
                f"<table width='100%' cellspacing='1' cellpadding='0'><tr>"
                f"<td align='left' style='padding-left:12px;'>"
                f"<table cellspacing='0' cellpadding='7' bgcolor='{BUBBLE_STATUS}'>"
                f"<tr><td>"
                f"<font face='Consolas,Courier New,monospace' size='2' color='{TEXT_DIM}'>{safe}</font>"
                f"</td></tr></table>"
                f"</td><td width='22%'></td></tr></table>"
            )

        self.chat.append(html)
        self.chat.moveCursor(QTextCursor.End)

    # ── Log buffering ─────────────────────────────────────────────────────────
    def append_log(self, line: str):
        self._log_buffer.append(line)

    def _flush_log(self):
        if not self._log_buffer:
            return
        combined = "\n".join(self._log_buffer)
        self._log_buffer.clear()
        self._bubble("status", combined)

    # ── Mode (browser / desktop) ──────────────────────────────────────────────
    def current_mode(self) -> str:
        return self.settings.value("mode", "browser") or "browser"

    def set_mode(self, mode: str):
        if mode not in ("browser", "desktop"):
            mode = "browser"
        self.settings.setValue("mode", mode)
        self.header.set_mode(mode)
        # Keep the tray action group in sync if it exists yet.
        for act in getattr(self, "_mode_actions", []):
            act.setChecked(act.data() == mode)
        # Update task input placeholder so the user knows what kind of task fits.
        if mode == "desktop":
            self.task_input.setPlaceholderText("Desktop task — e.g. \"Open Notepad and type today's date\"")
        else:
            self.task_input.setPlaceholderText("Browser task — e.g. \"Search for the iPhone 17 release date\"")

    def toggle_mode(self):
        new = "desktop" if self.current_mode() == "browser" else "browser"
        self.set_mode(new)
        self._bubble("status", f"Mode: {new.upper()}")

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _build_tray(self):
        self.tray = QSystemTrayIcon(_make_tray_icon(ACCENT_SEND), self)
        self.tray.setToolTip("Worker Buddy")

        menu = QMenu()
        menu.addAction("Show / Hide", self._toggle_visible)
        menu.addAction("Clear Chat", self.chat.clear)
        menu.addSeparator()

        # Mode submenu
        mode_menu = QMenu("Mode", menu)
        mode_grp = QActionGroup(mode_menu)
        self._mode_actions: list[QAction] = []
        for label, val in [("Browser (web tasks)", "browser"), ("Desktop (any program)", "desktop")]:
            a = QAction(label, mode_menu)
            a.setCheckable(True)
            a.setData(val)
            a.setChecked(val == self.current_mode())
            a.triggered.connect(lambda _c, v=val: self.set_mode(v))
            mode_grp.addAction(a)
            mode_menu.addAction(a)
            self._mode_actions.append(a)
        menu.addMenu(mode_menu)
        menu.addSeparator()

        self.aot_action = QAction("Always on Top", menu)
        self.aot_action.setCheckable(True)
        self.aot_action.setChecked(True)
        self.aot_action.triggered.connect(self.set_always_on_top)
        menu.addAction(self.aot_action)

        op_menu = QMenu("Opacity", menu)
        op_grp = QActionGroup(op_menu)
        for label, val in [("100%", 1.0), ("85%", 0.85), ("70%", 0.70), ("55%", 0.55)]:
            a = QAction(label, op_menu)
            a.setCheckable(True)
            a.setChecked(val == 1.0)
            a.triggered.connect(lambda _c, v=val: self.set_opacity(v))
            op_grp.addAction(a)
            op_menu.addAction(a)
        menu.addMenu(op_menu)
        menu.addSeparator()
        menu.addAction("Settings...", self._open_settings)
        menu.addAction("Check for Update", self._check_for_update)
        menu.addSeparator()
        menu.addAction("Exit", self.confirm_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._toggle_visible() if r == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def _check_for_update(self):
        repo = self.settings.value("github_repo", "").strip()
        if not repo:
            QMessageBox.information(
                self, "Check for Update",
                "No GitHub repo configured.\n\n"
                "Add  github_repo = owner/repo  in Settings to enable update checks."
            )
            return

        url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WorkerBuddy"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            current = __version__.lstrip("v")
            if latest and latest != current:
                self.tray.showMessage(
                    "Update Available",
                    f"Worker Buddy {latest} is available (you have {current}).\n"
                    f"Download: {data.get('html_url', '')}",
                    QSystemTrayIcon.Information, 8000
                )
            else:
                self.tray.showMessage(
                    "Up to Date",
                    f"You're running the latest version ({__version__}).",
                    QSystemTrayIcon.Information, 4000
                )
        except Exception as e:
            self.tray.showMessage(
                "Update Check Failed",
                f"Could not reach GitHub: {e}",
                QSystemTrayIcon.Warning, 5000
            )

    # ── Settings ──────────────────────────────────────────────────────────────
    def _restore_settings(self):
        size = self.settings.value("size", QSize(520, 740))
        self.resize(size)

        if self.settings.contains("pos"):
            pos  = self.settings.value("pos")
            scr  = QApplication.primaryScreen().geometry()
            if 0 <= pos.x() < scr.width() - 80 and 0 <= pos.y() < scr.height() - 80:
                self.move(pos)
            else:
                self._center()
        else:
            self._center()

        aot = self.settings.value("always_on_top", True, type=bool)
        self.set_always_on_top(aot)
        self.aot_action.setChecked(aot)
        self.setWindowOpacity(self.settings.value("opacity", 1.0, type=float))

        # Apply persisted mode (also paints the chip + sets the placeholder)
        self.set_mode(self.current_mode())

        last = self.settings.value("last_task", "")
        if last:
            self.task_input.setPlainText(last)

    def _center(self):
        scr = QApplication.primaryScreen().geometry()
        self.move((scr.width() - self.width()) // 2, (scr.height() - self.height()) // 2)

    def _save_settings(self):
        self.settings.setValue("pos",  self.pos())
        self.settings.setValue("size", self.size())
        self.settings.setValue("last_task", self.task_input.toPlainText())

    def set_always_on_top(self, enabled: bool):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if enabled:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        self.settings.setValue("always_on_top", enabled)

    def set_opacity(self, value: float):
        self.setWindowOpacity(value)
        self.settings.setValue("opacity", value)

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def _open_settings(self):
        from settings_dialog import SettingsDialog
        SettingsDialog(self).exec_()

    # ── Enter = send, Shift+Enter = newline ───────────────────────────────────
    def eventFilter(self, obj, event):
        if obj is self.task_input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not (event.modifiers() & Qt.ShiftModifier):
                    if not (self._agent_thread and self._agent_thread.isRunning()):
                        self._on_run_stop()
                    return True
        return super().eventFilter(obj, event)

    # ── Agent ─────────────────────────────────────────────────────────────────
    def _on_run_stop(self):
        if self._agent_thread and self._agent_thread.isRunning():
            self._agent_thread.request_stop()
        else:
            self._start_agent()

    def _start_agent(self):
        task = self.task_input.toPlainText().strip()
        if not task:
            return

        attachments = list(self._pending_files)
        full_task   = task
        if attachments:
            full_task += "\n\nAttached files:\n" + "\n".join(attachments)

        creds_path = self.settings.value(
            "creds_path", r"C:\JSON Credentials\QB_WC_credentials.json"
        )
        try:
            with open(creds_path) as f:
                creds   = json.load(f)
            api_key = creds["anthropic_key"]
        except Exception as e:
            self._bubble("agent", f"Could not load credentials: {e}")
            return

        model        = self.settings.value("model", "claude-sonnet-4-5-20250929")
        show_browser = self.settings.value("show_browser", True, type=bool)
        mode         = self.settings.value("mode", "browser")  # "browser" | "desktop"
        max_steps    = int(self.settings.value("desktop_max_steps", 60))

        self._bubble("user", task, attachments=attachments)
        self.task_input.clear()
        self._clear_chips()
        self._set_running()

        from agent_thread import AgentThread
        self._agent_thread = AgentThread(
            full_task, api_key,
            mode=mode, model=model,
            show_browser=show_browser, max_steps=max_steps,
        )
        self._agent_thread.log_line.connect(self.append_log)
        self._agent_thread.finished.connect(self._on_agent_finished)
        self._agent_thread.start()
        self._flush_timer.start()

    def _on_agent_finished(self, status: str):
        self._flush_timer.stop()
        self._flush_log()

        if status == "done":
            self._bubble("agent", "Done.")
            self.tray.setIcon(_make_tray_icon(DOT_DONE))
            if not self.isVisible():
                self.tray.showMessage("Worker Buddy", "Task complete.", QSystemTrayIcon.Information, 4000)
        elif status == "stopped":
            self._bubble("status", "Stopped.")
            self.tray.setIcon(_make_tray_icon(ACCENT_SEND))
        else:
            msg = status.removeprefix("error: ")
            self._bubble("agent", f"Error: {msg}")
            self.tray.setIcon(_make_tray_icon(DOT_ERROR))
            if not self.isVisible():
                self.tray.showMessage("Worker Buddy", f"Error: {msg}", QSystemTrayIcon.Warning, 6000)

        self._set_idle()

    def _set_idle(self):
        self.send_btn.setText("Send")
        self.send_btn.setStyleSheet(self._send_css(ACCENT_SEND))
        self.task_input.setEnabled(True)
        self.attach_btn.setEnabled(True)
        self.header.set_status("idle")
        self.tray.setIcon(_make_tray_icon(ACCENT_SEND))

    def _set_running(self):
        self.send_btn.setText("Stop")
        self.send_btn.setStyleSheet(self._send_css(ACCENT_STOP))
        self.task_input.setEnabled(False)
        self.attach_btn.setEnabled(False)
        self.header.set_status("running...")
        self.tray.setIcon(_make_tray_icon(DOT_RUNNING))

    # ── Quit ──────────────────────────────────────────────────────────────────
    def confirm_quit(self):
        if self._agent_thread and self._agent_thread.isRunning():
            box = QMessageBox(self)
            box.setWindowTitle("Worker Buddy")
            box.setText("Agent is running. Quit anyway?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec_() != QMessageBox.Yes:
                return
            self._agent_thread.terminate()
        self._save_settings()
        QApplication.quit()

    # ── Drag window (body area) ───────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag_pos = (
            e.globalPos() - self.frameGeometry().topLeft()
            if e.button() == Qt.LeftButton else None
        )

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(e.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.width() - self.grip.width() - 4,
                       self.height() - self.grip.height() - 4)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Enable HiDPI before creating the app
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 13))

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
