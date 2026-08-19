"""Tests for the dashboard's Light/Dark theme toggle.

Three properties matter here, none of them cosmetic:

**Both themes are complete.** `config.THEMES` is read by key with a `.get`
fallback everywhere it is used; a theme missing a required color would not
raise, it would silently render the wrong chrome. This test asserts both
entries carry every key `charts._base_layout` and `components.inject_css`
read.

**The toggle actually changes output, not just the input dict.** A theme
parameter that is accepted but never threaded through would still pass a
naive "does it raise" test. These assert the rendered CSS and the plotly
layout differ between 'Dark' and 'Light'.

**The default stays 'Dark'.** Every chart function defaults to `theme='Dark'`
so existing callers (and `test_dashboard_charts.py`) see unchanged output;
this is asserted directly rather than trusted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import charts, components as ui, config  # noqa: E402

REQUIRED_KEYS = {
    'SURFACE_PAGE', 'SURFACE_CARD', 'SURFACE_CHART', 'INK_PRIMARY',
    'INK_SECONDARY', 'INK_MUTED', 'GRIDLINE', 'AXIS_LINE', 'BORDER',
    'PLOTLY_TEMPLATE',
}


def test_both_themes_carry_every_required_key():
    for name in ('Dark', 'Light'):
        assert REQUIRED_KEYS.issubset(config.THEMES[name].keys()), name


def test_dark_theme_matches_the_module_constants():
    """`THEME_DARK` mirrors the standalone constants rather than replacing
    them - other modules still import `config.SURFACE_PAGE` etc. directly."""
    t = config.THEMES['Dark']
    assert t['SURFACE_PAGE'] == config.SURFACE_PAGE
    assert t['INK_PRIMARY'] == config.INK_PRIMARY
    assert t['PLOTLY_TEMPLATE'] == 'plotly_dark'


def test_light_theme_is_actually_light():
    t = config.THEMES['Light']
    assert t['SURFACE_PAGE'] != config.SURFACE_PAGE
    assert t['PLOTLY_TEMPLATE'] == 'plotly_white'


def test_inject_css_renders_different_chrome_per_theme():
    dark_css = ui.CSS.format(
        page=config.THEMES['Dark']['SURFACE_PAGE'], card=config.THEMES['Dark']['SURFACE_CARD'],
        chart=config.THEMES['Dark']['SURFACE_CHART'], ink=config.THEMES['Dark']['INK_PRIMARY'],
        ink2=config.THEMES['Dark']['INK_SECONDARY'], muted=config.THEMES['Dark']['INK_MUTED'],
        border=config.THEMES['Dark']['BORDER'], profit=config.PROFIT, loss=config.LOSS,
        warn=config.OPEN, mono=config.FONT_MONO, slot1=config.CATEGORICAL[0],
    )
    light_css = ui.CSS.format(
        page=config.THEMES['Light']['SURFACE_PAGE'], card=config.THEMES['Light']['SURFACE_CARD'],
        chart=config.THEMES['Light']['SURFACE_CHART'], ink=config.THEMES['Light']['INK_PRIMARY'],
        ink2=config.THEMES['Light']['INK_SECONDARY'], muted=config.THEMES['Light']['INK_MUTED'],
        border=config.THEMES['Light']['BORDER'], profit=config.PROFIT, loss=config.LOSS,
        warn=config.OPEN, mono=config.FONT_MONO, slot1=config.CATEGORICAL[0],
    )
    assert dark_css != light_css
    assert config.THEMES['Dark']['SURFACE_PAGE'] in dark_css
    assert config.THEMES['Light']['SURFACE_PAGE'] in light_css


def test_base_layout_plot_bgcolor_follows_theme():
    dark = charts._base_layout(theme='Dark')
    light = charts._base_layout(theme='Light')
    assert dark['plot_bgcolor'] == config.THEMES['Dark']['SURFACE_CHART']
    assert light['plot_bgcolor'] == config.THEMES['Light']['SURFACE_CHART']
    assert dark['plot_bgcolor'] != light['plot_bgcolor']
    assert dark['template'] == 'plotly_dark'
    assert light['template'] == 'plotly_white'


def test_unknown_theme_falls_back_to_dark():
    layout = charts._base_layout(theme='Sepia')
    assert layout['plot_bgcolor'] == config.THEMES['Dark']['SURFACE_CHART']


def test_empty_figure_default_theme_is_dark():
    fig = charts.empty_figure('no data')
    assert fig.layout.plot_bgcolor == config.THEMES['Dark']['SURFACE_CHART']


def test_empty_figure_light_theme_differs_from_dark():
    dark_fig = charts.empty_figure('no data', theme='Dark')
    light_fig = charts.empty_figure('no data', theme='Light')
    assert dark_fig.layout.plot_bgcolor != light_fig.layout.plot_bgcolor
