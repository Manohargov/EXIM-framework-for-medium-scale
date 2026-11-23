"""Theme palettes: five-plus complete skins, contrast-checked, switchable.

The console's themes are pure CSS variable sets, so these tests pin the
contract the stylesheet relies on: every token is present in every theme, text
stays legible on every background, and the picker actually swaps the block.
"""

from pathlib import Path

import pytest

from ecomops import theme

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src" / "ecomops" / "app.py")

TOKENS = (
    "ink", "surface", "panel", "field", "glow", "ivory", "muted", "dim",
    "faint", "soft", "gold", "gold-bright", "gold-rgb", "accent-2", "accent-3",
    "accent-4", "on-accent", "line", "tint-soft", "tint-mid", "tint-strong",
    "app-bg", "glass", "glass-strong", "glass-soft", "glass-border", "blur",
)


def _luminance(colour: str) -> float:
    channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    high, low = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (high + 0.05) / (low + 0.05)


def _rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _distance(first: str, second: str) -> float:
    return sum((a - b) ** 2 for a, b in zip(_rgb(first), _rgb(second), strict=True)) ** 0.5


def test_at_least_five_themes():
    assert len(theme.THEMES) >= 5
    assert theme.DEFAULT_THEME in theme.THEMES
    assert len(theme.names()) == len(set(theme.names())) == len(theme.THEMES)


def test_every_theme_exposes_every_token():
    for key, palette in theme.THEMES.items():
        assert palette.key == key
        css = palette.css()
        assert css.startswith("<style>") and css.rstrip().endswith("</style>")
        assert ":root {" in css
        for token in TOKENS:
            assert f"--msm-{token}:" in css, f"{key} misses {token}"
        assert "[data-testid=\"stAppViewContainer\"]" in css


def test_get_falls_back_to_the_default():
    default = theme.THEMES[theme.DEFAULT_THEME]
    assert theme.get(None) is default
    assert theme.get("") is default
    assert theme.get("not-a-theme") is default
    assert theme.get("  EMERALD ") is theme.THEMES["emerald"]


def test_three_glasses_and_three_flat_skins():
    glass = {key for key in theme.names() if theme.is_glass(key)}
    assert len(glass) == 3
    assert len(theme.THEMES) - len(glass) == 3
    assert glass == theme.GLASS_THEMES
    assert {key for key, palette in theme.THEMES.items() if palette.style_label == "Glass"} == glass
    assert {key for key, palette in theme.THEMES.items() if palette.style_label == "Flat"} == set(
        theme.names()
    ) - glass


def test_flat_themes_drop_blur_and_aurora():
    for key in theme.names():
        css = theme.css(key)
        if theme.is_glass(key):
            assert "--msm-blur:blur(" in css
            assert "radial-gradient" in css
        else:
            assert "--msm-blur:none;" in css
            assert "--msm-app-bg:var(--msm-ink);" in css
            assert "radial-gradient" not in css


def test_every_theme_has_four_distinct_hues():
    for key, palette in theme.THEMES.items():
        hues = palette.accents()
        assert len(hues) == 4
        assert len(set(hues)) == 4, key
        for index, first in enumerate(hues):
            assert _contrast(first, palette.ink) >= 4.5, f"{key} hue {index}"
            for second in hues[index + 1:]:
                assert _distance(first, second) >= 40, f"{key}: {first} vs {second}"


def test_text_stays_legible_on_every_background():
    for key, palette in theme.THEMES.items():
        assert _contrast(palette.ivory, palette.ink) >= 12, key
        assert _contrast(palette.muted, palette.ink) >= 6, key
        assert _contrast(palette.soft, palette.ink) >= 8, key
        assert _contrast(palette.on_accent, palette.gold) >= 7, key
        assert _contrast(palette.gold, palette.ink) >= 4.5, key


def test_labels_and_swatches_describe_the_palette():
    labels = theme.labels()
    assert set(labels) == set(theme.names())
    assert len(set(labels.values())) == len(labels)
    palette = theme.get("terracotta")
    markup = theme.swatch("terracotta")
    assert markup.count("msm-swatch") == 4
    for hue in palette.accents():
        assert hue in markup


@pytest.fixture()
def _app_env(monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")


def _run() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _injected_css(at: AppTest) -> list[str]:
    return [block.value for block in at.markdown if ":root {" in block.value]


def test_app_renders_the_theme_switcher(_app_env):
    at = _run()
    assert at.pills, "theme picker missing"
    picker = at.pills[0]
    assert list(picker.options) == [theme.get(key).label for key in theme.names()]
    assert picker.value == theme.DEFAULT_THEME
    css = _injected_css(at)
    assert css[-1].strip() == theme.css(theme.DEFAULT_THEME).strip()
    preview = [block.value for block in at.markdown if "msm-theme-preview" in block.value]
    assert preview and preview[-1].count("msm-swatch") == 4


def test_clearing_the_pill_keeps_a_palette(_app_env):
    at = _run()
    at.pills[0].set_value("rose").run()
    at.pills[0].set_value(None).run()
    assert not at.exception, at.exception
    assert at.pills[0].value == "rose"
    assert _injected_css(at)[-1].strip() == theme.css("rose").strip()


def test_switching_the_pill_swaps_the_palette(_app_env):
    at = _run()
    at.pills[0].set_value("emerald").run()
    assert not at.exception, at.exception
    assert at.pills[0].value == "emerald"
    css = _injected_css(at)
    assert css[-1].strip() == theme.css("emerald").strip()
    assert theme.THEMES["obsidian"].glow not in css[-1]


def test_two_contrast_levels():
    levels = theme.contrasts()
    assert list(levels) == ["soft", "crisp"]
    assert theme.get_contrast(None) == "soft"
    assert theme.get_contrast(" CRISP ") == "crisp"
    assert theme.get_contrast("nonsense") == "soft"
    assert theme.contrast_css("obsidian", "soft") == ""
    crisp = theme.contrast_css("obsidian", "crisp")
    assert "--msm-muted" in crisp
    assert "--msm-line" in crisp
    assert "color-mix" in crisp


def test_contrast_switch_updates_the_stylesheet(_app_env):
    at = _run()
    assert len(at.pills) == 2
    assert at.pills[1].value == "soft"
    at.pills[1].set_value("crisp").run()
    assert not at.exception, at.exception
    assert at.pills[1].value == "crisp"
    overrides = [block.value for block in at.markdown if "--msm-line:color-mix" in block.value]
    assert overrides
    at.pills[1].set_value(None).run()
    assert at.pills[1].value == "soft"
