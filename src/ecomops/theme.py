"""Colour themes for the console.

The app's voice is editorial: serif display type, obsidian surfaces, one accent.
This module keeps that grammar and swaps the paint — six palettes, each with
four distinct hues (never a monochrome accent) plus the neutral surfaces. The
variables are consumed by the base stylesheet in ``app.py``.

Every theme is a flat mapping of CSS custom properties, so switching is one
``<style>`` block: no widget rebuild, no rerun of the domain system, and the
poster renderer keeps its own branded palette (a shared image is the shop's
identity, not the operator's console skin).

Contrast is a test, not a hope: ``tests/test_theme.py`` measures body text and
muted text against each background.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_THEME = "obsidian"
DEFAULT_CONTRAST = "soft"
CONTRASTS: dict[str, str] = {"soft": "Soft", "crisp": "Crisp"}

# Three skins keep the frosted-glass treatment; the rest are flat/normal.
# The split is a look, not a palette change: each theme still carries four hues.
GLASS_THEMES: frozenset[str] = frozenset({"obsidian", "sapphire", "emerald"})


def is_glass(key: str | None) -> bool:
    """Whether a theme uses the frosted-glass material (unknown keys default)."""
    return (key or "").strip().lower() in GLASS_THEMES


@dataclass(frozen=True)
class Theme:
    """One palette, expressed as the CSS variables the base stylesheet reads."""

    key: str
    label: str
    blurb: str
    ink: str            # page background
    surface: str        # cards, forms, image frames
    panel: str          # sidebar, fixed chrome
    field: str          # inputs and selects
    glow: str           # top-of-page radial highlight
    ivory: str          # primary text
    muted: str          # secondary text
    dim: str            # tertiary text
    faint: str          # labels, hints
    soft: str           # quiet body copy
    gold: str           # accent
    gold_bright: str    # accent hover
    gold_rgb: str       # accent as "r,g,b" for alpha variants
    on_accent: str      # text drawn on the accent
    accent_2: str       # second hue, used alongside the accent
    accent_3: str       # third hue
    accent_4: str       # fourth hue
    line: str           # hairlines
    tint_soft: str      # faint surface wash
    tint_mid: str
    tint_strong: str

    @property
    def style_label(self) -> str:
        """Short picker hint for the material used by this skin."""
        return "Glass" if self.key in GLASS_THEMES else "Flat"

    def variables(self) -> str:
        """The ``:root`` block, one variable per line for readability."""
        names = (
            ("ink", "ink"), ("surface", "surface"), ("panel", "panel"), ("field", "field"),
            ("glow", "glow"), ("ivory", "ivory"), ("muted", "muted"), ("dim", "dim"),
            ("faint", "faint"), ("soft", "soft"), ("gold", "gold"),
            ("gold-bright", "gold_bright"), ("gold-rgb", "gold_rgb"),
            ("accent-2", "accent_2"), ("accent-3", "accent_3"),
            ("accent-4", "accent_4"),
            ("on-accent", "on_accent"), ("line", "line"), ("tint-soft", "tint_soft"),
            ("tint-mid", "tint_mid"), ("tint-strong", "tint_strong"),
        )
        body = "".join(
            f"        --msm-{css_name}:{getattr(self, attr)};\n" for css_name, attr in names
        )
        if self.key in GLASS_THEMES:
            body += (
                "        --msm-app-bg:\n"
                "          radial-gradient(760px 540px at 4% -8%,\n"
                "            color-mix(in srgb, var(--msm-accent-2) 30%, transparent), transparent 64%),\n"
                "          radial-gradient(880px 640px at 106% 4%,\n"
                "            color-mix(in srgb, var(--msm-accent-4) 26%, transparent), transparent 66%),\n"
                "          radial-gradient(720px 560px at 52% 114%,\n"
                "            color-mix(in srgb, var(--msm-accent-3) 22%, transparent), transparent 68%),\n"
                "          radial-gradient(1100px 520px at 12% -12%, var(--msm-glow) 0%, var(--msm-ink) 58%)\n"
                "          fixed, var(--msm-ink);\n"
                "        --msm-glass:color-mix(in srgb, var(--msm-surface) 58%, transparent);\n"
                "        --msm-glass-strong:color-mix(in srgb, var(--msm-surface) 82%, transparent);\n"
                "        --msm-glass-soft:color-mix(in srgb, var(--msm-ivory) 6%, transparent);\n"
                "        --msm-glass-hi:color-mix(in srgb, var(--msm-ivory) 12%, transparent);\n"
                "        --msm-glass-border:color-mix(in srgb, var(--msm-ivory) 17%, transparent);\n"
                "        --msm-blur:blur(20px) saturate(150%);\n"
                "        --msm-blur-strong:blur(26px) saturate(160%);\n"
                "        --msm-blur-mid:blur(16px) saturate(140%);\n"
                "        --msm-blur-soft:blur(12px);\n"
                "        --msm-blur-hair:blur(10px);\n"
                "        --msm-shadow:0 24px 70px rgba(0,0,0,.42);\n"
            )
        else:
            body += (
                "        --msm-app-bg:var(--msm-ink);\n"
                "        --msm-glass:var(--msm-surface);\n"
                "        --msm-glass-strong:var(--msm-panel);\n"
                "        --msm-glass-soft:var(--msm-surface);\n"
                "        --msm-glass-hi:transparent;\n"
                "        --msm-glass-border:var(--msm-line);\n"
                "        --msm-blur:none;\n"
                "        --msm-blur-strong:none;\n"
                "        --msm-blur-mid:none;\n"
                "        --msm-blur-soft:none;\n"
                "        --msm-blur-hair:none;\n"
                "        --msm-shadow:0 10px 28px rgba(0,0,0,.28);\n"
            )
        return "      :root {\n" + body + "      }\n"

    def accents(self) -> tuple[str, str, str, str]:
        """The four hues that make this theme multi-colour, in display order."""
        return (self.gold, self.accent_2, self.accent_3, self.accent_4)

    def css(self) -> str:
        """Ready-to-inject ``<style>`` block for this theme."""
        return (
            "<style>\n"
            + self.variables()
            + """      [data-testid="stAppViewContainer"] {
        background: var(--msm-app-bg);
        background-attachment: fixed;
        color: var(--msm-ivory);
      }
      [data-testid="stSidebar"] {background: var(--msm-panel);}
      [data-baseweb="select"] > div, [data-baseweb="input"] > div,
      [data-testid="stNumberInput"] [data-baseweb="input"],
      [data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input,
      [data-testid="stFileUploaderDropzone"] {
        background: var(--msm-field) !important;
        border-color: var(--msm-line) !important;
      }
      [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
      [data-testid="stTextArea"] textarea, [data-baseweb="select"] div,
      [data-baseweb="select"] svg, [data-testid="stWidgetLabel"] p {
        color: var(--msm-ivory) !important;
      }
      [data-baseweb="menu"] {background: var(--msm-surface) !important;}
      [data-baseweb="menu"] li {color: var(--msm-ivory) !important;}
      [data-baseweb="tag"] {
        background: rgba(var(--msm-gold-rgb), .18) !important;
        color: var(--msm-ivory) !important;
      }
      [data-testid="stExpander"] summary, [data-testid="stExpander"] p {
        color: var(--msm-ivory);
      }
    </style>
"""
        )


THEMES: dict[str, Theme] = {
    "obsidian": Theme(
        key="obsidian",
        label="Obsidian Gold",
        blurb="The house default: black lacquer, champagne accent.",
        ink="#0b0b0d", surface="#141417", panel="#0e0e10", field="#141417",
        glow="#1c1811", ivory="#f2efe8", muted="#9a968c", dim="#8f8b81",
        faint="#6f6b63", soft="#bdb8ac", gold="#c8a96a", gold_bright="#d9bd82",
        gold_rgb="200,169,106", on_accent="#0b0b0d",
        accent_2="#74c2a0", accent_3="#e2706b", accent_4="#8fa8e0",
        line="rgba(242,239,232,.12)", tint_soft="rgba(255,255,255,.018)",
        tint_mid="rgba(255,255,255,.03)", tint_strong="rgba(255,255,255,.045)",
    ),
    "sapphire": Theme(
        key="sapphire",
        label="Midnight Sapphire",
        blurb="Ink-blue surfaces with an ice-blue accent.",
        ink="#070b14", surface="#101828", panel="#0a1020", field="#101828",
        glow="#152340", ivory="#eef2fb", muted="#9aabc7", dim="#8d9db8",
        faint="#67748b", soft="#b8c4da", gold="#8fb6e8", gold_bright="#a9c8f2",
        gold_rgb="143,182,232", on_accent="#06101f",
        accent_2="#a78ee8", accent_3="#5ec9c0", accent_4="#e8b96a",
        line="rgba(238,242,251,.13)", tint_soft="rgba(255,255,255,.020)",
        tint_mid="rgba(255,255,255,.034)", tint_strong="rgba(255,255,255,.05)",
    ),
    "emerald": Theme(
        key="emerald",
        label="Emerald Ledger",
        blurb="Deep green glass with a mint accent.",
        ink="#07100c", surface="#0f1a15", panel="#0a1410", field="#0f1a15",
        glow="#122a20", ivory="#eef4ee", muted="#9bb3a4", dim="#8da595",
        faint="#67796d", soft="#bacbbe", gold="#9fd4a5", gold_bright="#b9e4be",
        gold_rgb="159,212,165", on_accent="#04120a",
        accent_2="#e3c46a", accent_3="#7fb8dd", accent_4="#e39a7c",
        line="rgba(238,244,238,.13)", tint_soft="rgba(255,255,255,.020)",
        tint_mid="rgba(255,255,255,.034)", tint_strong="rgba(255,255,255,.05)",
    ),
    "terracotta": Theme(
        key="terracotta",
        label="Terracotta Atelier",
        blurb="Burnt earth surfaces, copper accent.",
        ink="#120c08", surface="#1d1510", panel="#150f0a", field="#1d1510",
        glow="#2c1a10", ivory="#f7ede3", muted="#b6a08d", dim="#a9927f",
        faint="#826c5b", soft="#d2bcaa", gold="#e0a06a", gold_bright="#f0b884",
        gold_rgb="224,160,106", on_accent="#1a0e05",
        accent_2="#d97757", accent_3="#b3bd72", accent_4="#8fa9c9",
        line="rgba(247,237,227,.13)", tint_soft="rgba(255,255,255,.020)",
        tint_mid="rgba(255,255,255,.034)", tint_strong="rgba(255,255,255,.05)",
    ),
    "rose": Theme(
        key="rose",
        label="Rose Quartz",
        blurb="Plum lacquer with a rose-gold accent.",
        ink="#120a10", surface="#1d1219", panel="#150d12", field="#1d1219",
        glow="#2b1520", ivory="#f8eef4", muted="#bb9fae", dim="#ad92a1",
        faint="#846775", soft="#d6bccb", gold="#e0a3b8", gold_bright="#f0bccd",
        gold_rgb="224,163,184", on_accent="#1a0812",
        accent_2="#b9a3e0", accent_3="#efb98c", accent_4="#8fc4c0",
        line="rgba(248,238,244,.13)", tint_soft="rgba(255,255,255,.020)",
        tint_mid="rgba(255,255,255,.034)", tint_strong="rgba(255,255,255,.05)",
    ),
    "graphite": Theme(
        key="graphite",
        label="Graphite & Silver",
        blurb="Neutral charcoal with a cool silver accent.",
        ink="#0b0c0e", surface="#15171a", panel="#0f1113", field="#15171a",
        glow="#1c2024", ivory="#f1f2f4", muted="#a3a8b0", dim="#969ba3",
        faint="#6d727a", soft="#c2c6cc", gold="#c9cfd6", gold_bright="#dfe4ea",
        gold_rgb="201,207,214", on_accent="#0b0c0e",
        accent_2="#8fbdd6", accent_3="#d9b06a", accent_4="#9fc4a8",
        line="rgba(241,242,244,.12)", tint_soft="rgba(255,255,255,.018)",
        tint_mid="rgba(255,255,255,.03)", tint_strong="rgba(255,255,255,.045)",
    ),
}


def names() -> tuple[str, ...]:
    """Theme keys in display order."""
    return tuple(THEMES)


def labels() -> dict[str, str]:
    return {key: theme.label for key, theme in THEMES.items()}


def get(key: str | None) -> Theme:
    """Theme by key, falling back to the default for unknown or empty input."""
    return THEMES.get((key or "").strip().lower(), THEMES[DEFAULT_THEME])


def css(key: str | None) -> str:
    """CSS for one theme (unknown keys resolve to the default)."""
    return get(key).css()


def contrasts() -> dict[str, str]:
    """The two contrast levels offered by the picker."""
    return dict(CONTRASTS)


def get_contrast(level: str | None) -> str:
    """Contrast key, falling back to the soft/medium default."""
    value = (level or "").strip().lower()
    return value if value in CONTRASTS else DEFAULT_CONTRAST


def contrast_css(key: str | None, level: str | None) -> str:
    """Extra overrides for the crisp level; soft is the theme's own tuning."""
    if get_contrast(level) != "crisp":
        return ""
    palette = get(key)

    def lift(colour: str) -> str:
        return f"color-mix(in srgb, {colour} 52%, {palette.ivory})"

    return (
        "<style>\n      :root {\n"
        f"        --msm-muted:{lift(palette.muted)};\n"
        f"        --msm-dim:{lift(palette.dim)};\n"
        f"        --msm-faint:{lift(palette.faint)};\n"
        f"        --msm-line:color-mix(in srgb, {palette.ivory} 26%, transparent);\n"
        f"        --msm-tint-soft:color-mix(in srgb, {palette.ivory} 6%, transparent);\n"
        f"        --msm-tint-mid:color-mix(in srgb, {palette.ivory} 9%, transparent);\n"
        f"        --msm-tint-strong:color-mix(in srgb, {palette.ivory} 12%, transparent);\n"
        "      }\n</style>\n"
    )


def swatch(key: str) -> str:
    """Four accent dots, the visual signature of a multi-colour theme."""
    return "".join(
        f"<span class='msm-swatch' style='background:{colour};"
        f"border-color:{colour}'></span>"
        for colour in get(key).accents()
    )


__all__ = [
    "CONTRASTS",
    "DEFAULT_CONTRAST",
    "DEFAULT_THEME",
    "GLASS_THEMES",
    "THEMES",
    "Theme",
    "contrast_css",
    "contrasts",
    "css",
    "get",
    "get_contrast",
    "is_glass",
    "labels",
    "names",
    "swatch",
]
