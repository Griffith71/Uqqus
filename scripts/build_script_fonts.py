"""Fetch the Noto fonts for the scripts Noto Sans doesn't draw and write their @font-face stylesheets.

    python3 scripts/build_script_fonts.py            # fetch the pinned versions, write ruqqus/assets/fonts/scripts/
    python3 scripts/build_script_fonts.py --check    # only check that the stylesheets name the files as they are

The site's typeface is Noto Sans (latin, latin-ext, cyrillic, greek, vietnamese, devanagari: partials/theme.html). The
interface's other scripts get their own Noto family, self-hosted like Noto Sans (the owner, 2026-10-29): Arabic script
(Arabic, Persian) in Noto Sans Arabic, Urdu in Noto Nastaliq Urdu, Hebrew, Bengali, Thai, and Chinese (Simplified and
Traditional), Japanese and Korean in Noto Sans SC / TC / JP / KR. The files are fontsource's variable packages from
cdn.jsdelivr.net (the same files Google Fonts serves), pinned below; each package's OFL text is kept beside them.

Only each package's own script subsets are taken (Noto Sans already serves Latin, Cyrillic, Greek and Vietnamese). The
CJK packages are cut into numbered slices whose ranges also hold Latin letters, emoji and Private Use code points (the
ones Font Awesome draws): a slice's range is cut down to the CJK blocks (`CJK_BLOCKS`), so an emoji keeps its colour font
and a Latin page never fetches a CJK slice for a stray symbol; a slice left with nothing is not shipped.

Each family is one stylesheet (assets/fonts/<package>.css), its faces naming their files with `?v=` set to a hash of the file, so
routes/static.py caches them for a year. partials/theme.html loads the interface language's own stylesheet in <head> and
the others only when a page holds text in their script (tests/test_fonts_assets.py).
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "ruqqus" / "assets" / "fonts"
OUT = FONTS / "scripts"   # the font files, their licences and the manifest
# The stylesheets sit one folder up, in assets/fonts/: an address /assets/<a>/<b>/<x>.css is a guild's stylesheet
# (routes/boards.py), so assets/fonts/scripts/<x>.css could never be served.
CDN = "https://cdn.jsdelivr.net/npm/@fontsource-variable"

# family -> (package, version, the subsets taken, the licence file's name); CJK packages take every numbered slice
FAMILIES = {
    "Noto Sans Arabic": ("noto-sans-arabic", "5.3.0", ("arabic",), "NotoSansArabic-OFL.txt"),
    "Noto Nastaliq Urdu": ("noto-nastaliq-urdu", "5.3.0", ("arabic",), "NotoNastaliqUrdu-OFL.txt"),
    "Noto Sans Hebrew": ("noto-sans-hebrew", "5.3.0", ("hebrew",), "NotoSansHebrew-OFL.txt"),
    "Noto Sans Bengali": ("noto-sans-bengali", "5.3.0", ("bengali",), "NotoSansBengali-OFL.txt"),
    "Noto Sans Thai": ("noto-sans-thai", "5.3.0", ("thai",), "NotoSansThai-OFL.txt"),
    "Noto Sans SC": ("noto-sans-sc", "5.3.1", None, "NotoSansSC-OFL.txt"),
    "Noto Sans TC": ("noto-sans-tc", "5.3.1", None, "NotoSansTC-OFL.txt"),
    "Noto Sans JP": ("noto-sans-jp", "5.3.1", None, "NotoSansJP-OFL.txt"),
    "Noto Sans KR": ("noto-sans-kr", "5.3.1", None, "NotoSansKR-OFL.txt"),
}

# What a CJK slice keeps: Hangul Jamo, CJK radicals and Kangxi, ideographic description, CJK symbols and punctuation,
# kana, Bopomofo, Hangul compatibility Jamo, Kanbun, strokes, enclosed CJK and CJK compatibility, Han (extension A, the
# unified block, compatibility ideographs), Hangul Jamo extensions and syllables, vertical and compatibility forms,
# half- and full-width forms, the kana supplements and the supplementary ideographic planes.
CJK_BLOCKS = (
    (0x1100, 0x11FF), (0x2E80, 0x2FDF), (0x2FF0, 0x303F), (0x3040, 0x30FF), (0x3100, 0x312F), (0x3130, 0x318F),
    (0x3190, 0x31FF), (0x3200, 0x32FF), (0x3300, 0x33FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xA960, 0xA97F),
    (0xAC00, 0xD7FF), (0xF900, 0xFAFF), (0xFE10, 0xFE1F), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF), (0x1AFF0, 0x1B16F),
    (0x20000, 0x3134F),
)

FACE = re.compile(r"/\* (?P<name>[^*]+?)-wght-normal \*/\s*@font-face \{(?P<body>[^}]*)\}")


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as answer:
        return answer.read()


def parse_ranges(text):
    out = []
    for part in text.split(","):
        part = part.strip().upper().removeprefix("U+")
        lo, _, hi = part.partition("-")
        out.append((int(lo, 16), int(hi or lo, 16)))
    return out


def format_ranges(ranges):
    return ",".join(f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-{hi:04X}" for lo, hi in ranges)


def cut_to(ranges, blocks):
    """The parts of `ranges` inside `blocks`, merged and in order."""
    kept = []
    for lo, hi in ranges:
        for b_lo, b_hi in blocks:
            a, b = max(lo, b_lo), min(hi, b_hi)
            if a <= b:
                kept.append((a, b))
    kept.sort()
    merged = []
    for lo, hi in kept:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def version_of(data):
    return hashlib.sha256(data).hexdigest()[:10]


def stylesheet_name(package):
    return f"{package}.css"


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for family, (package, version, subsets, licence) in FAMILIES.items():
        base = f"{CDN}/{package}@{version}"
        css = fetch(f"{base}/wght.css").decode("utf-8")
        faces = []
        for match in FACE.finditer(css):
            name, body = match.group("name"), match.group("body")
            subset = name[len(package) + 1:]
            numbered = re.fullmatch(r"\[(\d+)\]", subset)
            if subsets is None and not numbered:
                continue
            if subsets is not None and subset not in subsets:
                continue
            ranges = parse_ranges(re.search(r"unicode-range:\s*([^;]+);", body).group(1))
            if subsets is None:
                ranges = cut_to(ranges, CJK_BLOCKS)
                if not ranges:
                    continue
            source = re.search(r"url\(\./files/([^)]+)\)", body).group(1)
            target = f"{package}-{numbered.group(1)}.woff2" if numbered else f"{package}.woff2"
            data = fetch(f"{base}/files/{source}")
            assert data[:4] == b"wOF2", source
            (OUT / target).write_bytes(data)
            faces.append((target, ranges))
        (OUT / licence).write_bytes(fetch(f"{base}/LICENSE"))
        write_css(family, package, version, faces)
        manifest[family] = {"package": f"@fontsource-variable/{package}", "version": version,
                            "files": [t for t, _ in faces]}
        print(f"{family}: {len(faces)} faces", file=sys.stderr)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def write_css(family, package, version, faces):
    lines = [f"/* {family}: @fontsource-variable/{package}@{version} (SIL OFL 1.1, scripts/{FAMILIES[family][3]}),"
             f" written by scripts/build_script_fonts.py */"]
    for target, ranges in faces:
        data = (OUT / target).read_bytes()
        lines.append(f'@font-face{{font-family:"{family}";font-style:normal;font-weight:100 900;font-display:swap;'
                     f'src:url("scripts/{target}?v={version_of(data)}") format("woff2");unicode-range:{format_ranges(ranges)}}}')
    (FONTS / stylesheet_name(package)).write_text("\n".join(lines) + "\n", encoding="utf-8")


def check():
    """Every face names an existing file at its current hash."""
    bad = []
    for family, (package, *_rest) in FAMILIES.items():
        css = (FONTS / stylesheet_name(package)).read_text(encoding="utf-8")
        for target, v in re.findall(r'url\("scripts/([^"?]+)\?v=([0-9a-f]+)"\)', css):
            file = OUT / target
            if not file.exists() or version_of(file.read_bytes()) != v:
                bad.append(target)
    return bad


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    if parser.parse_args().check:
        problems = check()
        print("\n".join(problems) or "ok")
        sys.exit(1 if problems else 0)
    build()
