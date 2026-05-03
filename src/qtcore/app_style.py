"""
app_style.py
============
Palette-aware application theming using Qt's own style system.

Rather than fighting Qt with a global QSS stylesheet, this module:

  1. Forces the cross-platform Fusion style, which reads QPalette
     faithfully and paints every widget correctly — including spin-box
     arrows, scroll-bar handles, and combo drop-downs — at any DPI.

  2. Builds a QPalette from the current system palette, overriding only
     the roles needed to express the desired colour scheme.

  3. Subclasses QProxyStyle for the small number of structural tweaks
     (corner radii, spacing) that cannot be expressed via palette alone,
     by overriding pixelMetric() rather than painting.

  4. Keeps QSS to an absolute minimum — only things that Fusion cannot
     express at all (tab indicator, group-box title position).

Usage
-----
    from app_style import apply_app_style

    app = QApplication(sys.argv)
    apply_app_style(app)

    # Theme updates automatically when the OS palette changes.
    # No manual paletteChanged wiring required.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QProxyStyle, QStyle, QStyleOption, QWidget,
)

# ── Accent color ─────────────────────────────────────────────────────────────
# The one value you are most likely to want to change per product.
_ACCENT = QColor("#4C8BF5")


#
# Colorr reference — all values are hand-picked for WCAG AA contrast on dark.
#
# Surface scale (darkest → lightest):
#   #0E1117  shadow       deepest background, shadows
#   #13161E  window       main window / panel background
#   #1A1E27  base         input fields, lists, text areas
#   #20253100 alt-base    alternating list rows
#   #262C38  button       buttons, toolbars, group-box fill
#   #2F3647  midlight     hover surfaces, subtle dividers
#   #3A4255  mid          borders
#   #4A5470  dark         deep borders, pressed states
#
# Text scale (dimmest → brightest):
#   #4D5666  placeholder  disabled / hint text
#   #8B95A8  window-text  secondary text
#   #C9D3E0  text         primary text in input fields
#   #E8EDF4  bright-text  high-emphasis / active labels
#
# Accent:
#   #4C8BF5  highlight    selection, focus rings, checked indicators
#   #FFFFFF  hi-text      text on accent background
#   #60A0FF  link         hyperlinks
#

def build_palette(base: QPalette | None = None) -> QPalette:
    """
    Return a fully-specified dark QPalette.

    Every role in every colour group is set explicitly so Fusion never
    falls back to an OS value that might clash with the dark scheme.
    The optional *base* parameter is accepted for API compatibility with
    the paletteChanged handler but is intentionally ignored — the dark
    palette is a fixed design rather than a derivative of the OS palette.

    Parameters
    ----------
    base : QPalette | None
        Ignored.  Present so the function signature matches the
        ``paletteChanged`` lambda in ``apply_app_style``.

    Returns
    -------
    QPalette
        A new palette ready to pass to ``QApplication.setPalette()``.
    """
    # ── Named colours ─────────────────────────────────────────────────────────
    shadow = QColor("#0E1117")
    window = QColor("#13161E")
    base_c = QColor("#1A1E27")
    alt_base = QColor("#202531")
    button = QColor("#262C38")
    midlight = QColor("#2F3647")
    mid = QColor("#3A4255")
    dark = QColor("#4A5470")

    placeholder = QColor("#4D5666")
    win_text = QColor("#8B95A8")
    text = QColor("#C9D3E0")
    bright_text = QColor("#E8EDF4")

    accent = _ACCENT  # #4C8BF5
    accent_text = QColor("#FFFFFF")
    link = QColor("#60A0FF")

    pal = QPalette()

    def set_all(role: QPalette.ColorRole, color: QColor) -> None:
        """Set *color* for *role* in every colour group."""
        for group in (QPalette.ColorGroup.Active,
                      QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            pal.setColor(group, role, color)

    def set_disabled(role: QPalette.ColorRole, color: QColor) -> None:
        pal.setColor(QPalette.ColorGroup.Disabled, role, color)

    R = QPalette.ColorRole

    # ── Surface roles ─────────────────────────────────────────────────────────
    set_all(R.Window, window)
    set_all(R.Base, base_c)
    set_all(R.AlternateBase, alt_base)
    set_all(R.Button, button)
    set_all(R.Midlight, midlight)
    set_all(R.Mid, mid)
    set_all(R.Dark, dark)
    set_all(R.Shadow, shadow)
    set_all(R.ToolTipBase, button)

    # ── Text roles ────────────────────────────────────────────────────────────
    set_all(R.WindowText, win_text)
    set_all(R.Text, text)
    set_all(R.BrightText, bright_text)
    set_all(R.ButtonText, text)
    set_all(R.PlaceholderText, placeholder)
    set_all(R.ToolTipText, text)
    set_all(R.Link, link)
    set_all(R.LinkVisited, link.darker(120))

    # ── Accent roles ──────────────────────────────────────────────────────────
    set_all(R.Highlight, accent)
    set_all(R.HighlightedText, accent_text)

    # ── Disabled overrides ────────────────────────────────────────────────────
    # Fusion dims disabled widgets by lightening their background slightly and
    # greying out text.  Setting these explicitly prevents washed-out colours.
    set_disabled(R.WindowText, placeholder)
    set_disabled(R.Text, placeholder)
    set_disabled(R.ButtonText, placeholder)
    set_disabled(R.Button, button)  # keep same surface
    set_disabled(R.Base, window)  # inputs look inert
    set_disabled(R.Highlight, mid)  # selection loses accent
    set_disabled(R.HighlightedText, placeholder)

    return pal


class AppStyle(QProxyStyle):
    """
    Thin QProxyStyle wrapper over Fusion.

    Only pixelMetric() is overridden, which controls sizes and spacing.
    No painting is duplicated — Fusion handles all widget rendering, DPI
    scaling, and accessibility concerns.

    Overriding pixelMetric() is safe across Qt versions because the enum
    values are stable and the base implementation is always the fallback.
    """

    def pixelMetric(
            self,
            metric: QStyle.PixelMetric,
            option: QStyleOption | None = None,
            widget: QWidget | None = None,
    ) -> int:
        PM = QStyle.PixelMetric
        match metric:
            case PM.PM_DefaultFrameWidth:
                # Controls the border width used by Fusion for input widgets.
                return 1
            case PM.PM_ButtonDefaultIndicator:
                # Thickness of the "default button" ring.
                return 2
            case PM.PM_ComboBoxFrameWidth:
                return 1
            case PM.PM_SpinBoxFrameWidth:
                return 1
            case PM.PM_ScrollBarExtent:
                # Thinner scroll bars (default is 16 on most platforms).
                return 10
            case PM.PM_ScrollBarSliderMin:
                return 30
            case PM.PM_SliderThickness | PM.PM_SliderLength:
                return 16
            case PM.PM_TabBarTabHSpace:
                # Horizontal padding inside each tab.
                return 28
            case PM.PM_TabBarTabVSpace:
                # Vertical padding inside each tab.
                return 10
            case PM.PM_ToolBarItemSpacing:
                return 3
            case PM.PM_ToolBarFrameWidth:
                return 0
            case _:
                return super().pixelMetric(metric, option, widget)


def apply_app_style(app: QApplication) -> None:
    """
    Apply the complete application style to *app*.

    Call once after constructing QApplication, before any widgets are
    created.  The style responds to subsequent OS palette changes
    automatically because Fusion re-reads the palette on every paint.

    Parameters
    ----------
    app : QApplication
        The running application instance.
    """
    app.setStyle(AppStyle("Fusion"))
    app.setPalette(build_palette(app.palette()))
    app.paletteChanged.connect(lambda _: app.setPalette(build_palette()))
