#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# The code is MIT; the author's personal text inside it is not. See LICENSE.
"""Render the profile README's animated SVGs and its auto-updated sections.

Standard library only. Needs GITHUB_TOKEN. Everything about a project (About
text, stars, languages, latest release) is read from the repository through the
GitHub GraphQL API, so editing a repo updates the profile on the next run.
profile.json only chooses which repos get a card and which stay off the page.
If the API call fails the script exits non-zero before touching any file.

All text uses textLength, so columns line up whichever monospace font the
viewer's system falls back to. Animations sit behind prefers-reduced-motion,
and every element's resting state is its finished state.
"""
import datetime
import html
import json
import os
import random
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

USER = "Khanh1916"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
CONFIG = ROOT / "profile.json"
README = ROOT / "README.md"
RAW = f"https://raw.githubusercontent.com/{USER}/{USER}/main/assets"

# Stage two of the profile CTF. Ciphertext only: the plaintext is not in this repo.
CTF_CIPHER = "0b1d151316130647591a2b1c59031047032e0047011d2b1a5d2e441a5e0c"

FONT = "ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono','Liberation Mono',monospace"
CW = 0.6  # advance width of one character, in em
# Compact panels are scaled into a phone column of about 343px. At 400px wide, 13px text lands near 11px on
# screen; the old 480px landed near 9px.
COMPACT_W = 400

THEMES = {
    "dark": {
        "BG": "#121821", "PANEL": "#0b0f14", "BORDER": "#232c38", "LINE": "#2f3a48", "LED_OFF": "#18202b",
        "TEXT": "#d5dde8", "TITLE": "#f2f6fb", "GRID": "#9fb0c6", "FLASH": "#ffffff", "SOFT": "#93a1b3",
        "DIM": "#768599", "LED_SOFT": "#a3b1c4", "RED": "#ff5468", "BLUE": "#46adff", "GREEN": "#3ddc97",
        "ACCENT": "#e6edf6", "HEAT": ("#26313f", "#4d5e73", "#8fa3ba", "#e6edf6"),
    },
    "light": {
        "BG": "#e7ebf1", "PANEL": "#f4f6f9", "BORDER": "#cfd6e0", "LINE": "#bcc6d3", "LED_OFF": "#e1e6ed",
        "TEXT": "#1f2933", "TITLE": "#0e141b", "GRID": "#5b6b80", "FLASH": "#0e141b", "SOFT": "#4f5d6e",
        "DIM": "#5d6c82", "LED_SOFT": "#5b6b80", "RED": "#b82818", "BLUE": "#1c5fc4", "GREEN": "#0f6d41",
        "ACCENT": "#1f2a37", "HEAT": ("#c9d2de", "#8f9dae", "#4f5d6e", "#1f2a37"),
    },
}


def apply_theme(name):
    """Point the module-level colours, and the tables built from them, at one theme."""
    palette = THEMES[name]
    globals().update(palette)
    globals().update(
        COLOURS={"red": palette["RED"], "blue": palette["BLUE"], "green": palette["GREEN"], "accent": palette["ACCENT"]},
        LEVELS=dict(zip(("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"),
                        (palette["LED_OFF"], *palette["HEAT"]))),
        HERO_COLUMNS=[
            ("offense", palette["RED"], "Break systems to see how they fail."),
            ("defense", palette["BLUE"], "Harden them and catch the drift."),
            ("privacy", palette["GREEN"], "Learn from data without reading it."),
        ],
    )


apply_theme("dark")


# ---------------------------------------------------------------- primitives

def t(x, y, s, size, fill=None, bold=False, anchor="start", track=0.0, cls=""):
    """One run of text with a fixed advance per character. `fill` defaults to the current theme's TEXT."""
    if fill is None:  # resolved per call: a default of TEXT would freeze the dark theme at import
        fill = TEXT
    length = len(s) * size * (CW + track)
    if anchor == "middle":
        x -= length / 2
    elif anchor == "end":
        x -= length
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" textLength="{length:.1f}" lengthAdjust="spacingAndGlyphs"'
    if bold:
        attrs += ' font-weight="700"'
    if cls:
        attrs += f' class="{cls}"'
    return f'<text {attrs}>{escape(s)}</text>'


def runs(x, y, segments, size, cls=""):
    """Several coloured (and optionally bold) runs on one line, placed by character count."""
    parts, cursor = [], x
    for segment in segments:
        text, colour, *rest = segment
        bold = bool(rest and rest[0])
        if text.strip():
            parts.append(t(cursor, y, text, size, colour, bold=bold))
        cursor += len(text) * size * CW
    inner = "".join(parts)
    return f'<g class="{cls}">{inner}</g>' if cls else inner


def svg(w, h, body, base="", anim="", label="", comment="", fixed=True):
    size = f'width="{w}" height="{h}" ' if fixed else ""
    note = f"<!-- {comment} -->" if comment else ""
    style = f"text{{font-family:{FONT};white-space:pre}}{base}@media (prefers-reduced-motion:no-preference){{{anim}}}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" {size}viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="{escape(label, {chr(34): "&quot;"})}">{note}<style>{style}</style>{body}</svg>\n'
    )


def pct(v, total):
    return f"{v / total * 100:.3f}%"


def shown(name, on, off, total):
    """Opacity 0 until `on`, 1 until `off`, then 0 until the loop restarts."""
    a, b = on / total * 100, off / total * 100
    return (
        f"@keyframes {name}{{0%,{a:.3f}%{{opacity:0}}{a + 0.02:.3f}%,{b:.3f}%{{opacity:1}}"
        f"{b + 0.02:.3f}%,100%{{opacity:0}}}}.{name}{{animation:{name} {total}s linear infinite}}"
    )


def typed(name, x, y, chars, size, bg, total, clear_at, start, duration):
    """Typewriter reveal that starts visible at t=0, clears cleanly, and re-types in character steps."""
    length = chars * size * CW + 2
    top, height = y - size, size * 1.35
    a, b, c = (v / total * 100 for v in (clear_at, start, start + duration))
    body = (
        f'<clipPath id="{name}c"><rect x="{x}" y="{top:.1f}" width="{length:.1f}" height="{height:.1f}"/></clipPath>'
        f'<g clip-path="url(#{name}c)"><rect class="{name}" x="{x}" y="{top:.1f}" width="{length:.1f}" height="{height:.1f}" fill="{bg}"/></g>'
    )
    base = f".{name}{{transform:translateX({length:.1f}px)}}"
    anim = (
        f"@keyframes {name}{{0%,{(a - 0.5):.3f}%{{transform:translateX({length:.1f}px)}}"
        f"{a:.3f}%,{b:.3f}%{{transform:translateX(0);animation-timing-function:steps({chars},end)}}"
        f"{c:.3f}%,100%{{transform:translateX({length:.1f}px)}}}}"
        f".{name}{{animation:{name} {total}s linear infinite}}"
    )
    return body, base, anim


def panel_path(w, h, r_top=0, r_bot=0):
    """Outline of one slice of the window. An edge is only drawn where the window itself ends (radius > 0)."""
    top = f"V{r_top}Q.75 .75 {r_top} .75H{w - r_top}Q{w - .75} .75 {w - .75} {r_top}" if r_top else ""
    bottom = f"V{h - r_bot}Q{w - .75} {h - .75} {w - r_bot} {h - .75}H{r_bot}Q.75 {h - .75} .75 {h - r_bot}" if r_bot else ""
    if r_top and r_bot:
        return f"M.75 {h - r_bot}{top}{bottom}z"
    if r_top:
        return f"M.75 {h}{top}V{h}"
    if r_bot:
        return f"M{w - .75} 0{bottom}V0"
    return f"M.75 0V{h}M{w - .75} 0V{h}"


def window_frame(w, h, title="cao-thanh-lam@kali: ~", menu=False):
    """Top Kali terminal window cap: rounded top corners, open at the bottom into the next slice."""
    size, bar = 13, 62 if menu else 34
    parts = [
        f'<path d="{panel_path(w, h, r_top=12)}" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>',
        f'<path d="M1.5 {bar}V12Q1.5 1.5 12 1.5H{w - 12}Q{w - 1.5} 1.5 {w - 1.5} 12V{bar}z" fill="{BG}"/>',
        f'<path d="M1 {bar}H{w - 1}" stroke="{BORDER}" stroke-width="1.5"/>',
        t(w / 2, 23, title, size, TEXT, True, "middle"),
        # Xfce window as Kali ships it: terminal icon on the left, round buttons with a blue close on the right.
        f'<rect x="18" y="11" width="16" height="14" rx="2.5" fill="{PANEL}" stroke="{SOFT}" stroke-width="1.2"/>',
        f'<path d="M21.5 15l3 3-3 3M26.5 21.5h4" fill="none" stroke="{SOFT}" stroke-width="1.2"/>',
        f'<circle cx="{w - 68}" cy="18" r="7.5" fill="{LINE}"/><circle cx="{w - 45}" cy="18" r="7.5" fill="{LINE}"/>',
        f'<circle cx="{w - 22}" cy="18" r="7.5" fill="{BLUE}"/>',
        f'<path d="M{w - 71.5} 20.5h7M{w - 48} 15h6v6h-6z" fill="none" stroke="{TITLE}" stroke-width="1.2"/>',
        f'<path d="M{w - 25} 15l6 6M{w - 19} 15l-6 6" fill="none" stroke="{PANEL}" stroke-width="1.6"/>',
    ]
    if menu:
        parts.append(t(20, 50, "File  Actions  Edit  View  Help", size, SOFT))
    return "".join(parts)


def panel_frame(w, h, r_bot=0):
    """A slice of the terminal below the title bar: side edges only, plus the window's bottom when `r_bot` is set."""
    # The fill is its own rect: filling an open outline would paint a wedge between two separate side edges.
    fill = "" if r_bot else f'<rect width="{w}" height="{h}" fill="{PANEL}"/>'
    paint = PANEL if r_bot else "none"
    return fill + f'<path d="{panel_path(w, h, r_bot=r_bot)}" fill="{paint}" stroke="{BORDER}" stroke-width="1.5"/>'


# ---------------------------------------------------------------- hero terminal

SERVICES = [
    ("22/tcp", "open", "ssh", "Linux / Bash / PowerShell / hardening", "Linux / Bash / PowerShell"),
    ("1521/tcp", "open", "oracle-tns", "Oracle / PHP / SQLi / logic flaws", "Oracle / PHP / SQLi"),
    ("1883/tcp", "open", "mqtt", "IoT intrusion detection", "IoT intrusion detection"),
    ("2375/tcp", "open", "docker", "Docker / Compose / GitHub Actions", "Docker / GitHub Actions"),
    ("3306/tcp", "open", "mysql", "MySQL / SQL", "MySQL / SQL"),
    ("6006/tcp", "open", "ml-stack", "PyTorch / scikit-learn / pandas / NumPy", "PyTorch / scikit / pandas"),
    ("6443/tcp", "open", "kubernetes", "Kubernetes / multi-tenant labs", "Kubernetes"),
    ("9092/tcp", "open", "flower", "Flower / Opacus / DP / SecAgg+", "Flower / Opacus / SecAgg+"),
]


PLAN = (
    "Notes on the technology at hand: things built, tools explored and problems followed "
    "from the first command to the result."
)


def msf_prompt(x, y, size, module=None):
    """Single-line Metasploit console prompt, returning (svg, command_x)."""
    segs = [("msf6", TEXT), (" ", TEXT)]
    if module:
        segs += [("auxiliary(", TEXT), (module, RED, True), (") ", TEXT)]
    segs.append(("> ", TEXT))
    cmd_x = x + sum(len(s[0]) for s in segs) * size * CW
    # Metasploit underlines the program name. A drawn line keeps its width exact in any font.
    underline = f'<path d="M{x} {y + 3}h{4 * size * CW:.1f}" stroke="{TEXT}" stroke-width="1"/>'
    return underline + runs(x, y, segs, size), cmd_x


def exec_notice(command):
    """What msfconsole prints before handing an unknown command to the shell."""
    return [("[*]", BLUE, True), (f" exec: {command}", TEXT)]


def stack_lines(compact):
    dirs = [
        ("languages/", "go python bash powershell php sql" if compact else "go  python  bash  powershell  php  sql"),
        ("infra/", "linux docker kubernetes gh-actions" if compact else "linux  docker  kubernetes  github-actions"),
        ("data/", "mysql oracle" if compact else "mysql  oracle"),
    ]
    rows = [[(f"{name:<12}", LED_SOFT), (items, TEXT)] for name, items in dirs]
    if compact:
        rows += [[(f"{'ml/':<12}", LED_SOFT), ("pytorch flower opacus scikit-learn", TEXT)], [(" " * 12 + "pandas numpy", TEXT)]]
    else:
        rows += [[(f"{'ml/':<12}", LED_SOFT), ("pytorch  flower  opacus  scikit-learn  pandas  numpy", TEXT)]]
    return rows


def kali_prompt(x, y, size, line_h):
    """The two-line zsh prompt of Kali, without a command. The command starts four cells into line two."""
    cell = size * CW
    user = "cao-thanh-lam"
    host_x = x + (4 + len(user)) * cell
    first = (
        t(x, y, "\u250c\u2500\u2500(", size, GREEN)
        + t(x + 4 * cell, y, user, size, BLUE, True)
        # The Kali separator is a wide glyph: give it two cells of its own.
        + f'<text x="{host_x:.1f}" y="{y:.1f}" font-size="{size}" fill="{BLUE}" textLength="{2 * cell:.1f}" lengthAdjust="spacingAndGlyphs">\u327f</text>'
        + runs(host_x + 2 * cell, y, [("kali", BLUE), (")-[", GREEN), ("~", TITLE), ("]", GREEN)], size)
    )
    second = runs(x, y + line_h, [("\u2514\u2500", GREEN), ("$", BLUE)], size)
    return first + second


NK_MONOGRAM_DESKTOP = (
    "▀▀█▄     ▀▀█▀▀      ",
    "  █▀▄      █        ",
    "  █ ▀▄     █  ▀▀█▀▀ ",
    "  █  ▀▄    █   ▄▀   ",
    "  █   ▀▄   █ ▄▀     ",
    "▄▄█▄▄  ▀▄  █ ▀▄     ",
    "       █▀█    ▀▄    ",
    "      ▄▄█▄▄   ▄▄█▄▄ ",
)
NK_MONOGRAM_COMPACT = (
    "▀▀█▄  ▀▀█▀▀      ",
    "  █▀▄   █  ▀▀█▀▀ ",
    "  █ ▀▄  █ ▄▀     ",
    "▄▄█▄▄▀▄ █  ▀▄    ",
    "     ▄▄█▄▄ ▄▄█▄  ",
)


def monogram_splash(x, y, size, line_h, art):
    """Interlocking NK ASCII monogram with a calm Kali-blue phosphor scan sweep."""
    parts = [t(x, y + row * line_h, line, size, TITLE) for row, line in enumerate(art)]
    beam_lines = "".join(t(x, y + row * line_h, line, size, BLUE) for row, line in enumerate(art))
    art_w = len(art[0]) * size * CW
    art_h = len(art) * line_h + 6
    parts.append(
        f'<clipPath id="nkc"><rect class="nkb" x="{x:.1f}" y="{y - size:.1f}" width="44" height="{art_h:.1f}"/></clipPath>'
        f'<g class="nkg" clip-path="url(#nkc)">{beam_lines}</g>'
    )
    base = ".nkg{opacity:0}"
    anim = (
        ".nkg{opacity:.92}"
        f"@keyframes nkb{{0%{{transform:translateX(-50px)}}55%,100%{{transform:translateX({art_w + 15:.1f}px)}}}}"
        ".nkb{animation:nkb 4.5s ease-in-out infinite}"
    )
    return "".join(parts), base, anim


def msf_box_row(x, y, size, segments, total_width=52, close_colour=None, close_bold=False):
    """Draw a Metasploit banner row with the closing bracket pinned to an exact column."""
    close_x = x + (total_width - 1) * size * CW
    return runs(x, y, segments, size) + t(close_x, y, "]", size, close_colour or DIM, bold=close_bold)


def banner(compact, x, y, matches):
    """NK ASCII monogram paired with a Metasploit banner statistics block."""
    if compact:
        size, line_h = 13, 19
        parts = []
        art_svg, base, anim = monogram_splash(x, y, size, line_h, NK_MONOGRAM_COMPACT)
        parts.append(art_svg)
        rx = x + 19 * size * CW
        right_rows = [
            ("CAO THANH LAM", TITLE, True),
            ("Cybersecurity researcher", TEXT, False),
            ("a.k.a. Nguyen Nam Khanh", LED_SOFT, False),
            ("───────────────────────────", DIM, False),
            ("southkhanh@gmail.com", BLUE, False),
        ]
        for idx, (text, colour, bold) in enumerate(right_rows):
            parts.append(t(rx, y + idx * line_h, text, size, colour, bold))
        y += len(NK_MONOGRAM_COMPACT) * line_h + 10
        open_count = sum(1 for s in SERVICES if s[1] == "open")
        parts.append(msf_box_row(x, y, size, [(f"=[ khanh1916 · {open_count} open · {matches} modules", ACCENT, True)], 42, ACCENT, True))
        y += line_h
        parts.append(msf_box_row(x, y, size, [
            ("+ -- --=[ ", DIM),
            ("offense", RED), (" | ", DIM), ("defense", BLUE), (" | ", DIM), ("privacy", GREEN),
        ], 42))
        return "".join(parts), base, anim, y + 4

    size, line_h = 16, 21
    art_svg, base, anim = monogram_splash(x, y, size, line_h, NK_MONOGRAM_DESKTOP)
    parts = [art_svg]
    rx = x + 22 * size * CW
    open_count = sum(1 for s in SERVICES if s[1] == "open")
    box_w = 52
    parts.append(msf_box_row(rx, y, size, [("=[ khanh1916 · Kali Research Profile", ACCENT, True)], box_w, ACCENT, True))
    box_rows = [
        [("+ -- --=[ ", DIM), ("CAO THANH LAM", TITLE, True), ("  ·  Nguyen Nam Khanh", LED_SOFT)],
        [("+ -- --=[ ", DIM), ("role: Cybersecurity researcher", TEXT)],
        [("+ -- --=[ ", DIM), ("themes: ", SOFT), ("offense", RED), (" | ", DIM), ("defense", BLUE), (" | ", DIM), ("privacy", GREEN)],
        [("+ -- --=[ ", DIM), (f"{open_count} services open · {matches} featured modules", SOFT)],
        [("+ -- --=[ ", DIM), ("contact: ", SOFT), ("southkhanh@gmail.com", BLUE)],
    ]
    for idx, segs in enumerate(box_rows, start=1):
        parts.append(msf_box_row(rx, y + idx * line_h, size, segs, box_w))
    return "".join(parts), base, anim, y + (len(NK_MONOGRAM_DESKTOP) - 1) * line_h


def hero(compact, latest, matches=3):
    """The whole top of the page: Kali QTerminal launching msfconsole with NK banner, .plan, services and stack."""
    w = COMPACT_W if compact else 860
    size, line_h, x = (13, 19, 20) if compact else (16, 21, 20)
    cell = size * CW
    wrap = int((w - 2 * x) / cell)
    open_count = sum(1 for s in SERVICES if s[1] == "open")

    def wrapped(text, colour):
        """One output row per terminal line: a real terminal wraps long output too."""
        return [[(line, colour)] for line in textwrap.wrap(text, wrap, break_long_words=False)]

    if compact:
        table = [[(f"{'PORT':<10}{'SERVICE':<11}STACK", DIM)]]
    else:
        table = [[(f"{'PORT':<11}{'STATE':<10}{'SERVICE':<13}VERSION", DIM)]]
    for port, state, service, long_text, short_text in SERVICES:
        hidden = state != "open"
        colour = ACCENT if hidden else GREEN
        if compact:
            table.append([(f"{port:<10}", LED_SOFT), (f"{service:<11}", colour), (short_text, ACCENT if hidden else TEXT)])
        else:
            table.append([(f"{port:<11}", LED_SOFT), (f"{state:<10}", colour), (f"{service:<13}", TEXT), (long_text, ACCENT if hidden else SOFT)])

    # The module context does not fit beside the command on a phone, so only the wide layout shows it.
    port_mod = None if compact else "scanner/portscan/khanh1916"
    scan = "nmap -sV khanh1916"
    blocks = [
        ("cat ~/.plan", [[(line, TEXT)] for line in textwrap.wrap(PLAN, wrap)] + [[], [("# You're the pentester now.", DIM)]], None),
        (scan, [
            *wrapped("Starting service inventory for Cao Thanh Lam", SOFT),
            *wrapped(f"Host is up. Latest push: {latest}", SOFT),
            [],
            *table,
            [],
            *wrapped("Service Info: OS: Linux, Windows, Cisco IOS, Junos", SOFT),
            [(f"Status: 1 researcher audited, {open_count} services open", SOFT)],
        ], port_mod),
        ("ls ~/stack", stack_lines(compact), None),
    ]

    parts = []
    # Top Kali zsh prompt launching msfconsole
    y = 84
    parts.append(kali_prompt(x, y, size, line_h) + t(x + 4 * cell, y + line_h, "msfconsole", size, TITLE))
    y += line_h + 24

    intro, base, anim, y = banner(compact, x, y, matches)
    parts.append(intro)
    gap = 9 if compact else 11
    spacer = 8 if compact else 9
    for command, lines, module in blocks:
        y += line_h + gap
        prompt_svg, cmd_x = msf_prompt(x, y, size, module=module)
        parts.append(prompt_svg + t(cmd_x, y, command, size, TITLE))
        for segments in [exec_notice(command), [], *lines]:
            if segments:
                y += line_h
                parts.append(runs(x, y, segments, size))
            else:
                y += spacer
    h = y + 20
    services = ", ".join(s[2] for s in SERVICES if s[1] == "open")
    names = "go, python, bash, powershell, php, sql, linux, docker, kubernetes, github-actions, mysql, oracle, pytorch, flower, opacus, scikit-learn, pandas, numpy"
    label = (
        "Cao Thanh Lam, a.k.a. Nguyen Nam Khanh. Cybersecurity researcher: break systems to see how they fail, harden them and catch the drift, and learn from data without reading it. "
        f"A Kali terminal running msfconsole with an NK monogram banner. {PLAN} Contact: southkhanh@gmail.com. "
        f"The service table lists open services: {services}. The stack directory lists {names}."
    )
    comment = f"31337/tcp stage 2: {CTF_CIPHER}"
    return svg(w, h, window_frame(w, h, menu=True) + "".join(parts), base, anim, label, comment, fixed=False)


def nuclei_panel(compact, matches):
    """msfconsole module search header introducing the featured finding cards below."""
    w = COMPACT_W if compact else 860
    size, line_h, x = (13, 20, 20) if compact else (16, 23, 20)
    command = "search type:finding" if compact else "search type:finding author:Khanh1916"
    y = 32
    prompt_svg, cmd_x = msf_prompt(x, y, size)
    parts = [prompt_svg, t(cmd_x, y, command, size, TITLE)]
    y += 6 + line_h
    parts.append(t(x, y, "Matching Modules", size, TEXT, True))
    y += line_h
    parts.append(t(x, y, "================", size, DIM))
    y += line_h
    status = f"{matches} matched. Select a card below to inspect." if compact else f"{matches} modules matched. Select a card below to inspect."
    parts.append(runs(x, y, [("[*] ", BLUE, True, ), (status, TEXT if not compact else SOFT)], size, "pulse"))
    h = y + 18
    anim = "@keyframes pulse{0%,100%{opacity:1}50%{opacity:.65}}.pulse{animation:pulse 3.2s ease-in-out infinite}"
    return svg(
        w, h, panel_frame(w, h) + "".join(parts), "", anim,
        f"msfconsole module search: {matches} featured finding modules matched below.",
        fixed=False,
    )


# ---------------------------------------------------------------- finding cards

CARD_W, CARD_H = 400, 260  # compact vertical module block
CARD_DESKTOP_W, CARD_DESKTOP_H = 860, 168  # desktop msfconsole module slice


def fit_langs(edges, limit):
    """Join language names on ' / ' without splitting any name mid-word."""
    names = [e["node"]["name"] for e in edges]
    out = ""
    for name in names:
        candidate = f"{out} / {name}" if out else name
        if len(candidate) <= limit:
            out = candidate
        elif not out:
            out = name[:limit]
            break
        else:
            break
    return out


def summarize(text, width=44, max_lines=3):
    """Fit a repo's About text: first sentence, cut at a clause or word if needed."""
    text = " ".join((text or "").split())
    if not text:
        return []
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    lines = textwrap.wrap(first, width)
    if len(lines) <= max_lines:
        return lines
    heads = [first[:m.start()] for m in re.finditer(r"[:;,]", first)]
    heads = [h for h in heads if len(h) >= 30 and len(textwrap.wrap(h, width)) <= max_lines]
    if heads:
        return textwrap.wrap(heads[-1], width)
    lines = lines[:max_lines]
    lines[-1] = lines[-1][:width - 1].rstrip() + "…"
    return lines


def use_line(y, index, langs):
    """The `msf6 > use N` line of a module card, with the repo's languages as a comment."""
    prompt_svg, cmd_x = msf_prompt(20, y, 13)
    segs = [(f"use {index}", BLUE)] + ([(f"  # {langs}", DIM)] if langs else [])
    return prompt_svg + runs(cmd_x, y, segs, 13)


def card(index, repo, tag, colour, body, base, anim, compact=True):
    """One msfconsole module entry showing description, language use command, and TUI telemetry."""
    name, stars = repo["name"], repo["stargazerCount"]
    edges = repo["languages"]["edges"]
    release = (repo.get("latestRelease") or {}).get("tagName")
    mod_idx = index - 1
    mod_prefix = f"[{mod_idx}] "
    mod_ns = f"finding/{tag.lower()}/"

    if compact:
        lines = summarize(repo.get("description"), width=44, max_lines=3) or ["No description yet."]
        langs = fit_langs(edges, 30)  # the prompt leaves 30 of the 46 columns for languages
        parts = [
            runs(20, 28, [(mod_prefix, DIM), (mod_ns, colour), (name, TITLE, True)], 13),
        ]
        if stars:
            parts.append(t(CARD_W - 20, 28, f"★ {stars}", 13, ACCENT, anchor="end"))
        for i, line in enumerate(lines):
            parts.append(t(20, 52 + i * 18, line, 13.5, SOFT))
        parts.append(f'<path d="M20 106H{CARD_W - 20}" stroke="{BORDER}" stroke-width="1"/>')
        parts.append(f'<g transform="translate(0,4)">{body}</g>')
        parts.append(use_line(246, mod_idx, langs))
        frame = panel_frame(CARD_W, CARD_H)
        return svg(CARD_W, CARD_H, frame + "".join(parts), base, anim, f"{name}: {' '.join(lines)}", fixed=False)

    lines = summarize(repo.get("description"), width=48, max_lines=3) or ["No description yet."]
    langs = fit_langs(edges, 34)
    head_segs = [(mod_prefix, DIM), (mod_ns, colour), (name, TITLE, True)]
    if release and len(mod_prefix) + len(mod_ns) + len(name) + len(release) + 4 <= 43:
        head_segs.append((f"  ({release})", DIM))
    parts = [runs(20, 32, head_segs, 15)]
    if stars:
        parts.append(t(444, 32, f"★ {stars}", 13.5, ACCENT, anchor="end"))
    for i, line in enumerate(lines):
        parts.append(t(20, 60 + i * 20, line, 14, TEXT))
    parts.append(use_line(146, mod_idx, langs))
    parts.append(f'<path d="M460 16V152" stroke="{BORDER}" stroke-width="1"/>')
    parts.append(f'<g transform="translate(460,-84)">{body}</g>')
    frame = panel_frame(CARD_DESKTOP_W, CARD_DESKTOP_H)
    return svg(CARD_DESKTOP_W, CARD_DESKTOP_H, frame + "".join(parts), base, anim, f"{name}: {' '.join(lines)}", fixed=False)


def anim_languages(repo):
    """Default animation for a featured repo without one of its own."""
    edges = repo["languages"]["edges"][:4]
    size = sum(e["size"] for e in edges) or 1
    body, anim, x = "", "", 20.0
    for i, edge in enumerate(edges):
        width = 360 * edge["size"] / size
        colour = edge["node"]["color"] or SOFT
        body += f'<rect class="lb{i}" x="{x:.1f}" y="150" width="{width:.1f}" height="14" fill="{colour}" style="transform-origin:{x:.1f}px 157px"/>'
        anim += (
            f"@keyframes lb{i}{{0%,{i * 8}%{{transform:scaleX(0)}}{i * 8 + 12}%,96%{{transform:scaleX(1)}}100%{{transform:scaleX(0)}}}}"
            f".lb{i}{{animation:lb{i} 8s ease-out infinite}}"
        )
        share = f"{edge['node']['name'][:9]} {edge['size'] * 100 // size}%"
        body += t(20 + i * 92, 186, share, 11, SOFT)
        x += width
    return body, "", anim


def anim_oracle(repo):
    """SQLi probe box and terminal bracket module indicators inside [20..380, 114..228]."""
    total = 10
    payload = "' UNION SELECT user,pw FROM users--"
    px = 28 + 2 * 12 * CW
    body = runs(20, 126, [("[probe] ", DIM), ("sqlmap --technique=U", SOFT)], 12)
    body += f'<rect x="20" y="135" width="360" height="26" rx="3" fill="{BG}" stroke="{BORDER}"/>'
    body += runs(28, 152, [("> ", RED, True), (payload, TITLE)], 12)
    cover, base, anim = typed("pay", px, 152, len(payload), 12, BG, total, 3.8, 4.1, 2.2)
    body += cover
    chips = (("[+C1:SQLI]", 20), ("[+C2:LOGIC]", 106.4), ("[+C3:SUPPLY]", 200), ("[+C4:PLSQL]", 300.8))
    for i, (label, cx) in enumerate(chips):
        body += t(cx, 188, label, 12, SOFT)
        body += f'<g class="ch{i}">{t(cx, 188, label, 12, RED, True)}</g>'
        anim += shown(f"ch{i}", 6.4 + i * 0.7, 9.6, total)
    body += t(20, 216, f"[*] status: {len(chips)} classes armed · CTF target ready", 12, SOFT)
    return body, base, anim


def anim_secbaseline(repo):
    """149-control matrix and pass/drift TUI telemetry inside [20..380, 114..228]."""
    match = re.search(r"(\d+) CIS-style", repo.get("description") or "")
    controls = int(match.group(1)) if match else 149
    total = 10
    columns = 30
    cell, gap = 9, 3
    x0, y0 = 20, 136
    rng = random.Random(149)
    drifted = set(rng.sample(range(controls), 3))
    body = runs(20, 126, [("[audit] ", DIM), (f"{controls} CIS controls", TEXT)], 12)
    body += f'<g class="dr">{t(380, 126, "[! 3 DRIFT]", 12, RED, True, "end")}</g>'
    # One loop: scan column by column, then the PASS count, then the drifted controls.
    scan_start, scan_time, hold = 0.4, 4.0, 9.6
    scanned, found = [""] * columns, ""
    for i in range(controls):
        cx = x0 + (i % columns) * (cell + gap)
        cy = y0 + (i // columns) * (cell + gap)
        box = f'x="{cx}" y="{cy}" width="{cell}" height="{cell}" rx="1.5"'
        body += f'<rect {box} fill="{LED_OFF}"/>'
        scanned[i % columns] += f'<rect {box} fill="{GRID}"/>'
        if i in drifted:
            found += f'<rect {box} fill="{RED}"/>'
    anim = ""
    for column, rects in enumerate(scanned):
        body += f'<g class="sc{column}">{rects}</g>'
        anim += shown(f"sc{column}", scan_start + column * scan_time / columns, hold, total)
    body += f'<g class="dr">{found}</g>'
    y = y0 + ((controls + columns - 1) // columns) * (cell + gap) + 20
    # The bar is drawn with rects: block glyphs differ in height from one fallback font to another.
    prefix, slots = "PASS [", 14
    bar_x, slot = 20 + len(prefix) * 12 * CW, 12 * CW
    body += t(20, y, prefix, 12, BLUE, True)
    body += (
        f'<g class="bar" style="transform-origin:{bar_x:.1f}px {y - 4}px">'
        f'<rect x="{bar_x:.1f}" y="{y - 9}" width="{(slots - 1) * slot:.1f}" height="10" fill="{BLUE}"/>'
        f'<rect x="{bar_x + (slots - 1) * slot:.1f}" y="{y - 9}" width="{slot:.1f}" height="10" fill="{BLUE}" fill-opacity=".3"/>'
        "</g>"
    )
    a, b, c = (v / total * 100 for v in (scan_start, scan_start + scan_time, hold))
    anim += (
        f"@keyframes bar{{0%,{a:.2f}%{{transform:scaleX(0)}}{b:.2f}%,{c:.2f}%{{transform:scaleX(1)}}"
        f"{c + 0.02:.2f}%,100%{{transform:scaleX(0)}}}}.bar{{animation:bar {total}s linear infinite}}"
    )
    body += t(bar_x + slots * slot, y, "]", 12, BLUE, True)
    body += f'<g class="rs">{t(bar_x + (slots + 2) * slot, y, f"{controls - 3}/{controls}", 12, BLUE, True)}</g>'
    anim += shown("rs", scan_start + scan_time + 0.2, hold, total)
    body += f'<g class="dr">{t(380, y, "3 regressions", 12, RED, True, "end")}</g>'
    anim += shown("dr", 5.4, hold, total)
    return body, "", anim


def anim_federated(repo):
    """Privacy-preserving SecAgg+ -> synthetic -> IDS pipeline inside [20..380, 114..228]."""
    total = 6
    rng = random.Random(1883)
    box_x, box_w, mid = 128, 78, 165
    base = ".mv{opacity:0}"
    anim = (
        "@keyframes nz{0%,100%{opacity:1}50%{opacity:.35}}.nz{animation:nz 1.6s ease-in-out infinite}"
        "@keyframes fl{to{stroke-dashoffset:-14}}.fl{animation:fl .7s linear infinite}"
    )
    body = runs(20, 126, [("[secagg+] ", DIM), ("DP ε-noise · local raw locked", GREEN)], 12)
    for gateway in range(5):
        y = 138 + gateway * 11
        hot = rng.sample(range(4), 2)
        body += f'<line x1="68" y1="{y + 3.5}" x2="{box_x}" y2="{mid}" stroke="{LINE}" stroke-width="1.1"/>'
        for column in range(4):
            x = 20 + column * 11
            if column in hot:
                body += (
                    f'<rect class="nz" x="{x}" y="{y}" width="9" height="7" rx="1" fill="{GREEN}" '
                    f'fill-opacity="{rng.uniform(0.6, 1):.2f}" style="animation-delay:{-rng.uniform(0, 1.6):.2f}s"/>'
                )
            else:
                body += f'<rect class="q" x="{x}" y="{y}" width="9" height="7" rx="1" fill="{GREEN}" fill-opacity=".18"/>'
        dx, dy = box_x - 77, mid - y - 3.5
        anim += (
            f"@keyframes mv{gateway}{{0%{{transform:translate(0,0);opacity:0}}10%,85%{{opacity:1}}"
            f"100%{{transform:translate({dx}px,{dy}px);opacity:0}}}}"
            f".mv{gateway}{{animation:mv{gateway} 2.4s linear infinite}}"
        )
        for offset in (0, 1.2):
            body += f'<rect class="mv mv{gateway}" x="68" y="{y + 2}" width="8" height="3" rx="1" fill="{ACCENT}" style="animation-delay:{-(gateway * 0.4 + offset):.2f}s"/>'

    body += f'<rect x="{box_x}" y="149" width="{box_w}" height="32" rx="4" fill="{GREEN}" fill-opacity=".14" stroke="{GREEN}"/>'
    body += t(box_x + box_w / 2, 169, "SecAgg+", 12.5, TITLE, True, "middle")
    body += f'<path class="fl" d="M{box_x + box_w + 2} {mid}h20" stroke="{GREEN}" stroke-width="1.5" stroke-dasharray="3 4"/>'

    diagonals = [""] * 8
    for row in range(5):
        y = 145 + row * 8
        for column in range(4):
            diagonals[row + column] += (
                f'<rect x="{230 + column * 12}" y="{y}" width="10" height="5" rx="1" fill="{GREEN}" fill-opacity="{rng.uniform(0.45, 1):.2f}"/>'
            )
    for step, cells in enumerate(diagonals):
        body += f'<g class="sy{step}">{cells}</g>'
        anim += shown(f"sy{step}", 0.6 + step * 0.3, 5.6, total)

    body += f'<path class="fl" d="M280 {mid}h28" stroke="{ACCENT}" stroke-width="1.5" stroke-dasharray="3 4"/>'
    body += f'<rect x="312" y="150" width="54" height="30" rx="4" fill="{BG}" stroke="{BORDER}"/>'
    body += f'<g class="ids"><rect x="312" y="150" width="54" height="30" rx="4" fill="{ACCENT}" fill-opacity=".22" stroke="{ACCENT}"/></g>'
    body += t(339, 169, "IDS", 12.5, TITLE, True, "middle")
    anim += shown("ids", 3.2, 5.6, total)

    body += t(20, 216, "node[0..4] -> noisy sum -> synth -> train", 12, SOFT)
    return body, base, anim


def anim_lunar(repo):
    total = 9
    body, anim = "", ""
    body += t(30, 133, "AI  (China's calendar, UTC+8)", 12, SOFT)
    body += t(30, 157, "03/02/2030", 22, RED, True)
    strike_end = 26 + 10 * 22 * CW + 8
    body += f'<line class="st" x1="26" y1="150" x2="{strike_end:.0f}" y2="150" stroke="{RED}" stroke-width="2.5"/>'
    base = ".st{transform-origin:26px 150px}"
    a, b = 2.2 / total * 100, 3.0 / total * 100
    anim += (
        f"@keyframes st{{0%,{a:.2f}%{{transform:scaleX(0)}}{b:.2f}%,96%{{transform:scaleX(1)}}100%{{transform:scaleX(0)}}}}"
        f".st{{animation:st {total}s ease-out infinite}}"
    )
    body += f'<g class="vn">{t(30, 176, "Vietnam  (UTC+7)", 12, SOFT)}{t(30, 200, "02/02/2030", 22, GREEN, True)}</g>'
    anim += shown("vn", 3.6, 8.6, total)
    body += t(380, 157, "Tết 2030", 15, TITLE, True, "end")
    body += t(380, 178, "23:09 in Hanoi", 12, ACCENT, anchor="end")
    body += t(380, 195, "00:09 in Beijing", 12, ACCENT, anchor="end")
    return body, base, anim


ANIMATIONS = {"oracle": anim_oracle, "secbaseline": anim_secbaseline, "federated": anim_federated, "lunar": anim_lunar}


# ---------------------------------------------------------------- activity



def activity(calendar, events, compact):
    """Recent public GitHub events via tcpdump inside msf6, then the year as a capture map."""
    w, margin, size, line_h = (COMPACT_W, 20, 13, 20) if compact else (860, 20, 16, 22)
    chars = int((w - 2 * margin) / (size * CW))
    weeks = calendar["weeks"]
    days = [day for week in weeks for day in week["contributionDays"]]
    total = calendar["totalContributions"]
    active = sum(1 for d in days if d["contributionCount"])
    peak = max((d["contributionCount"] for d in days), default=0)
    best = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        best = max(best, run)
    prompt_svg, cmd_x = msf_prompt(margin, 34, size)
    command = "tcpdump -i github -n host Khanh1916"
    parts = [prompt_svg, t(cmd_x, 34, command, size, TITLE), runs(margin, 34 + line_h, exec_notice(command), size)]
    y = 42 + line_h
    rows = events or ["(no public packets captured)"]
    for event in rows:
        y += line_h
        # Compact drops the date, the seconds and the "IP user" noise so the type and size still fit in 46 columns.
        text = re.sub(r"^(\d\d:\d\d):\d\d IP \S+ > ", r"\1 > ", event[6:]) if compact and events else event
        clipped = text if len(text) <= chars else text[:chars - 1] + "…"
        parts.append(t(margin, y, clipped, size, SOFT))
    y += line_h + 10
    burst = "day burst" if compact else "day longest burst"
    gap = " " if compact else "  "  # a four-digit total must still fit the 400px compact panel
    parts.append(runs(margin, y, [(f"{total}", ACCENT), (f" packets{gap}", SOFT), (f"{active}", ACCENT), (f" active days{gap}", SOFT),
                                  (f"{best}", ACCENT), (f" {burst}{gap}", SOFT), (f"{peak}", ACCENT), (" peak", SOFT)], size - 1))
    if not compact:
        sw_x = w - margin - 102
        parts.append(t(sw_x - 6, y, "less", 12, DIM, anchor="end"))
        for idx, col in enumerate((LED_OFF, *HEAT)):
            parts.append(f'<rect x="{sw_x + idx * 12}" y="{y - 9}" width="9" height="9" rx="1.5" fill="{col}"/>')
        parts.append(t(w - margin, y, "more", 12, DIM, anchor="end"))
    pitch = (w - 2 * margin) / len(weeks)
    cell = round(pitch * 0.8, 2)
    y0 = y + 16
    duration = 9
    for column, week in enumerate(weeks):
        delay = column / len(weeks) * duration
        x = margin + column * pitch
        for day in week["contributionDays"]:
            cy = y0 + day["weekday"] * pitch
            parts.append(f'<rect x="{x:.2f}" y="{cy:.2f}" width="{cell}" height="{cell}" rx="1.5" fill="{LEVELS[day["contributionLevel"]]}"/>')
            if day["contributionCount"]:
                parts.append(f'<rect class="f" x="{x:.2f}" y="{cy:.2f}" width="{cell}" height="{cell}" rx="1.5" fill="{FLASH}" style="animation-delay:{delay:.2f}s"/>')
    grid_h = 7 * pitch
    parts.append(f'<rect class="bm" x="{margin - 3}" y="{y0 - 4:.1f}" width="2.5" height="{grid_h + 6:.1f}" fill="{FLASH}"/>')
    h = int(y0 + grid_h + 18)
    frame = panel_frame(w, h)
    base = ".f,.bm{opacity:0}"
    anim = (
        f"@keyframes f{{0%{{opacity:.92}}7%,100%{{opacity:0}}}}.f{{animation:f {duration}s linear infinite}}"
        f"@keyframes bm{{0%{{transform:translateX(0);opacity:.9}}99%{{opacity:.9}}100%{{transform:translateX({w - 2 * margin}px);opacity:0}}}}"
        f".bm{{animation:bm {duration}s linear infinite}}"
    )
    label = f"tcpdump output of recent public GitHub events, then a contribution map: {total} contributions, {active} active days, longest run of {best} days, peak of {peak} in a day."
    return svg(w, h, frame + "".join(parts), base, anim, label, fixed=False)


# ---------------------------------------------------------------- sign-off

SIGNOFF = "You can’t trust code that you did not totally create yourself."


def signoff(compact):
    """Exit msf6 cleanly back to Kali zsh, print ~/.trust via cat, and rest on an idle prompt cursor."""
    w, size, line_h, x = (COMPACT_W, 13, 19, 20) if compact else (860, 16, 22, 20)
    wrap = int((w - 2 * x) / (size * CW))
    lines = textwrap.wrap(SIGNOFF, wrap)
    y = 28 if compact else 32
    prompt_svg, cmd_x = msf_prompt(x, y, size)
    parts = [prompt_svg, t(cmd_x, y, "exit", size, TITLE)]
    y += line_h + (5 if compact else 6)
    cell = size * CW
    parts.append(kali_prompt(x, y, size, line_h) + t(x + 4 * cell, y + line_h, "cat ~/.trust", size, TITLE))
    y += 2 * line_h + 4
    for line in lines:
        parts.append(t(x, y, line, size, ACCENT, True))
        y += line_h
    y += 8
    parts.append(kali_prompt(x, y, size, line_h))
    y += line_h
    parts.append(f'<rect class="bl" x="{x + 4 * cell:.1f}" y="{y - size + 2}" width="{cell:.1f}" height="{size + 2}" fill="{TEXT}"/>')
    h = y + 20
    frame = panel_frame(w, h, r_bot=12)
    anim = "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"
    return svg(w, h, frame + "".join(parts), "", anim, f"msf6 exit, then cat ~/.trust prints: {SIGNOFF}", fixed=False)


# ---------------------------------------------------------------- main

def api_json(request, attempts=4):
    """Open a GitHub API request and parse the JSON, retrying rate limits and 5xx errors with backoff."""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:  # before URLError, which it subclasses
            if (error.code != 429 and error.code < 500) or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
        time.sleep(2 ** attempt)


def fetch():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks { contributionDays { contributionCount contributionLevel weekday } }
          }
        }
        repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, orderBy: {field: PUSHED_AT, direction: DESC}) {
          nodes {
            name url description stargazerCount isFork isArchived pushedAt
            latestRelease { tagName }
            languages(first: 5, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name color } } }
          }
        }
      }
    }"""
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"login": USER}}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": f"{USER}-profile"},
    )
    payload = api_json(request)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    user = (payload.get("data") or {}).get("user")
    if not user:
        sys.exit(f"GitHub returned no data for user {USER}")
    return user


def fetch_events(visible):
    """Latest public events on the profile's own visible repos, as tcpdump lines (UTC+7)."""
    request = urllib.request.Request(
        f"https://api.github.com/users/{USER}/events/public?per_page=100",
        headers={"Authorization": f"bearer {os.environ['GITHUB_TOKEN']}", "User-Agent": f"{USER}-profile"},
    )
    payload = api_json(request)
    lines = []
    for event in payload:
        owner, _, name = event["repo"]["name"].partition("/")
        if owner != USER or name not in visible:
            continue
        when = datetime.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) + datetime.timedelta(hours=7)
        size = (event.get("payload") or {}).get("size")
        length = f", length {size}" if size else ""
        lines.append((when, f"{when:%m-%d %H:%M:%S} IP {USER.lower()} > {name}: {event['type']}{length}"))
    # The API is not strictly ordered and the text starts with MM-DD, which sorts wrongly
    # across New Year, so order by the real timestamp. tcpdump prints oldest first.
    return [line for _, line in sorted(lines)[-5:]]


def themed_link(href, stem, alt):
    """One full-width card link switching by colour scheme and viewport width (light desktop is the fallback)."""
    # One line on purpose: whitespace inside a link renders as a stray underlined space on GitHub.
    return (
        f'<div><a href="{href}"><picture>'
        f'<source media="(prefers-color-scheme: dark) and (max-width: 600px)" srcset="{RAW}/{stem}-compact.svg">'
        f'<source media="(max-width: 600px)" srcset="{RAW}/{stem}-compact-light.svg">'
        f'<source media="(prefers-color-scheme: dark)" srcset="{RAW}/{stem}.svg">'
        f'<img src="{RAW}/{stem}-light.svg" width="100%" alt="{alt}" align="top"/>'
        f'</picture></a></div>'
    )


def replace_block(text, name, block):
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        sys.exit(f"README is missing the {name} markers")
    return pattern.sub(lambda _: f"{start}\n\n{block}\n\n{end}", text)


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    user = fetch()
    hidden = set(config["hidden"]) | {USER, ".github"}
    visible = [r for r in user["repositories"]["nodes"]
               if not r["isFork"] and not r["isArchived"] and r["name"] not in hidden]
    by_name = {r["name"]: r for r in visible}
    latest = f"{visible[0]['name']} ({visible[0]['pushedAt'][:10]})" if visible else "none"

    calendar = user["contributionsCollection"]["contributionCalendar"]
    events = fetch_events(set(by_name))
    matches = sum(1 for entry in config["featured"] if entry["repo"] in by_name)
    files, links = {}, []
    for theme, suffix in (("dark", ""), ("light", "-light")):
        apply_theme(theme)
        files.update({
            f"hero{suffix}.svg": hero(False, latest, matches),
            f"hero-compact{suffix}.svg": hero(True, latest, matches),
            f"nuclei{suffix}.svg": nuclei_panel(False, matches),
            f"nuclei-compact{suffix}.svg": nuclei_panel(True, matches),
            f"activity{suffix}.svg": activity(calendar, events, False),
            f"activity-compact{suffix}.svg": activity(calendar, events, True),
            f"signoff{suffix}.svg": signoff(False),
            f"signoff-compact{suffix}.svg": signoff(True),
        })
        index = 0
        for entry in config["featured"]:
            repo = by_name.get(entry["repo"])
            if repo is None:
                if not suffix:
                    print(f"warning: featured repo {entry['repo']} is not public or is hidden", file=sys.stderr)
                continue
            index += 1
            draw = ANIMATIONS.get(entry.get("animation"), anim_languages)
            stem = f"card-{repo['name'].lower()}"
            colour = COLOURS.get(entry.get("color"), SOFT)
            files[f"{stem}{suffix}.svg"] = card(index, repo, entry["tag"], colour, *draw(repo), compact=False)
            files[f"{stem}-compact{suffix}.svg"] = card(index, repo, entry["tag"], colour, *draw(repo), compact=True)
            if not suffix:
                alt = html.escape(f"{repo['name']}: {' '.join(summarize(repo.get('description')))}", quote=True)
                links.append(themed_link(repo["url"], stem, alt))

    readme = README.read_text(encoding="utf-8")
    readme = replace_block(readme, "findings", "\n".join(links))
    README.write_text(readme, encoding="utf-8", newline="\n")

    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("*.svg"):
        if stale.name not in files:
            stale.unlink()
    for name, content in files.items():
        (OUT / name).write_text(content, encoding="utf-8", newline="\n")
        print(f"{name:28} {len(content.encode()) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
