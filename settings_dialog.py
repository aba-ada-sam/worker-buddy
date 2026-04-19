from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QSlider, QLineEdit, QPushButton, QComboBox, QFileDialog,
    QFrame, QWidget, QGraphicsDropShadowEffect, QSpinBox
)
from PyQt5.QtCore import Qt, QSettings, QPoint
from PyQt5.QtGui import QColor

BG_MAIN      = "#18181c"
BG_INPUT     = "#222228"
BORDER_IDLE  = "#2e2e38"
BORDER_FOCUS = "#5b5bd6"
ACCENT_RUN   = "#5b5bd6"
TEXT_PRIMARY = "#ededf0"
TEXT_DIM     = "#6e6e80"

# (model_id, label shown in dropdown). "(desktop)" tags are hints only; the
# runtime does the real validation and falls back if needed.
MODELS = [
    ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5  (browser + desktop)"),
    ("claude-opus-4-7",            "Claude Opus 4.7  (browser only)"),
    ("claude-opus-4-6",            "Claude Opus 4.6  (browser only)"),
    ("claude-haiku-4-5-20251001",  "Claude Haiku 4.5  (browser only, fastest)"),
]

_DIALOG_STYLE = f"""
QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI';
    font-size: 13px;
}}
QLineEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_IDLE};
    border-radius: 8px;
    padding: 6px 8px;
}}
QLineEdit:focus {{
    border: 1px solid {BORDER_FOCUS};
}}
QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_IDLE};
    border-radius: 8px;
    padding: 6px 8px;
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_IDLE};
    selection-background-color: #2e2e50;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_IDLE};
    border-radius: 4px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_RUN};
    border: 1px solid {ACCENT_RUN};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER_IDLE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT_RUN};
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -5px 0;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT_RUN};
    border-radius: 2px;
}}
QPushButton#save_btn {{
    background-color: {ACCENT_RUN};
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: bold;
}}
QPushButton#save_btn:hover {{
    background-color: #6c6ce0;
}}
QPushButton#cancel_btn, QPushButton#browse_btn, QPushButton#reset_btn {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_IDLE};
    border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton#cancel_btn:hover, QPushButton#browse_btn:hover, QPushButton#reset_btn:hover {{
    border: 1px solid {BORDER_FOCUS};
}}
QPushButton#close_btn {{
    background: none;
    border: none;
    color: {TEXT_DIM};
    font-size: 13px;
}}
QPushButton#close_btn:hover {{
    color: {TEXT_PRIMARY};
}}
QSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_IDLE};
    border-radius: 8px;
    padding: 4px 8px;
}}
QSpinBox:focus {{
    border: 1px solid {BORDER_FOCUS};
}}
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.settings = QSettings("LynnCove", "WorkerBuddy")
        self._drag_pos = None
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        container = QWidget()
        container.setObjectName("dlg_container")
        container.setStyleSheet(f"""
            QWidget#dlg_container {{
                background-color: {BG_MAIN};
                border-radius: 16px;
                border: 1px solid {BORDER_IDLE};
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 120))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(0)

        # Title bar
        title_row = QHBoxLayout()
        title_row.setContentsMargins(16, 12, 10, 12)
        title_lbl = QLabel("Settings")
        title_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold; background: none; border: none;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedSize(18, 18)
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {BORDER_IDLE}; border: none;")
        layout.addWidget(sep)

        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 0)
        body.setSpacing(14)
        layout.addLayout(body)

        body.setSpacing(12)
        body.addSpacing(2)

        # Always on top
        self.aot_cb = QCheckBox("Always on Top")
        self.aot_cb.setStyleSheet(f"color: {TEXT_PRIMARY}; background: none; border: none;")
        body.addWidget(self.aot_cb)

        # Opacity
        op_row = QHBoxLayout()
        op_lbl = QLabel("Opacity")
        op_lbl.setStyleSheet(f"color: {TEXT_DIM}; background: none; border: none;")
        op_row.addWidget(op_lbl)
        self.op_val_lbl = QLabel("100%")
        self.op_val_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; background: none; border: none;")
        op_row.addStretch()
        op_row.addWidget(self.op_val_lbl)
        body.addLayout(op_row)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(55, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(lambda v: self.op_val_lbl.setText(f"{v}%"))
        body.addWidget(self.opacity_slider)

        # Show browser
        self.browser_cb = QCheckBox("Show agent browser window")
        self.browser_cb.setStyleSheet(f"color: {TEXT_PRIMARY}; background: none; border: none;")
        body.addWidget(self.browser_cb)

        # Credentials path
        creds_lbl = QLabel("Credentials file")
        creds_lbl.setStyleSheet(f"color: {TEXT_DIM}; background: none; border: none;")
        body.addWidget(creds_lbl)

        creds_row = QHBoxLayout()
        self.creds_edit = QLineEdit()
        self.creds_edit.setPlaceholderText(r"C:\JSON Credentials\QB_WC_credentials.json")
        creds_row.addWidget(self.creds_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_creds)
        creds_row.addWidget(browse_btn)
        body.addLayout(creds_row)

        # Model
        model_lbl = QLabel("Model")
        model_lbl.setStyleSheet(f"color: {TEXT_DIM}; background: none; border: none;")
        body.addWidget(model_lbl)

        self.model_combo = QComboBox()
        for model_id, label in MODELS:
            # Store the raw id as userData; show the friendly label in the list.
            self.model_combo.addItem(label, userData=model_id)
        body.addWidget(self.model_combo)

        # Desktop mode max steps
        steps_lbl = QLabel("Desktop mode: max steps per task")
        steps_lbl.setStyleSheet(f"color: {TEXT_DIM}; background: none; border: none;")
        body.addWidget(steps_lbl)

        steps_row = QHBoxLayout()
        self.max_steps_spin = QSpinBox()
        self.max_steps_spin.setRange(5, 300)
        self.max_steps_spin.setSingleStep(5)
        self.max_steps_spin.setValue(60)
        self.max_steps_spin.setFixedWidth(90)
        steps_row.addWidget(self.max_steps_spin)
        steps_hint = QLabel("Runaway guard. Stop button still works at any time.")
        steps_hint.setStyleSheet(f"color: {TEXT_DIM}; background: none; border: none; font-size: 11px;")
        steps_row.addWidget(steps_hint, 1)
        body.addLayout(steps_row)

        # Reset position
        reset_btn = QPushButton("Reset Window to Corner")
        reset_btn.setObjectName("reset_btn")
        reset_btn.clicked.connect(self._reset_position)
        body.addWidget(reset_btn)

        # Buttons
        body.addSpacing(4)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel_btn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("save_btn")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        body.addLayout(btn_row)

        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(360)

    def _load_values(self):
        self.aot_cb.setChecked(self.settings.value("always_on_top", True, type=bool))
        opacity_pct = int(self.settings.value("opacity", 1.0, type=float) * 100)
        self.opacity_slider.setValue(opacity_pct)
        self.browser_cb.setChecked(self.settings.value("show_browser", True, type=bool))
        self.creds_edit.setText(
            self.settings.value("creds_path", r"C:\JSON Credentials\QB_WC_credentials.json")
        )
        model = self.settings.value("model", "claude-sonnet-4-5-20250929")
        idx = self.model_combo.findData(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.max_steps_spin.setValue(int(self.settings.value("desktop_max_steps", 60)))

    def _save(self):
        self.settings.setValue("always_on_top", self.aot_cb.isChecked())
        self.settings.setValue("opacity", self.opacity_slider.value() / 100.0)
        self.settings.setValue("show_browser", self.browser_cb.isChecked())
        creds = self.creds_edit.text().strip()
        if creds:
            self.settings.setValue("creds_path", creds)
        self.settings.setValue("model", self.model_combo.currentData())
        self.settings.setValue("desktop_max_steps", self.max_steps_spin.value())

        if self.parent():
            self.parent().set_always_on_top(self.aot_cb.isChecked())
            self.parent().set_opacity(self.opacity_slider.value() / 100.0)
            if hasattr(self.parent(), "aot_action"):
                self.parent().aot_action.setChecked(self.aot_cb.isChecked())

        self.accept()

    def _browse_creds(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Credentials File", "", "JSON Files (*.json);;All Files (*)"
        )
        if path:
            self.creds_edit.setText(path)

    def _reset_position(self):
        if self.parent():
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().geometry()
            pw = self.parent()
            pw.move(screen.width() - pw.width() - 20, screen.height() - pw.height() - 60)
        self.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            pg = self.parent().geometry()
            self.adjustSize()
            x = pg.x() + (pg.width() - self.width()) // 2
            y = pg.y() + (pg.height() - self.height()) // 2
            self.move(x, y)
