"""Saju chart components: static HTML + CSS keyframes, no JavaScript (#311).

Every component is laid out in percentages of the canvas and uses inline styles
so it travels inside one ``html`` layer. Labels are escaped; animation is
keyframe-only, so a HyperFrames seek reproduces the same frame.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from video_editor_core.composition import Canvas
from video_editor_core.designs import DesignTokens
from video_editor_core.graphics.registry import GraphicError, register_graphic


def _accent(tokens: DesignTokens) -> str:
    return tokens.palette.accent


def _ink(tokens: DesignTokens) -> str:
    return tokens.palette.ink


def _card_style(tokens: DesignTokens) -> str:
    return (
        "background:rgba(15,23,42,.82);border:1px solid rgba(255,255,255,.14);"
        "border-radius:18px;box-sizing:border-box;color:"
        f"{_ink(tokens)};font-family:Inter,system-ui,sans-serif;"
    )


@register_graphic("four_pillars")
def four_pillars(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    pillars = data.get("pillars") or []
    if not isinstance(pillars, list) or not pillars:
        raise GraphicError("four_pillars needs a non-empty 'pillars' list")
    cards = []
    for index, item in enumerate(pillars[:4]):
        if not isinstance(item, Mapping):
            raise GraphicError("four_pillars entries must be objects")
        label = html.escape(str(item.get("label") or ""))
        glyph = html.escape(str(item.get("glyph") or ""))
        sub = html.escape(str(item.get("sub") or ""))
        delay = 0.08 * index
        cards.append(
            f'<div style="{_card_style(tokens)}flex:1;padding:4% 3%;'
            f'animation:ve_g_card .5s ease-out {delay:.3f}s both;text-align:center;">'
            f'<div style="font-size:1.4vw;opacity:.72">{label}</div>'
            f'<div style="font-size:4.2vw;line-height:1.1;color:{_accent(tokens)}">{glyph}</div>'
            f'<div style="font-size:1.2vw;opacity:.8">{sub}</div></div>'
        )
    return (
        "<style>@keyframes ve_g_card{from{opacity:0;transform:translateY(18px) scale(.96)}"
        "to{opacity:1;transform:none}}</style>"
        '<div style="display:flex;gap:1.6%;width:100%;height:100%;align-items:center;'
        'padding:3%;box-sizing:border-box">' + "".join(cards) + "</div>"
    )


@register_graphic("element_bars")
def element_bars(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    elements = data.get("elements") or []
    if not isinstance(elements, list) or not elements:
        raise GraphicError("element_bars needs a non-empty 'elements' list")
    values = [float(item.get("value", 0)) for item in elements if isinstance(item, Mapping)]
    peak = max(values) if values else 1.0
    rows = []
    for index, item in enumerate(elements):
        if not isinstance(item, Mapping):
            raise GraphicError("element_bars entries must be objects")
        name = html.escape(str(item.get("name") or ""))
        value = float(item.get("value", 0))
        width = 8.0 + 82.0 * (value / peak if peak else 0.0)
        delay = 0.1 * index
        rows.append(
            '<div style="display:flex;align-items:center;gap:2%;margin:1.2% 0">'
            f'<div style="width:14%;font-size:1.4vw;text-align:right;opacity:.8">{name}</div>'
            '<div style="flex:1;height:2.2vw;background:rgba(255,255,255,.08);'
            'border-radius:999px;overflow:hidden">'
            f'<div style="width:{width:.1f}%;height:100%;background:{_accent(tokens)};'
            f'animation:ve_g_bar .6s ease-out {delay:.3f}s both"></div></div>'
            f'<div style="width:7%;font-size:1.3vw;opacity:.8">{value:g}</div></div>'
        )
    return (
        "<style>@keyframes ve_g_bar{from{transform:scaleX(0);transform-origin:left}"
        "to{transform:scaleX(1);transform-origin:left}}</style>"
        f'<div style="{_card_style(tokens)}width:100%;height:100%;padding:4%;">{"".join(rows)}</div>'
    )


@register_graphic("decade_ribbon")
def decade_ribbon(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    start = int(data.get("start", 1984))
    end = int(data.get("end", start + 60))
    current = int(data.get("current", start))
    if end <= start:
        raise GraphicError("decade_ribbon needs end > start")
    ticks = []
    for year in range(start, end + 1, 10):
        active = year <= current < year + 10
        ticks.append(
            f'<div style="flex:1;text-align:center;font-size:1.3vw;'
            f"color:{_accent(tokens) if active else _ink(tokens)};"
            f'opacity:{1.0 if active else 0.55}">{year}</div>'
        )
    return (
        f'<div style="{_card_style(tokens)}width:100%;height:100%;padding:4%;'
        'display:flex;flex-direction:column;justify-content:center">'
        '<div style="display:flex;align-items:center">'
        '<div style="flex:1;height:.5vw;background:rgba(255,255,255,.2);border-radius:999px;'
        'position:relative;margin:0 2%">'
        f'<div style="position:absolute;left:{((current - start) / (end - start)) * 100:.1f}%;'
        f"top:-.6vw;width:1.7vw;height:1.7vw;border-radius:50%;background:{_accent(tokens)};"
        'box-shadow:0 0 18px rgba(255,255,255,.3)"></div></div></div>'
        '<div style="display:flex;margin-top:3%">' + "".join(ticks) + "</div></div>"
    )


@register_graphic("medallion_ring")
def medallion_ring(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise GraphicError("medallion_ring needs a non-empty 'items' list")
    count = min(len(items), 12)
    radius = 34.0
    nodes = []
    for index in range(count):
        angle = (360.0 / count) * index
        label = html.escape(str(items[index]))
        nodes.append(
            f'<div style="position:absolute;left:50%;top:50%;transform:'
            f"rotate({angle:.2f}deg) translateY(-{radius}%) rotate(-{angle:.2f}deg);"
            f"width:7%;height:7%;margin:-3.5%;border-radius:50%;"
            f"background:rgba(15,23,42,.9);border:1px solid {_accent(tokens)};"
            "display:flex;align-items:center;justify-content:center;"
            f'font-size:1vw;color:{_ink(tokens)}">{label}</div>'
        )
    return (
        f'<div style="{_card_style(tokens)}width:100%;height:100%;position:relative;">'
        f'<div style="position:absolute;inset:18%;border:1px solid rgba(255,255,255,.18);'
        'border-radius:50%"></div>'
        f'<div style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);'
        f'font-size:1.4vw;color:{_accent(tokens)}">60</div>' + "".join(nodes) + "</div>"
    )


@register_graphic("strength_gauge")
def strength_gauge(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    value = float(data.get("value", 0.0))
    label = html.escape(str(data.get("label") or ""))
    peak = float(data.get("max", 100.0)) or 1.0
    percent = max(0.0, min(100.0, value / peak * 100.0))
    return (
        "<style>@keyframes ve_g_gauge{from{transform:scaleX(0);transform-origin:left}"
        "to{transform:scaleX(1);transform-origin:left}}</style>"
        f'<div style="{_card_style(tokens)}width:100%;height:100%;padding:5%;'
        'display:flex;flex-direction:column;justify-content:center;gap:2%">'
        f'<div style="font-size:1.5vw;opacity:.8">{label}</div>'
        '<div style="width:80%;height:2.4vw;background:rgba(255,255,255,.08);'
        'border-radius:999px;overflow:hidden">'
        f'<div style="width:{percent:.1f}%;height:100%;background:{_accent(tokens)};'
        'animation:ve_g_gauge .7s ease-out both"></div></div>'
        f'<div style="font-size:3.6vw;color:{_accent(tokens)}">{value:g}</div></div>'
    )


@register_graphic("feeding_cycle")
def feeding_cycle(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    steps = data.get("steps") or []
    if not isinstance(steps, list) or len(steps) < 2:
        raise GraphicError("feeding_cycle needs at least two 'steps'")
    nodes = []
    for item in steps:
        if not isinstance(item, Mapping):
            raise GraphicError("feeding_cycle entries must be objects")
        label = html.escape(str(item.get("label") or ""))
        glyph = html.escape(str(item.get("glyph") or ""))
        nodes.append(
            f'<div style="{_card_style(tokens)}padding:2% 3%;text-align:center;min-width:8%">'
            f'<div style="font-size:2.8vw;color:{_accent(tokens)}">{glyph}</div>'
            f'<div style="font-size:1.2vw;opacity:.8">{label}</div></div>'
        )
    arrow = f'<div style="font-size:2.2vw;color:{_accent(tokens)}">→</div>'
    return (
        f'<div style="{_card_style(tokens)}width:100%;height:100%;display:flex;'
        'align-items:center;justify-content:center;gap:1%;flex-wrap:wrap;padding:3%">'
        + arrow.join(nodes)
        + f'<div style="font-size:2.2vw;color:{_accent(tokens)}">↺</div></div>'
    )


@register_graphic("sixty_cycle_rings")
def sixty_cycle_rings(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise GraphicError("sixty_cycle_rings needs a non-empty 'items' list")
    current = int(data.get("current", 0))
    cells = []
    for index, item in enumerate(items[:60]):
        active = index == current
        label = html.escape(str(item))
        cells.append(
            f'<div style="padding:1%;text-align:center;font-size:1vw;'
            f"color:{_accent(tokens) if active else _ink(tokens)};"
            f"background:{'rgba(255,255,255,.12)' if active else 'transparent'};"
            f'border-radius:8px">{label}</div>'
        )
    return (
        f'<div style="{_card_style(tokens)}width:100%;height:100%;padding:3%;'
        'display:grid;grid-template-columns:repeat(10,1fr);gap:1%;align-content:center">'
        + "".join(cells)
        + "</div>"
    )


@register_graphic("countdown")
def countdown(data: Mapping[str, Any], tokens: DesignTokens, canvas: Canvas) -> str:
    start = int(data.get("from", 3))
    if start < 1:
        raise GraphicError("countdown 'from' must be at least 1")
    numbers = []
    for offset in range(start):
        value = start - offset
        delay = offset * 0.9
        numbers.append(
            f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
            f"justify-content:center;font-size:12vw;color:{_accent(tokens)};"
            f'animation:ve_g_count 1s ease-out {delay:.2f}s both">{value}</div>'
        )
    return (
        "<style>@keyframes ve_g_count{0%{opacity:0;transform:scale(.7)}"
        "25%{opacity:1;transform:scale(1)}75%{opacity:1;transform:scale(1)}"
        "100%{opacity:0;transform:scale(1.08)}}</style>"
        '<div style="position:relative;width:100%;height:100%">' + "".join(numbers) + "</div>"
    )
