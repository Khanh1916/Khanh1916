# SPDX-License-Identifier: MIT
"""Offline tests for scripts/generate.py. Standard library only, no token, no network.

Run from the repo root:  python -B -m unittest discover -s tests -v
"""
import datetime
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import generate as g  # noqa: E402

FEATURED = ["oracle-sec-lab", "secbaseline", "PP-FedData"]
BUTTON_COUNT = len(g.BUTTONS)
PER_THEME = 10 + BUTTON_COUNT + len(FEATURED)  # hero, nuclei, activity, support, signoff x2 sizes


def fake_repo(name, pushed):
    return {
        "name": name, "url": f"https://github.com/Khanh1916/{name}", "stargazerCount": 3,
        "description": "A sentence about the project. Another sentence that is dropped.",
        "isFork": False, "isArchived": False, "pushedAt": pushed, "latestRelease": {"tagName": "v1.0"},
        "languages": {"edges": [{"size": 70, "node": {"name": "Python", "color": "#3572A5"}},
                                {"size": 30, "node": {"name": "Shell", "color": None}}]},
    }


def fake_user():
    levels = ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    weeks = [{"contributionDays": [{"contributionCount": (w + d) % 4, "weekday": d, "contributionLevel": levels[(w + d) % 5]}
                                   for d in range(7)]} for w in range(53)]
    repos = [fake_repo(n, f"2026-11-1{i}T00:00:00Z") for i, n in enumerate(FEATURED)]
    repos.append(fake_repo("greenlight", "2026-11-18T00:00:00Z"))  # hidden by profile.json
    return {"contributionsCollection": {"contributionCalendar": {"totalContributions": 99, "weeks": weeks}},
            "repositories": {"nodes": repos}}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PipelineTest(unittest.TestCase):
    """Run main() end to end in a temp copy of the repo, with the GitHub calls faked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "profile.json", cls.tmp / "profile.json")
        shutil.copy(ROOT / "README.md", cls.tmp / "README.md")
        cls.original_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        saved = (g.OUT, g.CONFIG, g.README, g.fetch, g.fetch_events)
        g.OUT, g.CONFIG, g.README = cls.tmp / "assets", cls.tmp / "profile.json", cls.tmp / "README.md"
        g.fetch, g.fetch_events = fake_user, lambda visible: ["11-18 10:00:00 IP khanh1916 > secbaseline: PushEvent, length 1"]
        try:
            g.main()
        finally:
            g.OUT, g.CONFIG, g.README, g.fetch, g.fetch_events = saved
        cls.files = sorted((cls.tmp / "assets").glob("*.svg"))
        cls.readme = (cls.tmp / "README.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_file_count(self):
        self.assertEqual(len(self.files), 2 * PER_THEME)

    def test_every_svg_is_valid_xml_with_a_label(self):
        for path in self.files:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            self.assertTrue(root.get("aria-label"), f"{path.name} has no aria-label")
            self.assertEqual(root.get("role"), "img", path.name)

    def test_motion_is_behind_reduced_motion_query(self):
        for path in self.files:
            css = path.read_text(encoding="utf-8")
            if "@keyframes" in css:
                self.assertIn("prefers-reduced-motion:no-preference", css, path.name)

    def test_light_files_do_not_use_dark_only_colours(self):
        light = {v.lower() for v in g.THEMES["light"].values() if isinstance(v, str)}
        dark_only = {v.lower() for v in g.THEMES["dark"].values() if isinstance(v, str)} - light
        for path in self.files:
            if path.stem.endswith("-light"):
                text = path.read_text(encoding="utf-8").lower()
                leaked = sorted(c for c in dark_only if c in text)
                self.assertEqual(leaked, [], f"{path.name} uses dark colours {leaked}")

    def test_compact_panels_are_400_wide_and_nothing_overflows(self):
        ns = "{http://www.w3.org/2000/svg}"
        compact = [p for p in self.files if "-compact" in p.name]
        self.assertEqual(len(compact), 2 * 5)
        for path in compact:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            width = float(root.get("viewBox").split()[2])
            self.assertEqual(width, g.COMPACT_W, path.name)
            for node in root.iter(ns + "text"):
                left = float(node.get("x"))
                right = left + float(node.get("textLength"))
                self.assertGreaterEqual(left, 8, f"{path.name}: {node.text!r} starts too far left")
                self.assertLessEqual(right, width - 8, f"{path.name}: {node.text!r} runs past the right edge ({right:.0f} > {width - 8:.0f})")

    def test_hidden_repo_gets_no_card(self):
        self.assertFalse((self.tmp / "assets" / "card-greenlight.svg").exists())
        for name in FEATURED:
            self.assertTrue((self.tmp / "assets" / f"card-{name.lower()}.svg").exists(), name)

    def test_readme_changes_only_between_markers(self):
        def outside(text):
            return re.sub(r"<!-- findings:start -->.*<!-- findings:end -->", "", text, flags=re.S)
        self.assertEqual(outside(self.readme), outside(self.original_readme))
        self.assertEqual(self.readme.splitlines()[0], self.original_readme.splitlines()[0])  # CTF comment untouched

    def test_cards_use_picture_markup_not_fragments(self):
        block = re.search(r"<!-- findings:start -->(.*)<!-- findings:end -->", self.readme, re.S).group(1)
        self.assertEqual(block.count("<picture>"), len(FEATURED))
        self.assertNotIn("gh-dark-mode-only", block)

    def test_hero_alt_matches_hero_label(self):
        label = ET.fromstring((self.tmp / "assets" / "hero.svg").read_text(encoding="utf-8")).get("aria-label")
        alt = re.search(r'hero-light\.svg" width="100%" alt="([^"]*)"', self.original_readme).group(1)
        self.assertEqual(alt, label)


class ReadmeTest(unittest.TestCase):
    """Checks on the hand-written README itself."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_every_asset_reference_exists(self):
        for name in set(re.findall(r"/main/assets/([\w.-]+\.svg)", self.readme)):
            self.assertTrue((ROOT / "assets" / name).exists(), f"README references missing asset {name}")

    def test_every_picture_has_a_light_fallback_image(self):
        for block in re.findall(r"<picture>.*?</picture>", self.readme, re.S):
            self.assertRegex(block, r'<img src="[^"]+-light\.svg"')

    def test_contact_email_matches_the_script(self):
        script = (ROOT / "scripts" / "generate.py").read_text(encoding="utf-8")
        emails = set(re.findall(r"[\w.]+@gmail\.com", self.readme))
        self.assertEqual(len(emails), 1, emails)
        self.assertIn(next(iter(emails)), script)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self._sleep, self._open = g.time.sleep, g.urllib.request.urlopen
        g.time.sleep = lambda s: None

    def tearDown(self):
        g.time.sleep, g.urllib.request.urlopen = self._sleep, self._open

    def run_sequence(self, sequence):
        calls = []

        def fake(request, timeout=0):
            item = sequence[len(calls)]
            calls.append(item)
            if isinstance(item, Exception):
                raise item
            return Resp(json.dumps(item).encode())

        g.urllib.request.urlopen = fake
        try:
            return g.api_json(urllib.request.Request("http://example.invalid")), len(calls)
        except Exception as error:  # noqa: BLE001
            return error, len(calls)

    @staticmethod
    def http(code):
        return urllib.error.HTTPError("http://example.invalid", code, "x", {}, None)

    def test_retries_transient_errors(self):
        for failure in (self.http(503), self.http(429), TimeoutError()):
            self.assertEqual(self.run_sequence([failure, {"ok": 1}]), ({"ok": 1}, 2))

    def test_does_not_retry_client_errors(self):
        result, calls = self.run_sequence([self.http(404), {"ok": 1}])
        self.assertIsInstance(result, urllib.error.HTTPError)
        self.assertEqual(calls, 1)

    def test_gives_up_after_four_attempts(self):
        result, calls = self.run_sequence([self.http(500)] * 4)
        self.assertIsInstance(result, urllib.error.HTTPError)
        self.assertEqual(calls, 4)

    def test_events_are_ordered_by_time_across_new_year(self):
        def event(stamp):
            return {"repo": {"name": "Khanh1916/r"}, "created_at": stamp, "type": "PushEvent", "payload": {"size": 1}}
        stamps = ["2027-01-03T01:00:00Z", "2026-12-30T01:00:00Z", "2027-01-04T01:00:00Z", "2026-12-29T01:00:00Z",
                  "2026-12-31T01:00:00Z", "2027-01-02T01:00:00Z", "2027-01-05T01:00:00Z"]
        g.urllib.request.urlopen = lambda request, timeout=0: Resp(json.dumps([event(s) for s in stamps]).encode())
        import os
        os.environ.setdefault("GITHUB_TOKEN", "test")
        days = [line[:5] for line in g.fetch_events({"r"})]
        self.assertEqual(days, ["12-31", "01-02", "01-03", "01-04", "01-05"])


class UnitTest(unittest.TestCase):
    def test_summarize_keeps_first_sentence_and_fits(self):
        lines = g.summarize("First sentence here. Second sentence is dropped.")
        self.assertEqual(lines, ["First sentence here."])
        long = g.summarize("word " * 80)
        self.assertLessEqual(len(long), 3)
        self.assertTrue(all(len(line) <= 44 for line in long))

    def test_text_default_colour_follows_the_theme(self):
        try:
            g.apply_theme("light")
            self.assertIn(g.THEMES["light"]["TEXT"], g.t(0, 0, "x", 12))
            g.apply_theme("dark")
            self.assertIn(g.THEMES["dark"]["TEXT"], g.t(0, 0, "x", 12))
        finally:
            g.apply_theme("dark")

    def test_palette_text_contrast_is_at_least_aa(self):
        def lin(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        def lum(h):
            r, gr, b = (int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            return 0.2126 * lin(r) + 0.7152 * lin(gr) + 0.0722 * lin(b)

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        on_panel = ("TEXT", "TITLE", "SOFT", "DIM", "LED_SOFT", "ACCENT", "GRID", "RED", "BLUE", "GREEN")
        on_bg = ("TEXT", "TITLE", "SOFT", "ACCENT", "RED")  # the card chips and the oracle input box sit on BG
        for theme, palette in g.THEMES.items():
            for ground, names in (("PANEL", on_panel), ("BG", on_bg)):
                for name in names:
                    self.assertGreaterEqual(ratio(palette[name], palette[ground]), 4.5, f"{theme} {name} on {ground}")


if __name__ == "__main__":
    unittest.main()
