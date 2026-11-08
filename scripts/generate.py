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

BG = "#0b0f14"
PANEL = "#0d1117"
BORDER = "#2a323d"
TEXT = "#d7dee8"
SOFT = "#9aa6b4"
DIM = "#667384"
RED = "#ff5468"
BLUE = "#46adff"
GREEN = "#3ddc97"
AMBER = "#ffb454"


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

def shield(cx, cy):
    return (
        f'<g class="sh"><path d="M{cx} {cy - 22}l17 6v13c0 13-10 21-17 25c-7-4-17-12-17-25v-13z" '
        f'fill="{PANEL}" stroke="{BLUE}" stroke-width="2.5"/>'
        f'<path d="M{cx - 8} {cy}l6 6l11-12" fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></g>'
    )


def hero(compact):
    w, h = (480, 410) if compact else (860, 322)
    cx = w // 2
    size = 38 if compact else 62
    name = "NGUYEN NAM KHANH"
    name_y, wire_y = (106, 176) if compact else (112, 188)
    defs = (
        f'<defs><radialGradient id="r"><stop offset="0" stop-color="{RED}" stop-opacity=".2"/><stop offset="1" stop-color="{RED}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="b"><stop offset="0" stop-color="{BLUE}" stop-opacity=".2"/><stop offset="1" stop-color="{BLUE}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="g"><stop offset="0" stop-color="{GREEN}" stop-opacity=".18"/><stop offset="1" stop-color="{GREEN}" stop-opacity="0"/></radialGradient>'
        f'<pattern id="p" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0V28" fill="none" stroke="#fff" stroke-opacity=".035"/></pattern>'
        f'<clipPath id="f"><rect width="{w}" height="{h}" rx="16"/></clipPath></defs>'
    )
    parts = [defs, f'<g clip-path="url(#f)"><rect width="{w}" height="{h}" fill="{BG}"/>']
    glow = w * 0.36
    for gid, gx in (("r", w * 0.14), ("b", w * 0.5), ("g", w * 0.86)):
        parts.append(f'<circle cx="{gx:.0f}" cy="{h * 0.55:.0f}" r="{glow:.0f}" fill="url(#{gid})"/>')
    parts.append(f'<rect width="{w}" height="{h}" fill="url(#p)"/></g>')
    parts.append(f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="15.5" fill="none" stroke="{BORDER}" stroke-width="1.5"/>')
    parts.append(t(cx, name_y - size - 8, "// CYBERSECURITY RESEARCHER", 13 if compact else 15, SOFT, anchor="middle", track=0.12))
    track = 0.02 if compact else 0.05
    parts.append(t(cx - 2, name_y, name, size, RED, True, "middle", track, "gr"))
    parts.append(t(cx + 2, name_y, name, size, BLUE, True, "middle", track, "gb"))
    parts.append(t(cx, name_y, name, size, "#ffffff", True, "middle", track))
    parts.append(t(cx, name_y + 30, "a.k.a. Cao Thanh Lam  /  @Khanh1916", 14 if compact else 16, SOFT, anchor="middle"))

    left, right = 32, w - 32
    parts.append(f'<line x1="{left}" y1="{wire_y}" x2="{right}" y2="{wire_y}" stroke="#303a47" stroke-width="2" stroke-dasharray="2 6" stroke-linecap="round"/>')
    run_in = cx - 18 - left - 12
    run_out = right - cx - 24
    for i in range(5):
        parts.append(f'<rect class="pk" x="{left}" y="{wire_y - 3}" width="12" height="6" rx="1.5" fill="{RED}" style="animation-delay:{-i * 0.6:.1f}s"/>')
    for i in range(5):
        parts.append(f'<circle class="dp d{i % 3}" cx="{cx + 24}" cy="{wire_y}" r="3.2" fill="{GREEN}" style="animation-delay:{-i * 0.6:.1f}s"/>')
    parts.append(shield(cx, wire_y))

    columns = [
        ("RED / OFFENSE", RED, "I break systems", "to see how they fail."),
        ("BLUE / DEFENSE", BLUE, "I harden them", "and catch the drift."),
        ("GREEN / PRIVACY", GREEN, "I learn from data", "without reading it."),
    ]
    for i, (label, colour, first, second) in enumerate(columns):
        if compact:
            y = 226 + i * 60
            items = t(32, y, label, 13, colour, True, track=0.06) + t(32, y + 26, f"{first} {second}", 15, TEXT)
        else:
            x = (150, 430, 710)[i]
            items = (t(x, 240, label, 14, colour, True, "middle", 0.08)
                     + t(x, 271, first, 19, TEXT, anchor="middle")
                     + t(x, 297, second, 19, TEXT, anchor="middle"))
        parts.append(f'<g class="c{i}">{items}</g>')

    base = ".pk,.dp{opacity:0}"
    anim = (
        f"@keyframes pk{{0%{{transform:translateX(0);opacity:0}}6%,92%{{opacity:1}}100%{{transform:translateX({run_in}px);opacity:0}}}}"
        ".pk{animation:pk 3s linear infinite}"
        f"@keyframes d0{{0%{{transform:translate(0,0);opacity:0}}8%{{opacity:1}}30%{{transform:translate({run_out * .3:.0f}px,-9px)}}60%{{transform:translate({run_out * .6:.0f}px,8px)}}92%{{opacity:1}}100%{{transform:translate({run_out}px,-4px);opacity:0}}}}"
        f"@keyframes d1{{0%{{transform:translate(0,0);opacity:0}}8%{{opacity:1}}25%{{transform:translate({run_out * .25:.0f}px,10px)}}70%{{transform:translate({run_out * .7:.0f}px,-10px)}}92%{{opacity:1}}100%{{transform:translate({run_out}px,6px);opacity:0}}}}"
        f"@keyframes d2{{0%{{transform:translate(0,0);opacity:0}}8%{{opacity:1}}45%{{transform:translate({run_out * .45:.0f}px,-7px)}}75%{{transform:translate({run_out * .75:.0f}px,9px)}}92%{{opacity:1}}100%{{transform:translate({run_out}px,0);opacity:0}}}}"
        ".d0{animation:d0 3s linear infinite}.d1{animation:d1 3s linear infinite}.d2{animation:d2 3s linear infinite}"
        "@keyframes sh{0%,100%{opacity:1}50%{opacity:.62}}.sh{animation:sh .6s ease-in-out infinite}"
        "@keyframes gr{0%,91%,100%{transform:translate(0,0)}92%{transform:translate(-9px,2px)}94%{transform:translate(6px,-2px)}96%{transform:translate(-4px,0)}}.gr{animation:gr 6s steps(1,end) infinite}"
        "@keyframes gb{0%,91%,100%{transform:translate(0,0)}92%{transform:translate(9px,-2px)}94%{transform:translate(-6px,2px)}96%{transform:translate(4px,0)}}.gb{animation:gb 6s steps(1,end) infinite}"
        "@keyframes c{0%,28%,100%{opacity:1}36%,92%{opacity:.5}}"
        ".c0,.c1,.c2{animation:c 9s ease-in-out infinite}.c1{animation-delay:3s}.c2{animation-delay:6s}"
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
    ("5432/tcp", "open", "postgresql", "PostgreSQL / SQL", "PostgreSQL / SQL"),
    ("6006/tcp", "open", "ml-stack", "PyTorch / TensorFlow / scikit-learn / pandas", "PyTorch / TensorFlow / sklearn"),
    ("6443/tcp", "open", "kubernetes", "Kubernetes / multi-tenant labs", "Kubernetes"),
    ("8080/tcp", "open", "http-go", "Go / REST APIs / auth / rate limiting", "Go / REST / auth"),
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
            output.append([(f"{port:<11}", BLUE), (f"{service:<12}", colour), (short_text, AMBER if hidden else TEXT)])
        else:
            output.append([(f"{port:<11}", BLUE), (f"{state:<10}", colour), (f"{service:<13}", TEXT), (long_text, AMBER if hidden else SOFT)])
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
    parts.append(runs(x, first_y, [(prompt, GREEN)], size))
    parts.append(t(prompt_x, first_y, command, size, "#ffffff"))
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
    prompt_group = t(x, y, prompt, size, GREEN) + (
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
COLOURS = {"red": RED, "blue": BLUE, "green": GREEN, "amber": AMBER}


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
        t(20, 66, name, 23, "#ffffff", True),
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
        body += f'<rect x="{cx}" y="172" width="{width}" height="28" rx="5" fill="{BG}" stroke="#3a4552"/>'
        body += t(cx + width / 2, 190, text, 12, DIM, anchor="middle")
        lit = t(cx + width / 2, 190, text, 12, "#ffffff", True, "middle")
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
        body += f'<rect class="q" x="{cx}" y="{cy}" width="{cell}" height="{cell}" rx="2" fill="{BLUE}" style="animation-delay:{delay:.2f}s"/>'
        if i in drifted:
            body += f'<rect class="dr" x="{cx}" y="{cy}" width="{cell}" height="{cell}" rx="2" fill="{RED}"/>'
    anim += "@keyframes qq{0%{fill:#232b36}4%,100%{fill:" + BLUE + "}}.q{animation:qq 10s linear infinite}"
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
        body += f'<line x1="70" y1="{y + 5}" x2="{box_x}" y2="{mid}" stroke="#2a3a35" stroke-width="1.2"/>'
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
    body += t(box_x + box_w / 2, 164, "SecAgg+", 13, "#ffffff", True, "middle")
    body += f'<path class="fl" d="M{box_x + box_w + 2} {mid}h22" stroke="{GREEN}" stroke-width="1.5" stroke-dasharray="3 4"/>'

    for row in range(5):
        y = 139 + row * 9
        cells = "".join(
            f'<rect x="{240 + column * 14}" y="{y}" width="12" height="6" rx="1" fill="{GREEN}" fill-opacity="{rng.uniform(0.35, 1):.2f}"/>'
            for column in range(4)
        )
        body += f'<g class="sy{row}">{cells}</g>'
        anim += shown(f"sy{row}", 0.6 + row * 0.5, 5.6, total)

    body += f'<path class="fl" d="M297 {mid}h29" stroke="{BLUE}" stroke-width="1.5" stroke-dasharray="3 4"/>'
    body += f'<rect x="328" y="143" width="52" height="32" rx="6" fill="{BG}" stroke="{BORDER}"/>'
    body += f'<g class="ids"><rect x="328" y="143" width="52" height="32" rx="6" fill="{BLUE}" fill-opacity=".22" stroke="{BLUE}"/></g>'
    body += t(354, 164, "IDS", 13, "#ffffff", True, "middle")
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
    body += t(380, 157, "Tết 2030", 15, "#ffffff", True, "end")
    body += t(380, 178, "23:09 in Hanoi", 12, AMBER, anchor="end")
    body += t(380, 195, "00:09 in Beijing", 12, AMBER, anchor="end")
    return body, base, anim


ANIMATIONS = {"oracle": anim_oracle, "secbaseline": anim_secbaseline, "federated": anim_federated, "lunar": anim_lunar}


# ---------------------------------------------------------------- activity

LEVELS = {
    "NONE": "#161c24",
    "FIRST_QUARTILE": "#17406a",
    "SECOND_QUARTILE": "#1f71b8",
    "THIRD_QUARTILE": "#46adff",
    "FOURTH_QUARTILE": "#c0e4ff",
}


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
        runs(32, 36, [("$ ", GREEN), ("tcpdump -i github -n host Khanh1916", "#ffffff")], 15),
        runs(32, 63, [(f"{total}", BLUE), (" packets   ", SOFT), (f"{active}", BLUE), (" active days   ", SOFT),
                      (f"{best}", BLUE), (" day longest burst   ", SOFT), (f"{peak}", BLUE), (" peak/day", SOFT)], 14),
    ]
    for column, week in enumerate(weeks):
        delay = column / len(weeks) * duration
        for day in week["contributionDays"]:
            x = x0 + column * (cell + gap)
            y = y0 + day["weekday"] * (cell + gap)
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{LEVELS[day["contributionLevel"]]}"/>')
            if day["contributionCount"]:
                parts.append(f'<rect class="f" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="#fff" style="animation-delay:{delay:.2f}s"/>')
    beam_h = 7 * (cell + gap) + 6
    parts.append(f'<rect class="bm" x="{x0 - 3}" y="{y0 - 6}" width="3" height="{beam_h}" fill="{GREEN}"/>')
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
        t(x, 68, line, size, GREEN, True),
    ]
    cover, base, anim = typed("say", x, 68, len(line), size, BG, total, 2.2, 1.8, 13.4)
    parts.insert(0, f'<rect width="{w}" height="{h}" rx="12" fill="{BG}"/>')
    parts.append(cover)
    cursor_x = x + len(line) * size * CW + 3
    parts.append(f'<rect class="bl" x="{cursor_x:.1f}" y="52" width="11" height="20" fill="{GREEN}"/>')
    anim += "@keyframes bl{50%{opacity:0}}.bl{animation:bl 1s steps(1,end) infinite}"
    return svg(w, h, "".join(parts), base, anim, "Connection closed. Tôi vẫn ở đây: I am still here.", fixed=False)


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


def md_escape(text):
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", text)


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

    files = {
        "hero.svg": hero(False),
        "hero-compact.svg": hero(True),
        "nmap.svg": terminal(False, latest),
        "nmap-compact.svg": terminal(True, latest),
        "activity.svg": activity(user["contributionsCollection"]["contributionCalendar"]),
        "signoff.svg": signoff(),
    }

    links = []
    featured = set()
    for entry in config["featured"]:
        repo = by_name.get(entry["repo"])
        if repo is None:
            print(f"warning: featured repo {entry['repo']} is not public or is hidden", file=sys.stderr)
            continue
        featured.add(repo["name"])
        draw = ANIMATIONS.get(entry.get("animation"), anim_languages)
        filename = f"card-{repo['name'].lower()}.svg"
        files[filename] = card(len(links) + 1, repo, entry["tag"], COLOURS.get(entry.get("color"), SOFT), *draw(repo))
        alt = html.escape(f"{repo['name']}: {' '.join(summarize(repo.get('description')))}", quote=True)
        links.append(f'  <a href="{repo["url"]}"><img src="{RAW}/{filename}" alt="{alt}"/></a>')

    log_lines = ["Also in the logs:", ""]
    for repo in [r for r in visible if r["name"] not in featured][:8]:
        about = " ".join(summarize(repo.get("description"), width=110, max_lines=1))
        log_lines.append(f"- [{md_escape(repo['name'])}]({repo['url']})" + (f": {md_escape(about)}" if about else ""))

    readme = README.read_text(encoding="utf-8")
    readme = replace_block(readme, "findings", '<p align="center">\n' + "\n".join(links) + "\n</p>")
    readme = replace_block(readme, "logs", "\n".join(log_lines))
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
