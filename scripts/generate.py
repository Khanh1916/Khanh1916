#!/usr/bin/env python3
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

THEMES = {
    "dark": {
        "BG": "#121821", "PANEL": "#0b0f14", "BORDER": "#232c38", "LINE": "#2f3a48", "LED_OFF": "#18202b",
        "TEXT": "#d5dde8", "TITLE": "#f2f6fb", "GRID": "#9fb0c6", "FLASH": "#ffffff", "SOFT": "#93a1b3",
        "DIM": "#728196", "LED_SOFT": "#a3b1c4", "RED": "#ff5468", "BLUE": "#46adff", "GREEN": "#3ddc97",
        "ACCENT": "#e6edf6", "HEAT": ("#26313f", "#4d5e73", "#8fa3ba", "#e6edf6"),
    },
    "light": {
        "BG": "#e7ebf1", "PANEL": "#f4f6f9", "BORDER": "#cfd6e0", "LINE": "#bcc6d3", "LED_OFF": "#e1e6ed",
        "TEXT": "#1f2933", "TITLE": "#0e141b", "GRID": "#5b6b80", "FLASH": "#0e141b", "SOFT": "#4f5d6e",
        "DIM": "#66748a", "LED_SOFT": "#5b6b80", "RED": "#c5301f", "BLUE": "#1c5fc4", "GREEN": "#127a4a",
        "ACCENT": "#1f2a37", "HEAT": ("#c9d2de", "#8f9dae", "#4f5d6e", "#1f2a37"),
    },
}


def apply_theme(name):
    """Point the module-level colours, and the tables built from them, at one theme."""
    palette = THEMES[name]
    globals().update(palette)
    globals().update(
        LED_BLACK=palette["PANEL"], LED_ACCENT=palette["ACCENT"], LED_TEXT=palette["TEXT"],
        COLOURS={"red": palette["RED"], "blue": palette["BLUE"], "green": palette["GREEN"], "accent": palette["ACCENT"]},
        LEVELS=dict(zip(("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"),
                        (palette["LED_OFF"], *palette["HEAT"]))),
        HERO_COLUMNS=[
            ("RED / OFFENSE", palette["RED"], "I break systems", "to see how they fail."),
            ("BLUE / DEFENSE", palette["BLUE"], "I harden them", "and catch the drift."),
            ("GREEN / PRIVACY", palette["GREEN"], "I learn from data", "without reading it."),
        ],
    )


apply_theme("dark")


# ---------------------------------------------------------------- primitives

def t(x, y, s, size, fill=TEXT, bold=False, anchor="start", track=0.0, cls=""):
    """One run of text with a fixed advance per character."""
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
    """Several coloured runs on one line, placed by character count."""
    parts, cursor = [], x
    for text, colour in segments:
        if text.strip():
            parts.append(t(cursor, y, text, size, colour))
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


def typed(name, x, y, chars, size, bg, total, start, duration, end):
    """Typewriter reveal: a clipped cover slides right in character-sized steps."""
    length = chars * size * CW + 2
    top, height = y - size, size * 1.35
    a, b, c = (v / total * 100 for v in (start, start + duration, end))
    body = (
        f'<clipPath id="{name}c"><rect x="{x}" y="{top:.1f}" width="{length:.1f}" height="{height:.1f}"/></clipPath>'
        f'<g clip-path="url(#{name}c)"><rect class="{name}" x="{x}" y="{top:.1f}" width="{length:.1f}" height="{height:.1f}" fill="{bg}"/></g>'
    )
    base = f".{name}{{transform:translateX({length:.1f}px)}}"
    anim = (
        f"@keyframes {name}{{0%,{a:.3f}%{{transform:translateX(0);animation-timing-function:steps({chars},end)}}"
        f"{b:.3f}%,{c:.3f}%{{transform:translateX({length:.1f}px)}}{c + 0.02:.3f}%,100%{{transform:translateX(0)}}}}"
        f".{name}{{animation:{name} {total}s linear infinite}}"
    )
    return body, base, anim


# ---------------------------------------------------------------- hero

FONT5X7 = {
    "N": "10001 11001 10101 10011 10001 10001 10001", "G": "01110 10001 10000 10111 10001 10001 01110",
    "U": "10001 10001 10001 10001 10001 10001 01110", "Y": "10001 10001 01010 00100 00100 00100 00100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "A": "01110 10001 10001 11111 10001 10001 10001",
    "M": "10001 11011 10101 10101 10001 10001 10001", "K": "10001 10010 10100 11000 10100 10010 10001",
    "H": "10001 10001 10001 11111 10001 10001 10001", "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000", "S": "01111 10000 10000 01110 00001 00001 11110",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "L": "10000 10000 10000 10000 10000 10000 11111",
    "X": "10001 10001 01010 00100 01010 10001 10001", "D": "11110 10001 10001 10001 10001 10001 11110",
    "R": "11110 10001 10001 11110 10100 10010 10001", "8": "01110 10001 10001 01110 10001 10001 01110",
    "T": "11111 00100 00100 00100 00100 00100 00100", "C": "01110 10001 10000 10000 10000 10001 01110",
    "F": "11111 10000 10000 11110 10000 10000 10000", "W": "10001 10001 10001 10101 10101 11011 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "B": "11110 10001 10001 11110 10001 10001 11110",
    "-": "00000 00000 00000 11111 00000 00000 00000",
}


def led_board(x0, y0, pitch, dot, columns, rows, lines, total):
    """A dot-matrix panel. lines: (text, first column, first row)."""
    board = (
        f'<defs><pattern id="led" x="{x0}" y="{y0}" width="{pitch}" height="{pitch}" patternUnits="userSpaceOnUse">'
        f'<rect width="{dot}" height="{dot}" fill="{LED_OFF}"/></pattern></defs>'
        f'<rect x="{x0}" y="{y0}" width="{columns * pitch:.1f}" height="{rows * pitch:.1f}" fill="url(#led)"/>'
    )
    anim, index = "", 0
    for text, start_column, start_row in lines:
        for position, letter in enumerate(text):
            if letter == " ":
                continue
            glyph = FONT5X7[letter].split()
            squares = "".join(
                f"M{x0 + (start_column + position * 6 + c) * pitch:.1f} {y0 + (start_row + r) * pitch:.1f}h{dot}v{dot}h-{dot}z"
                for r in range(7) for c in range(5) if glyph[r][c] == "1"
            )
            board += f'<path class="k{index}" d="{squares}" fill="{LED_ACCENT}"/>'
            anim += shown(f"k{index}", 0.3 + index * 0.1, total - 0.6, total)
            index += 1
    return board, anim


def led_wire(width, y, margin):
    """Attack packets stop at the shield; only noised squares continue."""
    left, right, cx = margin, width - margin, width // 2
    run_in, run_out = cx - 18 - left - 12, right - cx - 24
    body = f'<rect x="{left}" y="{y - .75}" width="{right - left}" height="1.5" fill="{LINE}"/>'
    anim = (
        f"@keyframes pk{{0%{{transform:translateX(0);opacity:0}}4%,94%{{opacity:1}}100%{{transform:translateX({run_in}px);opacity:0}}}}"
        ".pk{animation:pk 3s linear infinite}"
    )
    for i in range(5):
        body += f'<rect class="pk" x="{left}" y="{y - 3}" width="12" height="6" fill="{RED}" style="animation-delay:{-i * .6:.1f}s"/>'
    for n, (a, b, c) in enumerate(((-8, 7, -4), (9, -9, 5), (-6, 8, 0))):
        anim += (
            f"@keyframes o{n}{{0%{{transform:translate(0,0);opacity:0}}4%,94%{{opacity:1}}30%{{transform:translate({run_out * .3:.0f}px,{a}px)}}"
            f"65%{{transform:translate({run_out * .65:.0f}px,{b}px)}}100%{{transform:translate({run_out}px,{c}px);opacity:0}}}}"
            f".o{n}{{animation:o{n} 3s linear infinite}}"
        )
    for i in range(5):
        body += f'<rect class="ot o{i % 3}" x="{cx + 24}" y="{y - 2.5}" width="5" height="5" fill="{GREEN}" style="animation-delay:{-i * .6:.1f}s"/>'
    body += (
        f'<path d="M{cx} {y - 22}l17 6v13c0 13-10 21-17 25c-7-4-17-12-17-25v-13z" fill="{LED_BLACK}" stroke="{LED_ACCENT}" stroke-width="2.5"/>'
        f'<path d="M{cx - 8} {y}l6 6l11-12" fill="none" stroke="{LED_ACCENT}" stroke-width="2.5" stroke-linecap="square"/>'
    )
    return body, ".pk,.ot{opacity:0}", anim


def hero(compact):
    total = 12
    if compact:
        w, h = 480, 452
        board, anim = led_board(44, 50, 7.4, 5.9, 53, 17, [("CAO THANH", 0, 1), ("LAM", 18, 9)], total)
        label_y, aka_y, wire_y = 34, 196, 238
    else:
        w, h = 860, 322
        board, anim = led_board(45, 50, 10, 8, 77, 9, [("CAO THANH LAM", 0, 1)], total)
        label_y, aka_y, wire_y = 36, 160, 192
    cx = w / 2
    parts = [f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{LED_BLACK}" stroke="{BORDER}" stroke-width="1.5"/>', board]
    parts.append(t(cx, label_y, "CYBERSECURITY RESEARCHER", 13 if compact else 15, LED_ACCENT, anchor="middle", track=0.12))
    parts.append(t(cx, aka_y, "a.k.a. Nguyen Nam Khanh  /  @Khanh1916", 14 if compact else 16, LED_SOFT, anchor="middle"))
    wire, base, wire_anim = led_wire(w, wire_y, 24 if compact else 36)
    parts.append(wire)
    anim += wire_anim
    for i, (label, colour, first, second) in enumerate(HERO_COLUMNS):
        if compact:
            y = 292 + i * 56
            parts.append(f'<rect x="30" y="{y - 10}" width="9" height="9" fill="{colour}"/>')
            parts.append(t(47, y, label, 13, colour, True, track=0.06))
            parts.append(t(30, y + 25, f"{first} {second}", 15, LED_TEXT))
        else:
            x = (150, 430, 710)[i]
            width = len(label) * 14 * 0.68
            parts.append(f'<rect x="{x - width / 2 - 14:.1f}" y="236" width="9" height="9" fill="{colour}"/>')
            parts.append(t(x + 2, 245, label, 13.5, colour, True, "middle", 0.08))
            parts.append(t(x, 274, first, 19, LED_TEXT, anchor="middle"))
            parts.append(t(x, 299, second, 19, LED_TEXT, anchor="middle"))
    if not compact:
        parts.append(f'<rect class="u" x="110" y="310" width="80" height="2" fill="{LED_ACCENT}"/>')
        base += ".u{opacity:0}"
        anim += (
            "@keyframes u{0%,33.3%{transform:translateX(0);opacity:1}33.4%,66.6%{transform:translateX(280px);opacity:1}"
            "66.7%,100%{transform:translateX(560px);opacity:1}}.u{animation:u 9s linear infinite}"
        )
    label = "Cao Thanh Lam, a.k.a. Nguyen Nam Khanh. Cybersecurity researcher: I break systems to see how they fail, I harden them and catch the drift, and I learn from data without reading it."
    comment = f"31337/tcp stage 2: {CTF_CIPHER}"
    return svg(w, h, "".join(parts), base, anim, label, comment, fixed=False)


# ---------------------------------------------------------------- nmap terminal

SERVICES = [
    ("22/tcp", "open", "ssh", "Linux / Bash / PowerShell / hardening", "Linux / Bash / PowerShell"),
    ("1521/tcp", "open", "oracle-tns", "Oracle / PHP / SQLi / logic flaws", "Oracle / PHP / SQLi"),
    ("1883/tcp", "open", "mqtt", "IoT intrusion detection", "IoT intrusion detection"),
    ("2375/tcp", "open", "docker", "Docker / Compose / GitHub Actions", "Docker / GitHub Actions"),
    ("3306/tcp", "open", "mysql", "MySQL / SQL", "MySQL / SQL"),
    ("6006/tcp", "open", "ml-stack", "PyTorch / scikit-learn / pandas / NumPy", "PyTorch / sklearn / pandas"),
    ("6443/tcp", "open", "kubernetes", "Kubernetes / multi-tenant labs", "Kubernetes"),
    ("9092/tcp", "open", "flower", "Flower / Opacus / DP / SecAgg+", "Flower / Opacus / SecAgg+"),
    ("31337/tcp", "filtered", "flag?", "see the end of this page", "see the end of this page"),
]


PLAN = (
    "I attack systems to learn how they fail, then build the tools that stop it happening again. "
    "Lately I've been stuck on a harder question: how do you train an intrusion detector on traffic "
    "nobody is allowed to see?"
)


def command_line(index, x, y, command, size, total, start):
    """A prompt and a command that types itself. Returns (svg, base css, animation css, finished at)."""
    prompt_x = x + 2 * size * CW
    duration = max(0.4, len(command) * 0.045)
    row = runs(x, y, [("$ ", ACCENT)], size) + t(prompt_x, y, command, size, TITLE)
    cover, base, anim = typed(f"c{index}", prompt_x, y, len(command), size, PANEL, total, start + 0.2, duration, total - 1)
    anim += shown(f"g{index}", start, total - 1, total)
    return f'<g class="g{index}">{row}</g>{cover}', base, anim, start + 0.2 + duration + 0.25


def timeline_length(blocks, per_line):
    moment = 0.5
    for command, lines in blocks:
        moment += 0.2 + max(0.4, len(command) * 0.045) + 0.25 + len(lines) * per_line
    return moment


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


def session(compact, latest):
    """whoami, id, .plan, nmap and ls as one terminal window."""
    w = 480 if compact else 860
    size, line_h, x = (13, 20, 20) if compact else (16, 23, 20)
    wrap = int((w - 2 * x) / (size * CW))
    open_count = sum(1 for s in SERVICES if s[1] == "open")
    if compact:
        table = [[(f"{'PORT':<11}{'SERVICE':<12}STACK", DIM)]]
    else:
        table = [[(f"{'PORT':<11}{'STATE':<10}{'SERVICE':<13}VERSION", DIM)]]
    for port, state, service, long_text, short_text in SERVICES:
        hidden = state != "open"
        colour = ACCENT if hidden else GREEN
        if compact:
            table.append([(f"{port:<11}", LED_SOFT), (f"{service:<12}", colour), (short_text, ACCENT if hidden else TEXT)])
        else:
            table.append([(f"{port:<11}", LED_SOFT), (f"{state:<10}", colour), (f"{service:<13}", TEXT), (long_text, ACCENT if hidden else SOFT)])
    blocks = [
        ("whoami", [[("cao-thanh-lam", TEXT)]]),
        ("id", [[("uid=1916(cao-thanh-lam) groups=red,blue,privacy", TEXT)]]),
        ("cat ~/.plan", [[(line, TEXT)] for line in textwrap.wrap(PLAN, wrap)] + [[], [("contact: southkhanh@gmail.com", TEXT)], [], [("# You're the pentester now.", DIM)]]),
        ("nmap -sV github.com/Khanh1916", [
            [("Starting Nmap against Cao Thanh Lam", SOFT)],
            [(f"Host is up. Latest push: {latest}", SOFT)],
            [],
            *table,
            [],
            [("Service Info: OS: Linux, Windows, Cisco IOS, Junos", SOFT)],
            [(f"Nmap done: 1 human scanned, {open_count} services open", SOFT)],
        ]),
        ("ls ~/stack", stack_lines(compact)),
    ]
    per_line = 0.11
    total = round(timeline_length(blocks, per_line) + 11)
    top = 44
    y = top + 28 - line_h
    parts, base, anim, moment = [], "", "", 0.5
    index = 0
    for command, lines in blocks:
        y += line_h
        row, row_base, row_anim, moment = command_line(index, x, y, command, size, total, moment)
        parts.append(row)
        base += row_base
        anim += row_anim
        index += 1
        for segments in lines:
            y += line_h
            moment += per_line
            if segments:
                parts.append(runs(x, y, segments, size, f"l{index}"))
                anim += shown(f"l{index}", moment, total - 1, total)
                index += 1
    y += line_h
    prompt_x = x + 2 * size * CW
    last = runs(x, y, [("$ ", ACCENT)], size) + f'<rect class="bl" x="{prompt_x:.1f}" y="{y - size + 2}" width="{size * CW:.1f}" height="{size + 2}" fill="{TEXT}"/>'
    parts.append(f'<g class="end">{last}</g>')
    anim += shown("end", moment + 0.2, total - 1, total)
    anim += "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"
    h = y + 22
    frame = [
        f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>',
        f'<path d="M1 {top}H{w - 1}" stroke="{BORDER}" stroke-width="1.5"/>',
    ]
    for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):  # macOS window controls
        frame.append(f'<circle cx="{22 + i * 20}" cy="22" r="6" fill="{colour}"/>')
    frame.append(t(w / 2, 27, "session: Khanh1916", 13, DIM, anchor="middle"))
    services = ", ".join(s[2] for s in SERVICES if s[1] == "open")
    names = "go, python, bash, powershell, php, sql, linux, docker, kubernetes, github-actions, mysql, oracle, pytorch, flower, opacus, scikit-learn, pandas, numpy"
    label = f"A terminal session. whoami prints cao-thanh-lam. {PLAN} Contact: southkhanh@gmail.com. An nmap scan shows open services: {services}; port 31337 is filtered and marked flag. The stack directory lists {names}."
    return svg(w, h, "".join(frame + parts), base, anim, label, fixed=False)


def command_panel(command, lines, compact, label):
    """A single command and its output, framed like every other panel."""
    w = 480 if compact else 860
    size, line_h, x = (13, 20, 20) if compact else (16, 23, 20)
    per_line = 0.15
    total = round(timeline_length([(command, lines)], per_line) + 9)
    y = 32
    parts, base, anim, moment = [], "", "", 0.5
    row, base, anim, moment = command_line(0, x, y, command, size, total, moment)
    parts.append(row)
    for i, segments in enumerate(lines):
        y += line_h
        moment += per_line
        if segments:
            parts.append(runs(x, y, segments, size, f"l{i}"))
            anim += shown(f"l{i}", moment, total - 1, total)
    h = y + 20
    frame = f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>'
    return svg(w, h, frame + "".join(parts), base, anim, label, fixed=False)


def nuclei_panel(compact, matches):
    return command_panel(
        "nuclei -u github.com/Khanh1916 -severity all",
        [[("[INF] ", LED_SOFT), (f"Scan completed. {matches} matches found.", TEXT)]],
        compact, f"nuclei scan of the profile: {matches} matches found, shown as the project cards below.",
    )


def support_panel(compact):
    width = 56 if compact else 85
    text = [
        "If my labs or tools helped you learn or build something, you can support me.",
        "Thank you!",
        "International: GitHub Sponsors preferred. Ko-fi for card or PayPal, no GitHub needed.",
        "Vietnam: MoMo preferred.",
    ]
    lines = [[(line, TEXT)] for paragraph in text for line in textwrap.wrap(paragraph, width)]
    return command_panel("./support", lines, compact, " ".join(text))


# ---------------------------------------------------------------- finding cards

CARD_W, CARD_H = 400, 263


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


def card(index, repo, tag, colour, body, base, anim):
    name, stars = repo["name"], repo["stargazerCount"]
    lines = summarize(repo.get("description")) or ["No description yet."]
    languages = " / ".join(e["node"]["name"] for e in repo["languages"]["edges"])
    release = (repo.get("latestRelease") or {}).get("tagName")
    marker = f"{release}  F-{index:02d}" if release else f"F-{index:02d}"
    parts = [
        f'<rect x=".75" y=".75" width="{CARD_W - 1.5}" height="{CARD_H - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>',
        f'<rect x="20" y="17" width="{len(tag) * 7.2 + 16:.1f}" height="20" rx="4" fill="{colour}" fill-opacity=".14"/>',
        t(28, 31, tag, 12, colour, True),
        t(CARD_W - 20, 31, marker, 12, DIM, anchor="end"),
        t(20, 66, name, 23, TITLE, True),
    ]
    if stars:
        parts.append(t(20 + len(name) * 23 * CW + 12, 66, f"★ {stars}", 14, ACCENT))
    for i, line in enumerate(lines):
        parts.append(t(20, 90 + i * 19, line, 13.5, SOFT))
    parts.append(f'<g transform="translate(0,19)">{body}</g>')
    parts.append(t(20, 248, languages[:50], 12, DIM))
    return svg(CARD_W, CARD_H, "".join(parts), base, anim, f"{name}: {' '.join(lines)}")


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
    total = 10
    payload = "' UNION SELECT user,pw FROM users--"
    x, y = 32, 150
    body = f'<rect x="20" y="131" width="360" height="29" rx="5" fill="{BG}" stroke="{BORDER}"/>' + t(x, y, payload, 13, RED)
    cover, base, anim = typed("pay", x, y, len(payload), 13, BG, total, 0.4, 2.4, 9.3)
    body += cover
    chips = [("C1 SQLI", 20, 100), ("C2 LOGIC", 128, 108), ("C3 SUPPLY CHAIN", 244, 136)]
    for i, (text, cx, width) in enumerate(chips):
        body += f'<rect x="{cx}" y="172" width="{width}" height="28" rx="5" fill="{BG}" stroke="{LINE}"/>'
        body += t(cx + width / 2, 190, text, 12, DIM, anchor="middle")
        lit = t(cx + width / 2, 190, text, 12, TITLE, True, "middle")
        body += f'<g class="ch{i}"><rect x="{cx}" y="172" width="{width}" height="28" rx="5" fill="{RED}" fill-opacity=".3" stroke="{RED}"/>{lit}</g>'
        anim += shown(f"ch{i}", 3.3 + i * 1.4, 9.3, total)
    return body, base, anim


def anim_secbaseline(repo):
    match = re.search(r"(\d+) CIS-style", repo.get("description") or "")
    controls = int(match.group(1)) if match else 149
    total = 10
    columns = 30
    cell, gap = 9, 3
    x0, y0 = 20, 130
    rng = random.Random(149)
    drifted = set(rng.sample(range(controls), 3))
    body = ""
    base = ".dr{opacity:0}"
    anim = "@keyframes dr{0%,74%{opacity:0}75%,90%{opacity:1}91%,100%{opacity:0}}.dr{animation:dr 10s linear infinite}"
    for i in range(controls):
        cx = x0 + (i % columns) * (cell + gap)
        cy = y0 + (i // columns) * (cell + gap)
        delay = i / controls * 4.0
        body += f'<rect class="q" x="{cx}" y="{cy}" width="{cell}" height="{cell}" rx="2" fill="{GRID}" style="animation-delay:{delay:.2f}s"/>'
        if i in drifted:
            body += f'<rect class="dr" x="{cx}" y="{cy}" width="{cell}" height="{cell}" rx="2" fill="{RED}"/>'
    anim += "@keyframes qq{0%{fill:" + LED_OFF + "}4%,100%{fill:" + GRID + "}}.q{animation:qq 10s linear infinite}"
    rows =(controls + columns - 1) // columns
    y = y0 + rows * (cell + gap) + 16
    body += t(20, y, f"$ checklist diff  # {controls} controls", 12, SOFT)
    note = t(380, y, "3 regressions", 12, RED, True, "end")
    body += f'<g class="dr">{note}</g>'
    return body, base, anim


def anim_federated(repo):
    """Count tables stay on the gateways; only masked vectors reach SecAgg+."""
    total = 6
    rng = random.Random(1883)
    box_x, box_w, mid = 130, 84, 159
    base = ".mv{opacity:0}"
    anim = (
        "@keyframes nz{0%,100%{opacity:1}50%{opacity:.35}}.nz{animation:nz 1.6s ease-in-out infinite}"
        "@keyframes fl{to{stroke-dashoffset:-14}}.fl{animation:fl .7s linear infinite}"
    )
    body = t(20, 122, "+ ε noise", 11, GREEN) + t(110, 122, "masked", 10.5, ACCENT, anchor="middle")
    for gateway in range(5):
        y = 128 + gateway * 13
        hot = rng.sample(range(4), 2)  # non-IID: each gateway sees only some classes
        body += f'<line x1="70" y1="{y + 5}" x2="{box_x}" y2="{mid}" stroke="{LINE}" stroke-width="1.2"/>'
        for column in range(4):
            x = 20 + column * 13
            if column in hot:
                body += (
                    f'<rect class="nz" x="{x}" y="{y}" width="11" height="10" rx="1.5" fill="{GREEN}" '
                    f'fill-opacity="{rng.uniform(0.6, 1):.2f}" style="animation-delay:{-rng.uniform(0, 1.6):.2f}s"/>'
                )
            else:
                body += f'<rect class="q" x="{x}" y="{y}" width="11" height="10" rx="1.5" fill="{GREEN}" fill-opacity=".16"/>'
        dx, dy = box_x - 79, mid - y - 5
        anim += (
            f"@keyframes mv{gateway}{{0%{{transform:translate(0,0);opacity:0}}10%,85%{{opacity:1}}"
            f"100%{{transform:translate({dx}px,{dy}px);opacity:0}}}}"
            f".mv{gateway}{{animation:mv{gateway} 2.4s linear infinite}}"
        )
        for offset in (0, 1.2):
            body += f'<rect class="mv mv{gateway}" x="70" y="{y + 3}" width="9" height="4" rx="1" fill="{ACCENT}" style="animation-delay:{-(gateway * 0.4 + offset):.2f}s"/>'

    body += f'<rect x="{box_x}" y="141" width="{box_w}" height="36" rx="6" fill="{GREEN}" fill-opacity=".14" stroke="{GREEN}"/>'
    body += t(box_x + box_w / 2, 164, "SecAgg+", 13, TITLE, True, "middle")
    body += f'<path class="fl" d="M{box_x + box_w + 2} {mid}h22" stroke="{GREEN}" stroke-width="1.5" stroke-dasharray="3 4"/>'

    for row in range(5):
        y = 139 + row * 9
        cells = "".join(
            f'<rect x="{240 + column * 14}" y="{y}" width="12" height="6" rx="1" fill="{GREEN}" fill-opacity="{rng.uniform(0.35, 1):.2f}"/>'
            for column in range(4)
        )
        body += f'<g class="sy{row}">{cells}</g>'
        anim += shown(f"sy{row}", 0.6 + row * 0.5, 5.6, total)

    body += f'<path class="fl" d="M297 {mid}h29" stroke="{ACCENT}" stroke-width="1.5" stroke-dasharray="3 4"/>'
    body += f'<rect x="328" y="143" width="52" height="32" rx="6" fill="{BG}" stroke="{BORDER}"/>'
    body += f'<g class="ids"><rect x="328" y="143" width="52" height="32" rx="6" fill="{ACCENT}" fill-opacity=".22" stroke="{ACCENT}"/></g>'
    body += t(354, 164, "IDS", 13, TITLE, True, "middle")
    anim += shown("ids", 3.2, 5.6, total)

    body += t(20, 205, "raw stays local", 11, SOFT)
    body += t(box_x + box_w / 2, 205, "noisy sum", 11, SOFT, anchor="middle")
    body += t(267, 205, "synthetic", 11, SOFT, anchor="middle")
    body += t(354, 205, "train", 11, SOFT, anchor="middle")
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
    """Recent public GitHub events as tcpdump lines, then the year as a capture map."""
    w, margin, size, line_h = (480, 20, 13, 20) if compact else (860, 32, 15, 22)
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
    parts = [runs(margin, 34, [("$ ", ACCENT), ("tcpdump -i github -n host Khanh1916", TITLE)], size)]
    y = 34
    rows = events or ["(no public packets captured)"]
    for event in rows:
        y += line_h
        text = event[6:] if compact and events else event
        clipped = text if len(text) <= chars else text[:chars - 1] + "…"
        parts.append(t(margin, y, clipped, size, SOFT))
    y += line_h + 8
    burst = "day burst" if compact else "day longest burst"
    parts.append(runs(margin, y, [(f"{total}", ACCENT), (" packets  ", SOFT), (f"{active}", ACCENT), (" active days  ", SOFT),
                                  (f"{best}", ACCENT), (f" {burst}  ", SOFT), (f"{peak}", ACCENT), (" peak", SOFT)], size - 1))
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
    frame = f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>'
    base = ".f,.bm{opacity:0}"
    anim = (
        f"@keyframes f{{0%{{opacity:.92}}7%,100%{{opacity:0}}}}.f{{animation:f {duration}s linear infinite}}"
        f"@keyframes bm{{0%{{transform:translateX(0);opacity:.9}}99%{{opacity:.9}}100%{{transform:translateX({w - 2 * margin}px);opacity:0}}}}"
        f".bm{{animation:bm {duration}s linear infinite}}"
    )
    label = f"tcpdump output of recent public GitHub events, then a contribution map: {total} contributions, {active} active days, longest run of {best} days, peak of {peak} in a day."
    return svg(w, h, frame + "".join(parts), base, anim, label, fixed=False)


# ---------------------------------------------------------------- sign-off

def signoff():
    w, h = 860, 92
    total = 14
    line = "tôi vẫn ở đây"
    size = 20
    x = (w - len(line) * size * CW) / 2
    parts = [
        t(w / 2, 32, "Connection to Khanh1916 closed.", 13, DIM, anchor="middle"),
        t(x, 68, line, size, ACCENT, True),
    ]
    cover, base, anim = typed("say", x, 68, len(line), size, PANEL, total, 2.2, 1.8, 13.4)
    parts.insert(0, f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>')
    parts.append(cover)
    cursor_x = x + len(line) * size * CW + 3
    parts.append(f'<rect class="bl" x="{cursor_x:.1f}" y="52" width="11" height="20" fill="{ACCENT}"/>')
    anim += "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"
    return svg(w, h, "".join(parts), base, anim, "Connection closed. Tôi vẫn ở đây: I am still here.", fixed=False)


# ---------------------------------------------------------------- buttons

# Brand glyphs from Simple Icons (CC0), on a 24x24 grid. MoMo is not in the set.
BRAND_ICONS = {
    "mail": "M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z",
    "facebook": "M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z",
    "sponsors": "M17.625 1.499c-2.32 0-4.354 1.203-5.625 3.03-1.271-1.827-3.305-3.03-5.625-3.03C3.129 1.499 0 4.253 0 8.249c0 4.275 3.068 7.847 5.828 10.227a33.14 33.14 0 0 0 5.616 3.876l.028.017.008.003-.001.003c.163.085.342.126.521.125.179.001.358-.041.521-.125l-.001-.003.008-.003.028-.017a33.14 33.14 0 0 0 5.616-3.876C20.932 16.096 24 12.524 24 8.249c0-3.996-3.129-6.75-6.375-6.75zm-.919 15.275a30.766 30.766 0 0 1-4.703 3.316l-.004-.002-.004.002a30.955 30.955 0 0 1-4.703-3.316c-2.677-2.307-5.047-5.298-5.047-8.523 0-2.754 2.121-4.5 4.125-4.5 2.06 0 3.914 1.479 4.544 3.684.143.495.596.797 1.086.796.49.001.943-.302 1.085-.796.63-2.205 2.484-3.684 4.544-3.684 2.004 0 4.125 1.746 4.125 4.5 0 3.225-2.37 6.216-5.048 8.523z",
    "kofi": "M11.351 2.715c-2.7 0-4.986.025-6.83.26C2.078 3.285 0 5.154 0 8.61c0 3.506.182 6.13 1.585 8.493 1.584 2.701 4.233 4.182 7.662 4.182h.83c4.209 0 6.494-2.234 7.637-4a9.5 9.5 0 0 0 1.091-2.338C21.792 14.688 24 12.22 24 9.208v-.415c0-3.247-2.13-5.507-5.792-5.87-1.558-.156-2.65-.208-6.857-.208m0 1.947c4.208 0 5.09.052 6.571.182 2.624.311 4.13 1.584 4.13 4v.39c0 2.156-1.792 3.844-3.87 3.844h-.935l-.156.649c-.208 1.013-.597 1.818-1.039 2.546-.909 1.428-2.545 3.064-5.922 3.064h-.805c-2.571 0-4.831-.883-6.078-3.195-1.09-2-1.298-4.155-1.298-7.506 0-2.181.857-3.402 3.012-3.714 1.533-.233 3.559-.26 6.39-.26m6.547 2.287c-.416 0-.65.234-.65.546v2.935c0 .311.234.545.65.545 1.324 0 2.051-.754 2.051-2s-.727-2.026-2.052-2.026m-10.39.182c-1.818 0-3.013 1.48-3.013 3.142 0 1.533.858 2.857 1.949 3.897.727.701 1.87 1.429 2.649 1.896a1.47 1.47 0 0 0 1.507 0c.78-.467 1.922-1.195 2.623-1.896 1.117-1.039 1.974-2.364 1.974-3.897 0-1.662-1.247-3.142-3.039-3.142-1.065 0-1.792.545-2.338 1.298-.493-.753-1.246-1.298-2.312-1.298",
}
BUTTONS = {
    "email": ("email", "25/tcp", "mail"),
    "facebook": ("facebook", "443/tcp", "facebook"),
    "sponsor": ("sponsor", "443/tcp", "sponsors"),
    "kofi": ("ko-fi", "443/tcp", "kofi"),
    "momo": ("momo", "443/tcp", None),
}


def button(label, port, icon):
    """One open port per link, in the same form as the nmap scan."""
    w, h, size, baseline, icon_x, icon_size = 222, 40, 13, 25, 122, 15
    parts = [
        f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="6" fill="{PANEL}" stroke="{LINE}" stroke-width="1.5"/>',
        t(14, baseline, port, size, SOFT),
        t(79, baseline, "open", size, GREEN),
        t(146, baseline, label, size, ACCENT),
    ]
    if icon:
        scale = icon_size / 24
        parts.append(f'<path transform="translate({icon_x} {(h - icon_size) / 2}) scale({scale})" d="{BRAND_ICONS[icon]}" fill="{ACCENT}"/>')
    else:
        parts.append(t(icon_x + icon_size / 2, baseline, "M", 15, ACCENT, True, "middle"))
    return svg(w, h, "".join(parts), "", "", f"{port} open: {label}")


# ---------------------------------------------------------------- main

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
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


def fetch_events(visible):
    """Latest public events on the profile's own visible repos, as tcpdump lines (UTC+7)."""
    request = urllib.request.Request(
        f"https://api.github.com/users/{USER}/events/public?per_page=100",
        headers={"Authorization": f"bearer {os.environ['GITHUB_TOKEN']}", "User-Agent": f"{USER}-profile"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    lines = []
    for event in payload:
        owner, _, name = event["repo"]["name"].partition("/")
        if owner != USER or name not in visible:
            continue
        when = datetime.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) + datetime.timedelta(hours=7)
        size = (event.get("payload") or {}).get("size")
        length = f", length {size}" if size else ""
        lines.append(f"{when:%m-%d %H:%M:%S} IP {USER.lower()} > {name}: {event['type']}{length}")
    return sorted(lines)[-5:]  # the API is not strictly ordered; tcpdump prints oldest first


def themed_link(href, stem, alt):
    """One link per theme. GitHub's CSS hides links ending in the other mode's fragment."""
    return "\n".join(
        f'  <a href="{href}#gh-{mode}-mode-only"><img src="{RAW}/{stem}{suffix}.svg" alt="{alt}"/></a>'
        for mode, suffix in (("light", "-light"), ("dark", ""))
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
            f"hero{suffix}.svg": hero(False),
            f"hero-compact{suffix}.svg": hero(True),
            f"session{suffix}.svg": session(False, latest),
            f"session-compact{suffix}.svg": session(True, latest),
            f"nuclei{suffix}.svg": nuclei_panel(False, matches),
            f"nuclei-compact{suffix}.svg": nuclei_panel(True, matches),
            f"activity{suffix}.svg": activity(calendar, events, False),
            f"activity-compact{suffix}.svg": activity(calendar, events, True),
            f"support{suffix}.svg": support_panel(False),
            f"support-compact{suffix}.svg": support_panel(True),
            f"signoff{suffix}.svg": signoff(),
            **{f"btn-{name}{suffix}.svg": button(*spec) for name, spec in BUTTONS.items()},
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
            files[f"{stem}{suffix}.svg"] = card(index, repo, entry["tag"], COLOURS.get(entry.get("color"), SOFT), *draw(repo))
            if not suffix:
                alt = html.escape(f"{repo['name']}: {' '.join(summarize(repo.get('description')))}", quote=True)
                links.append(themed_link(repo["url"], stem, alt))

    readme = README.read_text(encoding="utf-8")
    readme = replace_block(readme, "findings", '<p align="center">\n' + "\n".join(links) + "\n</p>")
    README.write_text(readme, encoding="utf-8", newline="\n")

    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("card-*.svg"):
        if stale.name not in files:
            stale.unlink()
    for name, content in files.items():
        (OUT / name).write_text(content, encoding="utf-8", newline="\n")
        print(f"{name:28} {len(content.encode()) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
