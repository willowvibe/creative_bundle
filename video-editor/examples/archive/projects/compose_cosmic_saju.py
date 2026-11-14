#!/usr/bin/env python3
"""HyperFrames composer for the Cosmic Saju 16:9 promo video.

Renders a self-contained HTML/CSS/JS composition through the local HyperFrames
CLI, then synthesizes a soft ambient pad + transition chimes via FFmpeg and
mixes them into the final MP4.

Output: videos/2026-07-08/cosmic-saju/renders/cosmic-saju-promo.mp4
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "videos" / "2026-07-08" / "cosmic-saju"
WORK_DIR = PROJECT_DIR / "work" / "hyperframes"
RENDERS_DIR = PROJECT_DIR / "renders"
GSAP_URL = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"

# Fixed 20-second scene timing requested in the creative brief.
SCENES: list[dict[str, Any]] = [
    {
        "id": "chart",
        "start": 0.0,
        "duration": 3.0,
        "headline": "Your Birth Chart",
        "subhead": "Year · Month · Day · Hour",
    },
    {
        "id": "elements",
        "start": 3.0,
        "duration": 3.0,
        "headline": "Five Elements",
        "subhead": "Fire · Water · Wood · Metal · Earth",
    },
    {
        "id": "report",
        "start": 6.0,
        "duration": 4.0,
        "headline": "Your Deep Report",
        "subhead": "Chart · Career · Timing",
    },
    {
        "id": "tiers",
        "start": 10.0,
        "duration": 3.0,
        "headline": "Choose Your Reading",
        "subhead": "From a free sample to a full destiny reading",
    },
    {
        "id": "compat",
        "start": 13.0,
        "duration": 3.0,
        "headline": "Marriage Compatibility",
        "subhead": "Two charts. One path.",
    },
    {
        "id": "brand",
        "start": 16.0,
        "duration": 4.0,
        "headline": "Cosmic Saju",
        "subhead": "Your Chart. Your Timing. Your Story.",
    },
]


def ensure_dirs() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


from video_editor_ffmpeg.probe import probe_duration  # noqa: E402


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def download_gsap(destination: Path) -> None:
    if destination.exists():
        print(f"gsap (cached): {destination}")
        return
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(GSAP_URL)
        response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    print(f"gsap: {destination} ({len(response.content)} bytes)")


def build_html(scenes: list[dict[str, Any]]) -> str:
    total_duration = max(s["start"] + s["duration"] for s in scenes)

    # Brand palette.
    colors = {
        "bg": "#0F0B1E",
        "ink": "#151025",
        "gold": "#C9A227",
        "goldSoft": "rgba(201,162,39,0.35)",
        "teal": "#2DD4BF",
        "tealSoft": "rgba(45,212,191,0.25)",
        "violet": "#7C3AED",
        "violetSoft": "rgba(124,58,237,0.20)",
        "fire": "#F59E0B",
        "water": "#3B82F6",
        "wood": "#10B981",
        "metal": "#E2E8F0",
        "earth": "#8B5CF6",
        "text": "#F5F3FF",
        "textMuted": "rgba(245,243,255,0.72)",
        "glass": "rgba(255,255,255,0.06)",
        "glassBorder": "rgba(255,255,255,0.14)",
    }

    fonts_link = (
        "https://fonts.googleapis.com/css2?family="
        "Noto+Serif+KR:wght@400;700&family="
        "Noto+Sans+KR:wght@300;400;700&family="
        "Playfair+Display:wght@400;700&display=swap"
    )

    # Random but deterministic particle field.
    random.seed(42)
    particles: list[str] = []
    for _ in range(60):
        x = random.randint(0, 1920)
        y = random.randint(0, 1080)
        size = random.randint(2, 5)
        opacity = random.uniform(0.15, 0.55)
        dx = random.randint(-80, 80)
        dy = random.randint(-60, 60)
        color = random.choice([colors["gold"], colors["teal"], colors["violet"], colors["text"]])
        particles.append(
            f'<div class="particle" data-dx="{dx}" data-dy="{dy}" '
            f'style="left:{x}px;top:{y}px;width:{size}px;height:{size}px;'
            f'background:{color};opacity:{opacity:.2f};"></div>'
        )
    particles_html = "\n".join(particles)

    css = textwrap.dedent(f"""
        @import url('{fonts_link}');
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{
          margin: 0;
          width: 1920px;
          height: 1080px;
          overflow: hidden;
          background: {colors['bg']};
          font-family: "Noto Sans KR", "Inter", sans-serif;
          color: {colors['text']};
        }}
        .bg {{
          position: absolute; inset: 0; z-index: 0;
          background:
            radial-gradient(circle at 18% 22%, {colors['violetSoft']} 0%, transparent 34%),
            radial-gradient(circle at 82% 78%, {colors['tealSoft']} 0%, transparent 30%),
            radial-gradient(circle at 50% 50%, {colors['goldSoft']} 0%, transparent 55%),
            {colors['bg']};
        }}
        .vignette {{
          position: absolute; inset: 0; z-index: 1;
          background: radial-gradient(circle at 50% 50%, transparent 0%, transparent 55%, rgba(0,0,0,0.45) 100%);
          pointer-events: none;
        }}
        .grain {{
          position: absolute; inset: 0; z-index: 2; pointer-events: none; opacity: 0.05;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
          mix-blend-mode: overlay;
        }}
        .particles {{
          position: absolute; inset: 0; z-index: 1; pointer-events: none;
        }}
        .particle {{
          position: absolute; border-radius: 50%;
          filter: blur(0.5px);
        }}
        .scene {{
          position: absolute; inset: 0; z-index: 10;
          opacity: 0;
          pointer-events: none;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
        }}
        .hero-layer {{
          position: absolute; inset: -60px; z-index: -1;
          background: radial-gradient(circle at 50% 50%, {colors['violetSoft']} 0%, transparent 70%);
        }}
        .headline {{
          font-family: "Noto Serif KR", "Playfair Display", serif;
          font-weight: 700;
          font-size: 96px;
          line-height: 1.08;
          letter-spacing: -0.02em;
          text-align: center;
          color: {colors['text']};
          text-shadow: 0 4px 40px rgba(0,0,0,0.45);
        }}
        .subhead {{
          font-family: "Noto Sans KR", "Inter", sans-serif;
          font-weight: 400;
          font-size: 38px;
          line-height: 1.4;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: {colors['textMuted']};
          margin-top: 18px;
        }}
        .label {{
          font-family: "Noto Sans KR", "Inter", sans-serif;
          font-weight: 700;
          font-size: 20px;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: {colors['gold']};
        }}
        .reveal {{ will-change: transform, opacity; }}

        /* Frame 1: Birth chart grid */
        .chart-grid {{
          display: grid;
          grid-template-columns: repeat(2, 340px);
          grid-template-rows: repeat(2, 340px);
          gap: 40px;
          margin-top: 40px;
        }}
        .pillar-cell {{
          width: 340px; height: 340px;
          border: 1px solid {colors['glassBorder']};
          background: {colors['glass']};
          backdrop-filter: blur(12px);
          border-radius: 28px;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          position: relative;
        }}
        .pillar-cell::before {{
          content: ''; position: absolute; inset: 0;
          border-radius: 28px;
          border: 1px solid transparent;
          background: linear-gradient(135deg, {colors['goldSoft']}, transparent 40%) border-box;
          -webkit-mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
          -webkit-mask-composite: xor;
          mask-composite: exclude;
          pointer-events: none;
        }}
        .pillar-glyph {{
          font-family: "Noto Serif KR", serif;
          font-size: 120px;
          color: {colors['gold']};
          line-height: 1;
          text-shadow: 0 0 50px {colors['goldSoft']};
        }}
        .pillar-line {{
          width: 80px; height: 2px; background: {colors['gold']};
          margin: 22px 0 18px; opacity: 0.6;
        }}
        .pillar-label {{
          font-size: 18px; letter-spacing: 0.16em;
          color: {colors['textMuted']};
        }}

        /* Frame 2: Elements */
        .elements-grid {{
          display: grid;
          grid-template-columns: repeat(2, 360px);
          grid-template-rows: repeat(2, 360px);
          gap: 36px;
          margin-top: 36px;
          position: relative;
        }}
        .element-card {{
          border: 1px solid {colors['glassBorder']};
          background: {colors['glass']};
          backdrop-filter: blur(10px);
          border-radius: 24px;
          padding: 34px;
          display: flex; flex-direction: column;
          align-items: center;
        }}
        .element-icon {{
          width: 72px; height: 72px; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          font-size: 34px; margin-bottom: 18px;
          box-shadow: 0 8px 40px rgba(0,0,0,0.25);
        }}
        .element-name {{
          font-size: 26px; font-weight: 700; letter-spacing: 0.1em;
          text-transform: uppercase; color: {colors['text']};
          margin-bottom: 28px;
        }}
        .bar-track {{
          width: 18px; height: 140px;
          background: rgba(255,255,255,0.08);
          border-radius: 9999px;
          overflow: hidden;
          position: relative;
        }}
        .bar-fill {{
          position: absolute; bottom: 0; left: 0; right: 0;
          transform-origin: bottom;
          transform: scaleY(0);
          border-radius: 9999px;
        }}
        .element-pct {{
          margin-top: 22px; font-size: 34px; font-weight: 700;
          color: {colors['text']};
        }}
        .earth-core {{
          position: absolute; left: 50%; top: 50%;
          transform: translate(-50%, -50%);
          width: 130px; height: 130px;
          border-radius: 50%;
          background: radial-gradient(circle, {colors['earth']} 0%, transparent 70%);
          display: flex; align-items: center; justify-content: center;
          font-size: 18px; font-weight: 700; letter-spacing: 0.12em;
          color: {colors['text']};
          text-shadow: 0 0 30px {colors['earth']};
          box-shadow: 0 0 80px {colors['earth']}55;
        }}

        /* Frame 3: Report mockup */
        .tablet {{
          width: 680px; height: 860px;
          border-radius: 48px;
          border: 1px solid {colors['glassBorder']};
          background: rgba(10,8,22,0.55);
          backdrop-filter: blur(22px);
          box-shadow: 0 40px 120px rgba(0,0,0,0.55), inset 0 0 0 1px rgba(255,255,255,0.08);
          padding: 34px;
          position: relative;
          overflow: hidden;
        }}
        .tablet-notch {{
          position: absolute; top: 22px; left: 50%; transform: translateX(-50%);
          width: 160px; height: 8px; border-radius: 9999px;
          background: {colors['glassBorder']};
        }}
        .tablet-screen {{
          width: 100%; height: 100%;
          border-radius: 28px;
          overflow: hidden;
          position: relative;
          background: {colors['bg']};
          border: 1px solid rgba(255,255,255,0.06);
        }}
        .report-page {{
          position: absolute; inset: 0;
          padding: 44px 38px;
          background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, transparent 100%);
        }}
        .report-title {{
          font-family: "Noto Serif KR", serif;
          font-size: 36px; color: {colors['gold']};
          margin-bottom: 28px;
        }}
        .report-row {{
          display: flex; justify-content: space-between;
          padding: 18px 0; border-bottom: 1px solid rgba(255,255,255,0.08);
          font-size: 22px;
        }}
        .report-row span:first-child {{ color: {colors['textMuted']}; }}
        .mini-chart {{
          display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;
          margin-top: 24px;
        }}
        .mini-cell {{
          border: 1px solid rgba(255,255,255,0.1); border-radius: 12px;
          padding: 18px; text-align: center;
        }}
        .mini-glyph {{ font-size: 30px; color: {colors['gold']}; }}
        .mini-label {{ font-size: 14px; color: {colors['textMuted']}; margin-top: 6px; }}

        /* Frame 4: Tiers */
        .tier-stage {{
          position: absolute; inset: 0; display: flex;
        }}
        .tier-panel {{
          position: absolute; top: 0; bottom: 0;
          width: 33.333%;
          border-right: 1px solid {colors['glassBorder']};
          background: {colors['glass']};
          backdrop-filter: blur(8px);
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          padding: 60px 44px;
          text-align: center;
        }}
        .tier-panel:last-child {{ border-right: none; }}
        .tier-name {{
          font-family: "Noto Serif KR", serif;
          font-size: 38px; color: {colors['text']};
          margin-bottom: 14px;
        }}
        .tier-price {{
          font-size: 64px; font-weight: 700; color: {colors['gold']};
          margin-bottom: 40px;
        }}
        .tier-snippet {{
          font-size: 26px; line-height: 1.5;
          color: {colors['textMuted']};
        }}
        .tier-accent {{
          width: 60px; height: 4px; border-radius: 9999px;
          background: {colors['teal']};
          margin-top: 34px;
        }}

        /* Frame 5: Compatibility */
        .compat-stage {{
          position: absolute; inset: 0;
          display: flex; align-items: center; justify-content: center;
        }}
        .chart-ring {{
          width: 360px; height: 360px;
          border-radius: 50%;
          border: 2px solid {colors['glassBorder']};
          background: radial-gradient(circle, {colors['violetSoft']} 0%, transparent 70%);
          display: flex; align-items: center; justify-content: center;
          position: relative;
          z-index: 2;
        }}
        .ring-glyph {{
          position: absolute; font-family: "Noto Serif KR", serif;
          font-size: 42px; color: {colors['gold']};
          transform: translate(-50%, -50%);
        }}
        .ring-center {{
          font-family: "Noto Serif KR", serif;
          font-size: 56px; color: {colors['text']};
        }}
        .connect-beam {{
          width: 420px; height: 4px;
          background: linear-gradient(90deg, {colors['gold']} 0%, {colors['teal']} 50%, {colors['gold']} 100%);
          border-radius: 9999px;
          box-shadow: 0 0 40px {colors['gold']};
          transform-origin: center;
          opacity: 0.9;
          z-index: 1;
        }}

        /* Frame 6: Brand reveal */
        .brand-stage {{
          position: absolute; inset: 0;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          text-align: center;
        }}
        .brand-mark {{
          width: 140px; height: 140px;
          border: 2px solid {colors['gold']};
          border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          font-family: "Noto Serif KR", serif;
          font-size: 72px; color: {colors['gold']};
          box-shadow: 0 0 80px {colors['goldSoft']};
          margin-bottom: 44px;
        }}
        .brand-text {{
          font-family: "Noto Serif KR", "Playfair Display", serif;
          font-size: 120px; font-weight: 700;
          color: {colors['text']};
          letter-spacing: 0.04em;
        }}
        .brand-tag {{
          font-size: 40px; font-weight: 300;
          color: {colors['textMuted']};
          margin-top: 24px;
          letter-spacing: 0.08em;
        }}
        .brand-cta {{
          margin-top: 60px;
          border: 1px solid {colors['gold']};
          background: {colors['goldSoft']};
          padding: 24px 56px; border-radius: 9999px;
          font-size: 28px; font-weight: 700;
          color: {colors['text']};
          letter-spacing: 0.04em;
        }}
    """)

    def scene_html(scene: dict[str, Any]) -> str:
        sid = scene["id"]
        start = scene["start"]
        dur = scene["duration"]
        headline = escape_html(scene["headline"])
        subhead = escape_html(scene["subhead"])
        parts: list[str] = []

        # Background glow for every scene.
        parts.append('<div class="hero-layer"></div>')

        # Shared top headline/subhead only for the first two narrative scenes.
        # Content-heavy scenes (report, tiers, compatibility, brand) carry their
        # own titles, so the shared text would overlap or ghost behind panels.
        if sid in {"chart", "elements"}:
            parts.append(
                f'<div class="scene-headline reveal headline" style="margin-bottom:6px;">{headline}</div>'
                f'<div class="scene-subhead reveal subhead">{subhead}</div>'
            )

        if sid == "chart":
            parts.append(textwrap.dedent("""
                <div class="chart-grid">
                  <div class="pillar-cell reveal">
                    <div class="pillar-glyph">年</div>
                    <div class="pillar-line"></div>
                    <div class="pillar-label">Year Pillar</div>
                  </div>
                  <div class="pillar-cell reveal">
                    <div class="pillar-glyph">月</div>
                    <div class="pillar-line"></div>
                    <div class="pillar-label">Month Pillar</div>
                  </div>
                  <div class="pillar-cell reveal">
                    <div class="pillar-glyph">日</div>
                    <div class="pillar-line"></div>
                    <div class="pillar-label">Day Pillar</div>
                  </div>
                  <div class="pillar-cell reveal">
                    <div class="pillar-glyph">時</div>
                    <div class="pillar-line"></div>
                    <div class="pillar-label">Hour Pillar</div>
                  </div>
                </div>
            """))
        elif sid == "elements":
            elements = [
                ("FIRE", "🔥", colors["fire"], "78%"),
                ("WATER", "💧", colors["water"], "64%"),
                ("WOOD", "🌿", colors["wood"], "55%"),
                ("METAL", "⚜", colors["metal"], "43%"),
            ]
            cards = "\n".join(
                f"""
                <div class="element-card reveal">
                  <div class="element-icon" style="background:{color}80;box-shadow:0 0 40px {color}40;">{icon}</div>
                  <div class="element-name">{name}</div>
                  <div class="bar-track"><div class="bar-fill" style="height:{pct};background:{color};"></div></div>
                  <div class="element-pct">{pct}</div>
                </div>
                """
                for name, icon, color, pct in elements
            )
            parts.append(
                f'<div class="elements-grid">{cards}<div class="earth-core reveal">EARTH</div></div>'
            )
        elif sid == "report":
            parts.append(textwrap.dedent("""
                <div class="tablet reveal">
                  <div class="tablet-notch"></div>
                  <div class="tablet-screen">
                    <div class="report-page page-1" style="z-index:3;">
                      <div class="report-title">Birth Chart</div>
                      <div class="mini-chart">
                        <div class="mini-cell"><div class="mini-glyph">年</div><div class="mini-label">Year</div></div>
                        <div class="mini-cell"><div class="mini-glyph">月</div><div class="mini-label">Month</div></div>
                        <div class="mini-cell"><div class="mini-glyph">日</div><div class="mini-label">Day</div></div>
                        <div class="mini-cell"><div class="mini-glyph">時</div><div class="mini-label">Hour</div></div>
                      </div>
                      <div class="report-row"><span>Day Master</span><span>Jia Wood</span></div>
                      <div class="report-row"><span>Season</span><span>Spring</span></div>
                    </div>
                    <div class="report-page page-2" style="z-index:2;opacity:0;">
                      <div class="report-title">Career Path</div>
                      <div class="report-row"><span>Strength</span><span>Leadership · Fire</span></div>
                      <div class="report-row"><span>Wealth</span><span>Metal · Stable</span></div>
                      <div class="report-row"><span>Best Sector</span><span>Strategy · Growth</span></div>
                      <div class="report-row"><span>Next Window</span><span>Autumn 2026</span></div>
                    </div>
                    <div class="report-page page-3" style="z-index:1;opacity:0;">
                      <div class="report-title">Timing Table</div>
                      <div class="report-row"><span>2026 · Year of the Horse</span><span>Movement</span></div>
                      <div class="report-row"><span>July · Water Month</span><span>Clarity</span></div>
                      <div class="report-row"><span>August · Metal Month</span><span>Decision</span></div>
                      <div class="report-row"><span>October · Wood Month</span><span>Launch</span></div>
                    </div>
                  </div>
                </div>
            """))
        elif sid == "tiers":
            tiers = [
                ("Free Sample", "Free", "Your birth chart snapshot · element overview", colors["teal"], -600, "x"),
                ("Essential", "₩12,000", "Full chart reading · career · yearly timing", colors["gold"], 500, "y"),
                ("Deep Destiny", "₩39,000", "Everything + compatibility · 10-year luck cycles", colors["violet"], 600, "x"),
            ]
            panels = []
            for idx, (name, price, snippet, color, origin, axis) in enumerate(tiers):
                panels.append(
                    f'<div class="tier-panel tier-panel-{idx}" '
                    f'style="left:{idx*33.333:.3f}%;" data-axis="{axis}" data-from="{origin}">'
                    f'<div class="tier-name">{name}</div>'
                    f'<div class="tier-price">{price}</div>'
                    f'<div class="tier-snippet">{snippet}</div>'
                    f'<div class="tier-accent" style="background:{color};"></div>'
                    f'</div>'
                )
            parts.append(f'<div class="tier-stage">{"".join(panels)}</div>')
        elif sid == "compat":
            glyph_positions = [(-150, -120, "甲"), (150, -120, "乙"), (150, 120, "丙"), (-150, 120, "丁")]
            ring_glyphs = "\n".join(
                f'<div class="ring-glyph" style="left:calc(50% + {x}px);top:calc(50% + {y}px);">{g}</div>'
                for x, y, g in glyph_positions
            )
            parts.append(textwrap.dedent(f"""
                <div class="compat-stage">
                  <div class="chart-ring left" style="margin-right:-40px;">
                    {ring_glyphs}
                    <div class="ring-center">A</div>
                  </div>
                  <div class="connect-beam"></div>
                  <div class="chart-ring right" style="margin-left:-40px;">
                    {ring_glyphs}
                    <div class="ring-center">B</div>
                  </div>
                </div>
            """))
        elif sid == "brand":
            parts.append(textwrap.dedent("""
                <div class="brand-stage">
                  <div class="brand-mark">☽</div>
                  <div class="brand-text">Cosmic Saju</div>
                  <div class="brand-tag">Your Chart. Your Timing. Your Story.</div>
                  <div class="brand-cta">Discover Your Reading — cosmicsaju.com</div>
                </div>
            """))

        html = "\n".join(parts)
        return (
            f'<div class="scene" id="{sid}" data-start="{start:.3f}" '
            f'data-duration="{dur:.3f}">{html}</div>'
        )

    scene_blocks = "\n".join(scene_html(s) for s in scenes)

    gsap_script = textwrap.dedent("""
        window.__timelines = window.__timelines || {};
        const tl = gsap.timeline({ paused: true });
        const scenes = Array.from(document.querySelectorAll('.scene'));
        const total = parseFloat(document.querySelector('#root').dataset.duration);
        const particles = Array.from(document.querySelectorAll('.particle'));

        // Slow parallax drift for background particles.
        particles.forEach((p) => {
          const dx = parseFloat(p.dataset.dx) || 0;
          const dy = parseFloat(p.dataset.dy) || 0;
          const startOpacity = parseFloat(gsap.getProperty(p, 'opacity')) || 0.3;
          tl.fromTo(p,
            { x: -dx * 0.5, y: -dy * 0.5, opacity: startOpacity * 0.4 },
            { x: dx * 0.5, y: dy * 0.5, opacity: startOpacity, duration: total, ease: "none" },
            0
          );
        });

        scenes.forEach((scene, i) => {
          const start = parseFloat(scene.dataset.start);
          const dur = parseFloat(scene.dataset.duration);
          const end = start + dur;
          const nextStart = i + 1 < scenes.length ? parseFloat(scenes[i + 1].dataset.start) : total;
          // Fade the old scene out a bit earlier so the new scene's headline
          // does not ghost on top of the old scene's text during the cut.
          const fadeStart = Math.max(start, nextStart - 0.58);

          tl.set(scene, { opacity: 0 }, 0);
          tl.to(scene, { opacity: 1, duration: 0.42, ease: "power2.out" }, start);

          // Hero push-in.
          const hero = scene.querySelector('.hero-layer');
          if (hero) {
            tl.fromTo(hero, { scale: 1.1 }, { scale: 1, duration: dur, ease: "none" }, start);
          }

          // Staggered element reveals.
          const reveals = scene.querySelectorAll('.reveal');
          if (reveals.length) {
            tl.from(reveals, { opacity: 0, y: 45, duration: 0.65, stagger: 0.07, ease: "back.out(1.4)" }, start + 0.22);
          }

          // Element percentage bars.
          const bars = scene.querySelectorAll('.bar-fill');
          if (bars.length) {
            tl.fromTo(bars, { scaleY: 0 }, { scaleY: 1, duration: 1.0, stagger: 0.12, ease: "power2.out" }, start + 0.25);
          }

          // Tier panels slide and lock.
          const tiers = scene.querySelectorAll('.tier-panel');
          if (tiers.length) {
            tiers.forEach((panel, idx) => {
              const axis = panel.dataset.axis || 'x';
              const from = parseFloat(panel.dataset.from) || 0;
              const delay = idx * 0.1;
              const fromObj = axis === 'x' ? { x: from, opacity: 0 } : { y: from, opacity: 0 };
              tl.fromTo(panel, fromObj, { x: 0, y: 0, opacity: 1, duration: 0.85, ease: "back.out(1.2)" }, start + 0.1 + delay);
            });
          }

          // Report pages scroll/crossfade.
          const pages = scene.querySelectorAll('.report-page');
          if (pages.length) {
            pages.forEach((page, idx) => {
              const pageStart = start + 0.5 + idx * 1.1;
              tl.set(page, { opacity: 0 }, 0);
              if (idx === 0) {
                tl.to(page, { opacity: 1, duration: 0.5, ease: "power2.out" }, pageStart);
              } else {
                tl.to(pages[idx - 1], { opacity: 0, y: -60, duration: 0.6, ease: "power2.in" }, pageStart);
                tl.fromTo(page, { opacity: 0, y: 80 }, { opacity: 1, y: 0, duration: 0.7, ease: "power2.out" }, pageStart + 0.15);
              }
            });
          }

          // Compatibility charts move together and beam lights up.
          if (scene.id === 'compat') {
            const left = scene.querySelector('.chart-ring.left');
            const right = scene.querySelector('.chart-ring.right');
            const beam = scene.querySelector('.connect-beam');
            if (left && right && beam) {
              tl.fromTo(left, { x: -200, opacity: 0 }, { x: 0, opacity: 1, duration: 0.9, ease: "power2.out" }, start + 0.1);
              tl.fromTo(right, { x: 200, opacity: 0 }, { x: 0, opacity: 1, duration: 0.9, ease: "power2.out" }, start + 0.1);
              tl.fromTo(beam, { scaleX: 0, opacity: 0 }, { scaleX: 1, opacity: 0.95, duration: 0.8, ease: "power2.out" }, start + 0.7);
            }
          }

          // Brand reveal: logo forms, tagline + CTA fade in.
          if (scene.id === 'brand') {
            const mark = scene.querySelector('.brand-mark');
            const text = scene.querySelector('.brand-text');
            const tag = scene.querySelector('.brand-tag');
            const cta = scene.querySelector('.brand-cta');
            if (mark) tl.fromTo(mark, { scale: 0.6, opacity: 0, rotation: -15 }, { scale: 1, opacity: 1, rotation: 0, duration: 1.0, ease: "back.out(1.6)" }, start + 0.1);
            if (text) tl.fromTo(text, { opacity: 0, y: 40, letterSpacing: "24px" }, { opacity: 1, y: 0, letterSpacing: "4px", duration: 1.1, ease: "power2.out" }, start + 0.35);
            if (tag) tl.fromTo(tag, { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.9, ease: "power2.out" }, start + 0.95);
            if (cta) tl.fromTo(cta, { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.9, ease: "power2.out" }, start + 1.35);
          }

          // Cross-fade out to the next scene.
          tl.to(scene, { opacity: 0, duration: 0.35, ease: "power2.in" }, fadeStart);
        });

        window.__timelines["main"] = tl;
    """)

    return textwrap.dedent(f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=1920, height=1080" />
            <script src="gsap.min.js"></script>
            <style>
            {css}
            </style>
          </head>
          <body>
            <div id="root" data-composition-id="cosmic-saju" data-start="0" data-duration="{total_duration:.3f}" data-width="1920" data-height="1080">
              <div class="bg"></div>
              <div class="particles">{particles_html}</div>
              <div class="grain"></div>
              <div class="vignette"></div>
              {scene_blocks}
            </div>
            <script>
            {gsap_script}
            </script>
          </body>
        </html>
    """)


def render_hyperframes(project_dir: Path, output_path: Path) -> None:
    if os.getenv("REUSE_RENDER") and output_path.exists():
        print(f"render (reused): {output_path}")
        return
    run(
        [
            "hyperframes", "render", str(project_dir),
            "-o", str(output_path),
            "--fps", "30",
            "--resolution", "landscape",
            "--quality", "standard",
            "--quiet",
        ]
    )
    print(f"hyperframes render: {output_path} ({probe_duration(output_path):.2f}s)")


def generate_pad(path: Path, duration: float = 20.0) -> None:
    if os.getenv("REUSE_AUDIO") and path.exists():
        print(f"pad (reused): {path}")
        return
    expr = (
        "0.10*sin(2*PI*110*t)+"
        "0.07*sin(2*PI*164.81*t)+"
        "0.06*sin(2*PI*196*t)+"
        "0.05*sin(2*PI*261.63*t)+"
        "0.03*sin(2*PI*329.63*t)"
    )
    filters = (
        "lowpass=f=240,"
        "tremolo=5:0.35,"
        "aecho=0.7:0.8:1200|1800:0.35|0.25,"
        "pan=stereo|c0=0.6*c0|c1=0.6*c0,"
        "volume=0.5"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=48000:c=mono",
        "-t", str(duration),
        "-af", filters,
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(path),
    ]
    run(cmd)
    print(f"pad: {path} ({probe_duration(path):.2f}s)")


def generate_chime(path: Path, duration: float = 1.5) -> None:
    if os.getenv("REUSE_AUDIO") and path.exists():
        print(f"chime (reused): {path}")
        return
    expr = (
        "0.35*sin(2*PI*880*t)*exp(-2.8*t)+"
        "0.25*sin(2*PI*1320*t)*exp(-3.5*t)+"
        "0.12*sin(2*PI*1760*t)*exp(-4.0*t)"
    )
    filters = (
        "aecho=0.6:0.5:400|900:0.35|0.25,"
        "pan=stereo|c0=0.7*c0|c1=0.7*c0"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=48000:c=mono",
        "-t", str(duration),
        "-af", filters,
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(path),
    ]
    run(cmd)
    print(f"chime: {path} ({probe_duration(path):.2f}s)")


def mix_final(
    video_path: Path,
    pad_path: Path,
    chime_path: Path,
    output_path: Path,
) -> None:
    chime_times = [3.0, 6.0, 10.0, 13.0, 16.0]
    # Build a single chimes timeline, then mix pad + chimes with two inputs.
    # Using a two-step filtergraph avoids the large multi-input amix queue
    # that can stall loudnorm on some FFmpeg builds.
    chime_parts = ["[2:a]asplit=5[c0][c1][c2][c3][c4];"]
    delayed_labels: list[str] = []
    for idx, t in enumerate(chime_times):
        ms = int(t * 1000)
        label = f"ac{idx}"
        chime_parts.append(f"[c{idx}]adelay={ms}|{ms}[{label}];")
        delayed_labels.append(f"[{label}]")
    chime_parts.append(
        f"{''.join(delayed_labels)}amix=inputs=5:duration=longest:normalize=0[chimes];"
    )
    filter_complex = (
        "[0:v]copy[vout];"
        + "".join(chime_parts)
        + "[1:a][chimes]amix=inputs=2:duration=longest:normalize=0[mix];"
        + "[mix]loudnorm=I=-16:TP=-1.5:LRA=11[afinal]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(pad_path),
        "-i", str(chime_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[afinal]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run(cmd)
    print(f"final: {output_path} ({probe_duration(output_path):.2f}s)")


def main() -> int:
    ensure_dirs()

    project_dir = WORK_DIR / "hf_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    pad_path = WORK_DIR / "pad.wav"
    chime_path = WORK_DIR / "chime.wav"
    silent_video_path = WORK_DIR / "silent.mp4"
    final_path = RENDERS_DIR / "cosmic-saju-promo.mp4"

    # 1. HTML composition.
    asyncio.run(download_gsap(project_dir / "gsap.min.js"))
    html = build_html(SCENES)
    (project_dir / "index.html").write_text(html, encoding="utf-8")
    (project_dir / "hyperframes.json").write_text(
        json.dumps(
            {
                "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                "paths": {
                    "blocks": "compositions",
                    "components": "compositions/components",
                    "assets": "assets",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 2. Silent HyperFrames render.
    render_hyperframes(project_dir, silent_video_path)

    # 3. Audio synthesis.
    total_duration = max(s["start"] + s["duration"] for s in SCENES)
    generate_pad(pad_path, duration=total_duration)
    generate_chime(chime_path)

    # 4. Final mix.
    mix_final(silent_video_path, pad_path, chime_path, final_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
