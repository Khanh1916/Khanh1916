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
        "BG": "#13120f", "PANEL": "#0a0a09", "BORDER": "#2c2820", "LINE": "#3a352a", "LED_OFF": "#1e1c17",
        "TEXT": "#eadcc0", "TITLE": "#f6ecd8", "GRID": "#d19200", "FLASH": "#ffe9b8", "SOFT": "#b3a78f",
        "DIM": "#8a7f6b", "LED_SOFT": "#b89a5e", "RED": "#ff5468", "BLUE": "#46adff", "GREEN": "#3ddc97",
        "AMBER": "#ffb000", "HEAT": ("#4d3a10", "#8a6410", "#d18f00", "#ffd166"),
    },
    "light": {
        "BG": "#ece3cf", "PANEL": "#f7f1e3", "BORDER": "#d8ccb2", "LINE": "#c9bb9c", "LED_OFF": "#e9dfc9",
        "TEXT": "#2c2518", "TITLE": "#15110a", "GRID": "#b37a00", "FLASH": "#5a3a00", "SOFT": "#5f5442",
        "DIM": "#75694f", "LED_SOFT": "#7a6238", "RED": "#c5301f", "BLUE": "#1c5fc4", "GREEN": "#127a4a",
        "AMBER": "#9a5b00", "HEAT": ("#efcf8d", "#dca23a", "#ad7100", "#5f3f00"),
    },
}


def apply_theme(name):
    """Point the module-level colours, and the tables built from them, at one theme."""
    palette = THEMES[name]
    globals().update(palette)
    globals().update(
        LED_BLACK=palette["PANEL"], LED_AMBER=palette["AMBER"], LED_TEXT=palette["TEXT"],
        COLOURS={"red": palette["RED"], "blue": palette["BLUE"], "green": palette["GREEN"], "amber": palette["AMBER"]},
        LEVELS=dict(zip(("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"),
                        (palette["LED_OFF"], *palette["HEAT"]))),
        HERO_COLUMNS=[
            ("RED / OFFENSE", palette["RED"], "I break systems", "to see how they fail."),
            ("BLUE / DEFENSE", palette["BLUE"], "I harden them", "and catch the drift."),
            ("GREEN / PRIVACY", palette["GREEN"], "I learn from data", "without reading it."),
        ],
        BUTTONS={
            "email": ("EMAIL", "southkhanh@gmail.com", "mail", palette["AMBER"]),
            "facebook": ("FACEBOOK", "n72khanh", "f", palette["BLUE"]),
            "sponsor": ("SPONSOR", "github sponsors", "heart", palette["RED"]),
            "kofi": ("KO-FI", "card or paypal", "cup", palette["AMBER"]),
            "momo": ("MOMO", "vietnam", "coin", palette["GREEN"]),
        },
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
            board += f'<path class="k{index}" d="{squares}" fill="{LED_AMBER}"/>'
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
        f'<path d="M{cx} {y - 22}l17 6v13c0 13-10 21-17 25c-7-4-17-12-17-25v-13z" fill="{LED_BLACK}" stroke="{LED_AMBER}" stroke-width="2.5"/>'
        f'<path d="M{cx - 8} {y}l6 6l11-12" fill="none" stroke="{LED_AMBER}" stroke-width="2.5" stroke-linecap="square"/>'
    )
    return body, ".pk,.ot{opacity:0}", anim


def hero(compact):
    total = 12
    if compact:
        w, h = 480, 452
        board, anim = led_board(33.5, 50, 7, 5.6, 59, 17, [("NGUYEN NAM", 0, 1), ("KHANH", 15, 9)], total)
        label_y, aka_y, wire_y = 34, 196, 238
    else:
        w, h = 860, 322
        board, anim = led_board(31, 52, 8.4, 6.6, 95, 9, [("NGUYEN NAM KHANH", 0, 1)], total)
        label_y, aka_y, wire_y = 36, 156, 192
    cx = w / 2
    parts = [f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{LED_BLACK}" stroke="{BORDER}" stroke-width="1.5"/>', board]
    parts.append(t(cx, label_y, "CYBERSECURITY RESEARCHER", 13 if compact else 15, LED_AMBER, anchor="middle", track=0.12))
    parts.append(t(cx, aka_y, "a.k.a. Cao Thanh Lam  /  @Khanh1916", 14 if compact else 16, LED_SOFT, anchor="middle"))
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
        parts.append(f'<rect class="u" x="110" y="310" width="80" height="2" fill="{LED_AMBER}"/>')
        base += ".u{opacity:0}"
        anim += (
            "@keyframes u{0%,33.3%{transform:translateX(0);opacity:1}33.4%,66.6%{transform:translateX(280px);opacity:1}"
            "66.7%,100%{transform:translateX(560px);opacity:1}}.u{animation:u 9s linear infinite}"
        )
    label = "Nguyen Nam Khanh, a.k.a. Cao Thanh Lam. Cybersecurity researcher: I break systems to see how they fail, I harden them and catch the drift, and I learn from data without reading it."
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


def terminal(compact, latest):
    w = 480 if compact else 860
    size, line_h = (13, 20) if compact else (16, 24)
    x = 20
    open_count = sum(1 for s in SERVICES if s[1] == "open")
    prompt = "visitor@github:~$ "
    command = "nmap -sV github.com/Khanh1916"
    output = [
        [("Starting Nmap against Nguyen Nam Khanh", SOFT)],
        [(f"Host is up. Latest push: {latest}", SOFT)],
        [],
    ]
    if compact:
        output.append([(f"{'PORT':<11}{'SERVICE':<12}STACK", DIM)])
    else:
        output.append([(f"{'PORT':<11}{'STATE':<10}{'SERVICE':<13}VERSION", DIM)])
    for port, state, service, long_text, short_text in SERVICES:
        hidden = state != "open"
        colour = AMBER if hidden else GREEN
        if compact:
            output.append([(f"{port:<11}", LED_SOFT), (f"{service:<12}", colour), (short_text, AMBER if hidden else TEXT)])
        else:
            output.append([(f"{port:<11}", LED_SOFT), (f"{state:<10}", colour), (f"{service:<13}", TEXT), (long_text, AMBER if hidden else SOFT)])
    output += [
        [],
        [("Service Info: OS: Linux, Windows, Cisco IOS, Junos", SOFT)],
        [(f"Nmap done: 1 human scanned, {open_count} services open", SOFT)],
    ]

    top = 44
    first_y = top + 28
    h = first_y + (len(output) + 1) * line_h + 18
    total = 24
    parts = [
        f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>',
        f'<path d="M1 {top}H{w - 1}" stroke="{BORDER}" stroke-width="1.5"/>',
    ]
    for i, colour in enumerate((RED, AMBER, GREEN)):
        parts.append(f'<circle cx="{22 + i * 20}" cy="22" r="6" fill="{colour}"/>')
    parts.append(t(w / 2, 27, "recon: Khanh1916", 13, DIM, anchor="middle"))

    prompt_x = x + len(prompt) * size * CW
    parts.append(runs(x, first_y, [(prompt, AMBER)], size))
    parts.append(t(prompt_x, first_y, command, size, TITLE))
    cover, base, anim = typed("cmd", prompt_x, first_y, len(command), size, PANEL, total, 0.5, 1.7, 23)
    parts.append(cover)

    step = len(command) * size * CW
    cursor_w = size * CW
    parts.append(f'<rect class="cu" x="{prompt_x:.1f}" y="{first_y - size + 2}" width="{cursor_w:.1f}" height="{size + 2}" fill="{TEXT}"/>')
    base += ".cu{opacity:0}"
    a, b = 0.5 / total * 100, 2.2 / total * 100
    anim += (
        f"@keyframes cu{{0%,{a:.3f}%{{opacity:1;transform:translateX(0);animation-timing-function:steps({len(command)},end)}}"
        f"{b:.3f}%{{opacity:1;transform:translateX({step:.1f}px)}}{b + 0.02:.3f}%,100%{{opacity:0;transform:translateX({step:.1f}px)}}}}"
        f".cu{{animation:cu {total}s linear infinite}}"
    )

    y, moment = first_y, 2.7
    for i, segments in enumerate(output):
        y += line_h
        if segments:
            parts.append(runs(x, y, segments, size, f"l{i}"))
            anim += shown(f"l{i}", moment, 23, total)
            moment += 0.2
    y += line_h
    prompt_group = t(x, y, prompt, size, AMBER) + (
        f'<rect class="bl" x="{prompt_x:.1f}" y="{y - size + 2}" width="{cursor_w:.1f}" height="{size + 2}" fill="{TEXT}"/>'
    )
    parts.append(f'<g class="end">{prompt_group}</g>')
    anim += shown("end", moment, 23, total)
    anim += "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"

    open_list = ", ".join(s[2] for s in SERVICES if s[1] == "open")
    label = f"An animated nmap scan of this profile. Open services map to skills: {open_list}. Port 31337 is filtered and marked flag."
    return svg(w, h, "".join(parts), base, anim, label, fixed=False)


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
        parts.append(t(20 + len(name) * 23 * CW + 12, 66, f"★ {stars}", 14, AMBER))
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
    body = t(20, 122, "+ ε noise", 11, GREEN) + t(110, 122, "masked", 10.5, AMBER, anchor="middle")
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
            body += f'<rect class="mv mv{gateway}" x="70" y="{y + 3}" width="9" height="4" rx="1" fill="{AMBER}" style="animation-delay:{-(gateway * 0.4 + offset):.2f}s"/>'

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

    body += f'<path class="fl" d="M297 {mid}h29" stroke="{AMBER}" stroke-width="1.5" stroke-dasharray="3 4"/>'
    body += f'<rect x="328" y="143" width="52" height="32" rx="6" fill="{BG}" stroke="{BORDER}"/>'
    body += f'<g class="ids"><rect x="328" y="143" width="52" height="32" rx="6" fill="{AMBER}" fill-opacity=".22" stroke="{AMBER}"/></g>'
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
    body += t(380, 178, "23:09 in Hanoi", 12, AMBER, anchor="end")
    body += t(380, 195, "00:09 in Beijing", 12, AMBER, anchor="end")
    return body, base, anim


ANIMATIONS = {"oracle": anim_oracle, "secbaseline": anim_secbaseline, "federated": anim_federated, "lunar": anim_lunar}


# ---------------------------------------------------------------- activity



def activity(calendar):
    weeks = calendar["weeks"]
    w, h = 860, 216
    cell, gap, x0, y0 = 12, 3, 32, 84
    days = [day for week in weeks for day in week["contributionDays"]]
    total = calendar["totalContributions"]
    active = sum(1 for d in days if d["contributionCount"])
    peak = max((d["contributionCount"] for d in days), default=0)
    best = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        best = max(best, run)
    duration = 9
    grid_w = len(weeks) * (cell + gap) - gap
    parts = [
        f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>',
        runs(32, 36, [("$ ", AMBER), ("tcpdump -i github -n host Khanh1916", TITLE)], 15),
        runs(32, 63, [(f"{total}", AMBER), (" packets   ", SOFT), (f"{active}", AMBER), (" active days   ", SOFT),
                      (f"{best}", AMBER), (" day longest burst   ", SOFT), (f"{peak}", AMBER), (" peak/day", SOFT)], 14),
    ]
    for column, week in enumerate(weeks):
        delay = column / len(weeks) * duration
        for day in week["contributionDays"]:
            x = x0 + column * (cell + gap)
            y = y0 + day["weekday"] * (cell + gap)
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{LEVELS[day["contributionLevel"]]}"/>')
            if day["contributionCount"]:
                parts.append(f'<rect class="f" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{FLASH}" style="animation-delay:{delay:.2f}s"/>')
    beam_h = 7 * (cell + gap) + 6
    parts.append(f'<rect class="bm" x="{x0 - 3}" y="{y0 - 6}" width="3" height="{beam_h}" fill="{FLASH}"/>')
    base = ".f,.bm{opacity:0}"
    anim = (
        f"@keyframes f{{0%{{opacity:.92}}7%,100%{{opacity:0}}}}.f{{animation:f {duration}s linear infinite}}"
        f"@keyframes bm{{0%{{transform:translateX(0);opacity:.9}}99%{{opacity:.9}}100%{{transform:translateX({grid_w}px);opacity:0}}}}"
        f".bm{{animation:bm {duration}s linear infinite}}"
    )
    label = f"Contribution calendar drawn as a packet capture: {total} contributions, {active} active days, longest run of {best} days, peak of {peak} in a day."
    return svg(w, h, "".join(parts), base, anim, label, fixed=False)


# ---------------------------------------------------------------- sign-off

def signoff():
    w, h = 860, 92
    total = 14
    line = "tôi vẫn ở đây"
    size = 20
    x = (w - len(line) * size * CW) / 2
    parts = [
        t(w / 2, 32, "Connection to Khanh1916 closed.", 13, DIM, anchor="middle"),
        t(x, 68, line, size, AMBER, True),
    ]
    cover, base, anim = typed("say", x, 68, len(line), size, PANEL, total, 2.2, 1.8, 13.4)
    parts.insert(0, f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{PANEL}" stroke="{BORDER}" stroke-width="1.5"/>')
    parts.append(cover)
    cursor_x = x + len(line) * size * CW + 3
    parts.append(f'<rect class="bl" x="{cursor_x:.1f}" y="52" width="11" height="20" fill="{AMBER}"/>')
    anim += "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"
    return svg(w, h, "".join(parts), base, anim, "Connection closed. Tôi vẫn ở đây: I am still here.", fixed=False)


# ---------------------------------------------------------------- stack rack and buttons

STACK = [
    ("LANGUAGES", [("GO", "Go"), ("PY", "Python"), ("SH", "Bash"), ("PS", "PowerShell"), ("PHP", "PHP"), ("SQL", "SQL")]),
    ("INFRA", [("LNX", "Linux"), ("DKR", "Docker"), ("K8S", "Kubernetes"), ("GHA", "GitHub Actions")]),
    ("DATA", [("MY", "MySQL"), ("ORA", "Oracle")]),
    ("MACHINE LEARNING", [("TCH", "PyTorch"), ("FLW", "Flower"), ("OPA", "Opacus"), ("SKL", "scikit-learn"), ("PD", "pandas"), ("NP", "NumPy")]),
]
ICONS = {
    "heart": "0110110 1111111 1111111 1111111 0111110 0011100 0001000",
    "mail": "1111111 1100011 1010101 1001001 1000001 1000001 1111111",
    "cup": "0101000 0010100 0000000 1111110 1111101 1111110 0111100",
    "f": "0001110 0010000 0010000 0111110 0010000 0010000 0010000",
    "coin": "0011100 0100010 1001001 1010101 1001001 0100010 0011100",
}


def dot_path(x, y, pitch, dot, rows):
    return "".join(
        f"M{x + c * pitch:.1f} {y + r * pitch:.1f}h{dot}v{dot}h-{dot}z"
        for r, row in enumerate(rows) for c, value in enumerate(row) if value == "1"
    )


def dot_word(x, y, pitch, dot, word):
    return "".join(dot_path(x + i * 6 * pitch, y, pitch, dot, FONT5X7[ch].split()) for i, ch in enumerate(word))


def stack_panel(compact):
    """The stack as a rack of modules; each lights up, then its link LED blinks."""
    w, columns, margin = (480, 3, 16) if compact else (860, 6, 20)
    gap, module_h, total = 10, 72, 10
    module_w = int((w - 2 * margin - (columns - 1) * gap) / columns)
    cells, captions, column, row = [], {}, 0, 0
    for group, items in STACK:
        for i, item in enumerate(items):
            if i == 0:
                captions.setdefault(row, []).append((column, group))
            cells.append((column, row, item))
            column += 1
            if column == columns:
                column, row = 0, row + 1
    rows = max(c[1] for c in cells) + 1
    tops, y, parts = {}, 16, []
    for r in range(rows):
        for col, caption in captions.get(r, []):
            parts.append(t(margin + col * (module_w + gap), y + 11, caption, 12, LED_SOFT, track=0.1))
        y += 20 if r in captions else 0
        tops[r] = y
        y += module_h + gap
    h = y - gap + 16
    parts.insert(0, f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{LED_BLACK}" stroke="{BORDER}" stroke-width="1.5"/>')
    rng = random.Random(8080)
    anim = "@keyframes bl{50%{opacity:.12}}"
    for index, (col, r, (code, name)) in enumerate(cells):
        x, top = margin + col * (module_w + gap), tops[r]
        parts.append(f'<rect x="{x}" y="{top}" width="{module_w}" height="{module_h}" rx="5" fill="{BG}" stroke="{BORDER}"/>')
        cols = len(code) * 6 - 1
        path = dot_word(x + (module_w - cols * 4) / 2, top + 12, 4, 3.2, code)
        parts.append(f'<path class="m{index}" d="{path}" fill="{LED_AMBER}"/>')
        anim += shown(f"m{index}", 0.2 + index * 0.13, total - 0.6, total)
        parts.append(t(x + module_w / 2, top + 60, name, 12, LED_TEXT, anchor="middle"))
        colour = rng.choice((GREEN, GREEN, LED_AMBER))
        parts.append(f'<rect class="l{index}" x="{x + module_w - 13}" y="{top + 7}" width="6" height="6" fill="{colour}"/>')
        anim += f".l{index}{{animation:bl {rng.uniform(0.5, 2.2):.2f}s steps(1,end) infinite}}"
    names = ", ".join(name for _, items in STACK for _, name in items)
    return svg(w, h, "".join(parts), "", anim, f"Full stack: {names}.", fixed=False)


def button(index, word, note, icon, colour):
    """A LED push-button; the word lights letter by letter and the icon pulses."""
    w, h, total, x0 = 240, 64, 7, 58
    parts = [
        f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="8" fill="{LED_BLACK}" stroke="{BORDER}" stroke-width="1.5"/>',
        f'<path class="ic" d="{dot_path(16, 18, 4, 3.2, ICONS[icon].split())}" fill="{colour}"/>',
        t(x0, 52, note, 11.5, LED_SOFT),
        f'<rect class="bl" x="{w - 20}" y="14" width="6" height="6" fill="{colour}"/>',
    ]
    anim = (
        "@keyframes ic{0%,100%{opacity:1}50%{opacity:.4}}.ic{animation:ic 1.6s ease-in-out infinite}"
        f"@keyframes bl{{50%{{opacity:.12}}}}.bl{{animation:bl {1 + index * 0.37:.2f}s steps(1,end) infinite}}"
    )
    for position, letter in enumerate(word):
        path = dot_path(x0 + position * 6 * 2.7, 13, 2.7, 2.2, FONT5X7[letter].split())
        parts.append(f'<path class="k{position}" d="{path}" fill="{LED_AMBER}"/>')
        anim += shown(f"k{position}", 0.25 + position * 0.09, total - 0.5, total)
    return svg(w, h, "".join(parts), "", anim, f"{word}: {note}")


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
    files, links = {}, []
    for theme, suffix in (("dark", ""), ("light", "-light")):
        apply_theme(theme)
        files.update({
            f"hero{suffix}.svg": hero(False),
            f"hero-compact{suffix}.svg": hero(True),
            f"nmap{suffix}.svg": terminal(False, latest),
            f"nmap-compact{suffix}.svg": terminal(True, latest),
            f"activity{suffix}.svg": activity(calendar),
            f"signoff{suffix}.svg": signoff(),
            f"stack{suffix}.svg": stack_panel(False),
            f"stack-compact{suffix}.svg": stack_panel(True),
            **{f"btn-{name}{suffix}.svg": button(i, *spec) for i, (name, spec) in enumerate(BUTTONS.items())},
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
