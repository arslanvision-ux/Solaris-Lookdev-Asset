"""
Qt stylesheet constants for the Asset Manager UI.
Dark-theme design consistent with Houdini's native look.
Targets PySide6 (Houdini 20+).
"""

# ── Color Palette ──
COLORS = {
    "bg_darkest":    "#1c1c1c",
    "bg_dark":       "#222222",
    "bg_medium":     "#2a2a2a",
    "bg_light":      "#323232",
    "bg_lighter":    "#3c3c3c",
    "surface":       "#262626",
    "surface_hover": "#2e2e2e",
    "surface_press": "#1e1e1e",
    "accent":        "#d4853a",
    "accent_hover":  "#e09550",
    "accent_press":  "#b8702e",
    "accent_soft":   "rgba(212, 133, 58, 0.15)",
    "text_primary":  "#e8e8e8",
    "text_secondary":"#9a9a9a",
    "text_dim":      "#666666",
    "border":        "#383838",
    "border_focus":  "#d4853a",
    "error":         "#e74c3c",
    "warning":       "#f39c12",
    "success":       "#2ecc71",
    "info":          "#3498db",
    "badge_ready":   "#2ecc71",
    "badge_pending": "#f39c12",
    "badge_error":   "#e74c3c",
    "badge_process": "#3498db",
    "scrollbar_bg":  "#1c1c1c",
    "scrollbar_fg":  "#484848",
}

# ── Main Stylesheet ──
MAIN_STYLESHEET = f"""

/* ── Scoped to Main Panel ── */
#AssetManagerMain {{
    background-color: {COLORS['bg_darkest']};
    color: {COLORS['text_primary']};
    font-family: "Inter", "Segoe UI", "Roboto", sans-serif;
    font-size: 12px;
}}

#AssetManagerMain QWidget {{
    background-color: transparent;
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    background-color: {COLORS['bg_dark']};
    margin-top: -1px;
}}

QTabBar::tab {{
    background-color: {COLORS['bg_medium']};
    color: {COLORS['text_secondary']};
    border: 1px solid {COLORS['border']};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 20px;
    margin-right: 2px;
    font-weight: 500;
}}

QTabBar::tab:selected {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['accent']};
    border-bottom: 2px solid {COLORS['accent']};
}}

QTabBar::tab:hover:!selected {{
    background-color: {COLORS['surface_hover']};
    color: {COLORS['text_primary']};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {COLORS['bg_light']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 7px 16px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {COLORS['surface_hover']};
    border-color: {COLORS['accent']};
}}

QPushButton:pressed {{
    background-color: {COLORS['surface_press']};
}}

QPushButton:disabled {{
    color: {COLORS['text_dim']};
    background-color: {COLORS['bg_darkest']};
    border-color: {COLORS['bg_medium']};
}}

QPushButton#primaryButton {{
    background-color: {COLORS['accent']};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background-color: {COLORS['accent_hover']};
}}

QPushButton#primaryButton:pressed {{
    background-color: {COLORS['accent_press']};
}}

/* ── Line Edit ── */
QLineEdit {{
    background-color: {COLORS['surface']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 6px 10px;
    selection-background-color: {COLORS['accent']};
}}

QLineEdit:focus {{
    border-color: {COLORS['border_focus']};
}}

/* ── Combo Box ── */
QComboBox {{
    background-color: {COLORS['surface']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 6px 10px;
}}

QComboBox:hover {{
    border-color: {COLORS['accent']};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS['bg_medium']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent_soft']};
    selection-color: {COLORS['accent']};
}}

/* ── Spin Box / Slider ── */
QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['surface']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 4px 8px;
}}

QSlider::groove:horizontal {{
    background-color: {COLORS['bg_medium']};
    height: 6px;
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background-color: {COLORS['accent']};
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}}

QSlider::sub-page:horizontal {{
    background-color: {COLORS['accent']};
    border-radius: 3px;
}}

/* ── Progress Bar ── */
QProgressBar {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    text-align: center;
    color: {COLORS['text_primary']};
    height: 20px;
}}

QProgressBar::chunk {{
    background-color: {COLORS['accent']};
    border-radius: 4px;
}}

/* ── Table / Tree ── */
QTableWidget, QTreeWidget {{
    background-color: {COLORS['bg_dark']};
    alternate-background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    gridline-color: {COLORS['border']};
}}

QTableWidget::item, QTreeWidget::item {{
    padding: 4px 8px;
}}

QTableWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {COLORS['accent_soft']};
    color: {COLORS['accent']};
}}

QHeaderView::section {{
    background-color: {COLORS['bg_medium']};
    color: {COLORS['text_secondary']};
    border: 1px solid {COLORS['border']};
    padding: 6px 10px;
    font-weight: 600;
}}

/* ── Scroll Bar ── */
QScrollBar:vertical {{
    background-color: {COLORS['scrollbar_bg']};
    width: 10px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background-color: {COLORS['scrollbar_fg']};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLORS['accent']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: {COLORS['scrollbar_bg']};
    height: 10px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background-color: {COLORS['scrollbar_fg']};
    border-radius: 5px;
    min-width: 30px;
}}

/* ── Group Box ── */
QGroupBox {{
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 600;
    color: {COLORS['text_secondary']};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}

/* ── Label ── */
QLabel {{
    background-color: transparent;
}}

QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {COLORS['text_primary']};
    padding-bottom: 4px;
}}

QLabel#dimLabel {{
    color: {COLORS['text_dim']};
    font-size: 11px;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {COLORS['border']};
}}

/* ── Tool Tip ── */
QToolTip {{
    background-color: {COLORS['bg_medium']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['accent']};
    border-radius: 4px;
    padding: 4px 8px;
}}
"""


# ── Thumbnail Card Styles ──
THUMBNAIL_CARD_STYLE = f"""
QFrame#thumbnailCard {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}

QFrame#thumbnailCard:hover {{
    border-color: {COLORS['accent']};
    background-color: {COLORS['surface_hover']};
}}

QLabel#thumbnailImage {{
    border-radius: 6px;
    background-color: {COLORS['bg_darkest']};
}}

QLabel#assetName {{
    font-weight: 600;
    font-size: 11px;
    color: {COLORS['text_primary']};
}}

QLabel#statusBadge {{
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 8px;
}}
"""


def get_status_badge_color(status: str) -> str:
    """Return the background color for a status badge."""
    return {
        "ready":      COLORS["badge_ready"],
        "pending":    COLORS["badge_pending"],
        "processing": COLORS["badge_process"],
        "error":      COLORS["badge_error"],
    }.get(status, COLORS["text_dim"])
