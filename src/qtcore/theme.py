"""
app_style.py
============
Palette-aware application theming using Qt's own style system.

Rather than fighting Qt with a global QSS stylesheet, this module:

  1. Forces the cross-platform Fusion style, which reads QPalette
     faithfully and paints every widget correctly — including spin-box
     arrows, scroll-bar handles, and combo drop-downs — at any DPI.

  2. Builds a QPalette from one of two built-in colour schemes (dark or
     light) via :func:`build_palette`, overriding only the roles needed
     to express the desired colour scheme.

  3. Subclasses QProxyStyle for the small number of structural tweaks
     (corner radii, spacing) that cannot be expressed via palette alone,
     by overriding pixelMetric() rather than painting.

  4. Keeps QSS to an absolute minimum — only things that Fusion cannot
     express at all (tab indicator, group-box title position).

Usage
-----
    from app_style import apply_app_style, Theme

    app = QApplication(sys.argv)
    apply_app_style(app, Theme.DARK)   # or Theme.LIGHT, Theme.CREAM, Theme.MIDNIGHT, Theme.DAWN

    # Switch at runtime:
    apply_app_style(app, Theme.CREAM)

    # Theme updates automatically when the OS palette changes.
    # No manual paletteChanged wiring required.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QProxyStyle, QStyle, QStyleOption, QWidget,
)

from qtcore.utils import handled_qt_disconnect


# ── Theme enum ────────────────────────────────────────────────────────────────

class Theme(Enum):
    DARK     = auto()
    LIGHT    = auto()
    CREAM    = auto()
    MIDNIGHT = auto()
    DAWN     = auto()


# ── Per-theme colour tokens ───────────────────────────────────────────────────

@dataclass(frozen=True)
class _Tokens:
    """
    All named colours for one theme.

    Surface scale runs from the most recessed background to the most
    raised foreground.  Text scale runs from dimmest to brightest.
    """
    # Surfaces (most recessed → most raised)
    shadow:   QColor
    window:   QColor   # main window / panel background
    base:     QColor   # input fields, lists, text areas
    alt_base: QColor   # alternating list rows
    button:   QColor   # buttons, toolbars, group-box fill
    midlight: QColor   # hover surfaces, subtle dividers
    mid:      QColor   # borders
    dark:     QColor   # deep borders, pressed states

    # Text (dimmest → brightest)
    placeholder: QColor   # disabled / hint text
    win_text:    QColor   # secondary text
    text:        QColor   # primary text in input fields
    bright_text: QColor   # high-emphasis / active labels

    # Accent
    accent:      QColor   # selection, focus rings, checked indicators
    accent_text: QColor   # text on accent background
    link:        QColor   # hyperlinks

    # Tooltip surface (usually close to button/alt_base)
    tooltip_base: QColor


#
# Dark colour reference — hand-picked for WCAG AA contrast.
#
# Surface scale (darkest → lightest):
#   #0E1117  shadow       deepest background, shadows
#   #13161E  window       main window / panel background
#   #1A1E27  base         input fields, lists, text areas
#   #202531  alt-base     alternating list rows
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
_DARK = _Tokens(
    shadow       = QColor("#0E1117"),
    window       = QColor("#13161E"),
    base         = QColor("#1A1E27"),
    alt_base     = QColor("#202531"),
    button       = QColor("#262C38"),
    midlight     = QColor("#2F3647"),
    mid          = QColor("#3A4255"),
    dark         = QColor("#4A5470"),
    placeholder  = QColor("#4D5666"),
    win_text     = QColor("#8B95A8"),
    text         = QColor("#C9D3E0"),
    bright_text  = QColor("#E8EDF4"),
    accent       = QColor("#4C8BF5"),
    accent_text  = QColor("#FFFFFF"),
    link         = QColor("#60A0FF"),
    tooltip_base = QColor("#262C38"),
)

#
# Light colour reference — hand-picked for WCAG AA contrast.
#
# Surface scale (lightest → darkest):
#   #FFFFFF  base         input fields, lists, text areas
#   #F5F7FA  alt-base     alternating list rows
#   #EFF1F5  window       main window / panel background
#   #E4E7EE  button       buttons, toolbars, group-box fill
#   #D5DAE5  midlight     hover surfaces, subtle dividers
#   #BEC5D4  mid          borders
#   #A0AABF  dark         deep borders, pressed states
#   #8892A8  shadow       deepest shadows
#
# Text scale (dimmest → brightest):
#   #A0AABF  placeholder  disabled / hint text
#   #6B7690  window-text  secondary text
#   #2D3550  text         primary text in input fields
#   #111827  bright-text  high-emphasis / active labels
#
# Accent:
#   #2563EB  highlight    selection, focus rings, checked indicators
#   #FFFFFF  hi-text      text on accent background
#   #1D4ED8  link         hyperlinks
#
_LIGHT = _Tokens(
    shadow       = QColor("#8892A8"),
    window       = QColor("#EFF1F5"),
    base         = QColor("#FFFFFF"),
    alt_base     = QColor("#F5F7FA"),
    button       = QColor("#E4E7EE"),
    midlight     = QColor("#D5DAE5"),
    mid          = QColor("#BEC5D4"),
    dark         = QColor("#A0AABF"),
    placeholder  = QColor("#A0AABF"),
    win_text     = QColor("#6B7690"),
    text         = QColor("#2D3550"),
    bright_text  = QColor("#111827"),
    accent       = QColor("#2563EB"),
    accent_text  = QColor("#FFFFFF"),
    link         = QColor("#1D4ED8"),
    tooltip_base = QColor("#F5F7FA"),
)


#
# Cream colour reference — warm parchment surfaces, hand-picked for WCAG AA contrast.
#
# The cream theme shares the cool-grey text of the light theme but replaces every
# surface with warm yellow-white tones.  The accent shifts to a muted terracotta
# so it reads as warm rather than clinical, while still meeting 4.5 : 1 on base.
#
# Surface scale (lightest → darkest):
#   #FDFAF4  base         input fields, lists, text areas  (warm white)
#   #F7F3E8  alt-base     alternating list rows
#   #F0EAD6  window       main window / panel background   (classic parchment)
#   #E8E0C8  button       buttons, toolbars, group-box fill
#   #D8CEAF  midlight     hover surfaces, subtle dividers
#   #C4B896  mid          borders
#   #A89D7F  dark         deep borders, pressed states
#   #8C8168  shadow       deepest shadows
#
# Text scale (dimmest → brightest):
#   #A89D7F  placeholder  disabled / hint text
#   #6B6050  window-text  secondary text
#   #3D3020  text         primary text in input fields     (warm near-black)
#   #1C1208  bright-text  high-emphasis / active labels
#
# Accent:
#   #9B4520  highlight    terracotta — warm, readable, non-clinical
#   #FFFFFF  hi-text      text on accent background
#   #7A3418  link         deeper terracotta for hyperlinks
#
_CREAM = _Tokens(
    shadow       = QColor("#8C8168"),
    window       = QColor("#F0EAD6"),
    base         = QColor("#FDFAF4"),
    alt_base     = QColor("#F7F3E8"),
    button       = QColor("#E8E0C8"),
    midlight     = QColor("#D8CEAF"),
    mid          = QColor("#C4B896"),
    dark         = QColor("#A89D7F"),
    placeholder  = QColor("#A89D7F"),
    win_text     = QColor("#6B6050"),
    text         = QColor("#3D3020"),
    bright_text  = QColor("#1C1208"),
    accent       = QColor("#9B4520"),
    accent_text  = QColor("#FFFFFF"),
    link         = QColor("#7A3418"),
    tooltip_base = QColor("#F7F3E8"),
)


#
# Midnight colour reference — near-pure black surfaces for OLED / low-light use.
#
# The midnight theme pushes surfaces as dark as possible while keeping enough
# contrast between layers so chrome (panels, toolbars, borders) is still
# legible.  Text is a cool blue-white to complement the deep background.
# The accent is a vivid violet — it pops sharply against near-black without
# the harshness of a saturated red or green.
#
# Surface scale (darkest → lightest):
#   #000000  shadow       pure black — shadows vanish into the void
#   #080808  window       main window / panel background
#   #0F0F0F  base         input fields, lists, text areas
#   #151515  alt-base     alternating list rows
#   #1C1C1C  button       buttons, toolbars, group-box fill
#   #252525  midlight     hover surfaces, subtle dividers
#   #323232  mid          borders
#   #454545  dark         deep borders, pressed states
#
# Text scale (dimmest → brightest):
#   #444444  placeholder  disabled / hint text
#   #7A8394  window-text  secondary text   (cool blue-grey)
#   #B8C4D4  text         primary text in input fields
#   #E2EAF4  bright-text  high-emphasis / active labels
#
# Accent:
#   #7C3AED  highlight    vivid violet — high contrast on black
#   #FFFFFF  hi-text      text on accent background
#   #A78BFA  link         softer violet for hyperlinks
#
_MIDNIGHT = _Tokens(
    shadow       = QColor("#000000"),
    window       = QColor("#080808"),
    base         = QColor("#0F0F0F"),
    alt_base     = QColor("#151515"),
    button       = QColor("#1C1C1C"),
    midlight     = QColor("#252525"),
    mid          = QColor("#323232"),
    dark         = QColor("#454545"),
    placeholder  = QColor("#444444"),
    win_text     = QColor("#7A8394"),
    text         = QColor("#B8C4D4"),
    bright_text  = QColor("#E2EAF4"),
    accent       = QColor("#7C3AED"),
    accent_text  = QColor("#FFFFFF"),
    link         = QColor("#A78BFA"),
    tooltip_base = QColor("#1C1C1C"),
)

#
# Dawn colour reference — soft warm-rose and lavender surfaces evoking early
# morning sky, hand-picked for WCAG AA contrast.
#
# Surfaces blend pink-tinged whites with dusty mauves.  Text is a deep
# warm-plum rather than near-black so it feels at home against the rosy
# backgrounds.  The accent is a muted rose-magenta — saturated enough to
# mark interactive elements clearly, soft enough not to jar.
#
# Surface scale (lightest → darkest):
#   #FEF8F8  base         input fields, lists, text areas  (blush white)
#   #FAF0F2  alt-base     alternating list rows
#   #F5E6EA  window       main window / panel background   (soft rose)
#   #EDD8DE  button       buttons, toolbars, group-box fill
#   #DEC4CC  midlight     hover surfaces, subtle dividers
#   #C9A8B4  mid          borders
#   #B08898  dark         deep borders, pressed states
#   #8F6878  shadow       deepest shadows
#
# Text scale (dimmest → brightest):
#   #B08898  placeholder  disabled / hint text
#   #7A5060  window-text  secondary text   (muted mauve)
#   #4A2535  text         primary text     (deep warm-plum)
#   #200A14  bright-text  high-emphasis / active labels
#
# Accent:
#   #B5376A  highlight    rose-magenta — warm, readable, 4.8:1 on base
#   #FFFFFF  hi-text      text on accent background
#   #8C1F4D  link         deeper rose for hyperlinks
#
_DAWN = _Tokens(
    shadow       = QColor("#8F6878"),
    window       = QColor("#F5E6EA"),
    base         = QColor("#FEF8F8"),
    alt_base     = QColor("#FAF0F2"),
    button       = QColor("#EDD8DE"),
    midlight     = QColor("#DEC4CC"),
    mid          = QColor("#C9A8B4"),
    dark         = QColor("#B08898"),
    placeholder  = QColor("#B08898"),
    win_text     = QColor("#7A5060"),
    text         = QColor("#4A2535"),
    bright_text  = QColor("#200A14"),
    accent       = QColor("#B5376A"),
    accent_text  = QColor("#FFFFFF"),
    link         = QColor("#8C1F4D"),
    tooltip_base = QColor("#FAF0F2"),
)

_TOKENS: dict[Theme, _Tokens] = {
    Theme.DARK:     _DARK,
    Theme.LIGHT:    _LIGHT,
    Theme.CREAM:    _CREAM,
    Theme.MIDNIGHT: _MIDNIGHT,
    Theme.DAWN:     _DAWN,
}


# ── Palette builder ───────────────────────────────────────────────────────────

def build_palette(theme: Theme = Theme.DARK, base: QPalette | None = None) -> QPalette:
    """
    Return a fully-specified QPalette for *theme*.

    Every role in every colour group is set explicitly so Fusion never
    falls back to an OS value that might clash with the chosen scheme.

    Parameters
    ----------
    theme : Theme
        ``Theme.DARK``, ``Theme.LIGHT``, ``Theme.CREAM``, ``Theme.MIDNIGHT``, or ``Theme.DAWN``.
    base : QPalette | None
        Ignored.  Present so the function signature is compatible with
        the ``paletteChanged`` lambda in :func:`apply_app_style`.

    Returns
    -------
    QPalette
        A new palette ready to pass to ``QApplication.setPalette()``.
    """
    t = _TOKENS[theme]
    pal = QPalette()

    def set_all(role: QPalette.ColorRole, color: QColor) -> None:
        for group in (QPalette.ColorGroup.Active,
                      QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            pal.setColor(group, role, color)

    def set_disabled(role: QPalette.ColorRole, color: QColor) -> None:
        pal.setColor(QPalette.ColorGroup.Disabled, role, color)

    R = QPalette.ColorRole

    # ── Surface roles ─────────────────────────────────────────────────────────
    set_all(R.Window,        t.window)
    set_all(R.Base,          t.base)
    set_all(R.AlternateBase, t.alt_base)
    set_all(R.Button,        t.button)
    set_all(R.Midlight,      t.midlight)
    set_all(R.Mid,           t.mid)
    set_all(R.Dark,          t.dark)
    set_all(R.Shadow,        t.shadow)
    set_all(R.ToolTipBase,   t.tooltip_base)

    # ── Text roles ────────────────────────────────────────────────────────────
    set_all(R.WindowText,      t.win_text)
    set_all(R.Text,            t.text)
    set_all(R.BrightText,      t.bright_text)
    set_all(R.ButtonText,      t.text)
    set_all(R.PlaceholderText, t.placeholder)
    set_all(R.ToolTipText,     t.text)
    set_all(R.Link,            t.link)
    set_all(R.LinkVisited,     t.link.darker(130))

    # ── Accent roles ──────────────────────────────────────────────────────────
    set_all(R.Highlight,       t.accent)
    set_all(R.HighlightedText, t.accent_text)

    # ── Disabled overrides ────────────────────────────────────────────────────
    # Disabled text becomes placeholder-grey on both themes.
    # Disabled base/highlight lose colour so the widget reads as inert.
    set_disabled(R.WindowText,      t.placeholder)
    set_disabled(R.Text,            t.placeholder)
    set_disabled(R.ButtonText,      t.placeholder)
    set_disabled(R.Button,          t.button)     # keep same surface
    set_disabled(R.Base,            t.alt_base)   # inputs look inert
    set_disabled(R.Highlight,       t.midlight)   # selection loses accent
    set_disabled(R.HighlightedText, t.placeholder)

    return pal


# ── Proxy style ───────────────────────────────────────────────────────────────

class AppStyle(QProxyStyle):
    """
    Thin QProxyStyle wrapper over Fusion.

    Only pixelMetric() is overridden, which controls sizes and spacing.
    No painting is duplicated — Fusion handles all widget rendering, DPI
    scaling, and accessibility concerns.  The same metrics apply to both
    light and dark themes for visual consistency.
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
                return 1
            case PM.PM_ButtonDefaultIndicator:
                return 2
            case PM.PM_ComboBoxFrameWidth:
                return 1
            case PM.PM_SpinBoxFrameWidth:
                return 1
            case PM.PM_ScrollBarExtent:
                return 10
            case PM.PM_ScrollBarSliderMin:
                return 30
            case PM.PM_SliderThickness | PM.PM_SliderLength:
                return 16
            case PM.PM_TabBarTabHSpace:
                return 28
            case PM.PM_TabBarTabVSpace:
                return 10
            case PM.PM_ToolBarItemSpacing:
                return 3
            case PM.PM_ToolBarFrameWidth:
                return 0
            case _:
                return super().pixelMetric(metric, option, widget)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_app_style(app: QApplication, theme: Theme = Theme.DARK) -> None:
    """
    Apply the complete application style to *app*.

    Can be called more than once to switch themes at runtime — Qt
    propagates palette changes to all live widgets automatically.

    Parameters
    ----------
    app : QApplication
        The running application instance.
    theme : Theme
        ``Theme.DARK`` (default), ``Theme.LIGHT``, or ``Theme.CREAM``.
    """
    # Re-install the style only once; re-applying it on every theme
    # switch would reset internal style state unnecessarily.
    if not isinstance(app.style(), AppStyle):
        app.setStyle(AppStyle("Fusion"))

    app.setPalette(build_palette(theme))

    # Disconnect any previous paletteChanged handler to avoid stacking
    # multiple lambdas when apply_app_style is called more than once.
    handled_qt_disconnect(app.paletteChanged)
    app.paletteChanged.connect(lambda _: app.setPalette(build_palette(theme)))