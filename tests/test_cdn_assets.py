"""Scripts from a CDN are pinned to an exact version and hash-checked, and none is older than the fix of the holes the
bug hunt found (2026-10, L7: jQuery 3.1.1 / 3.2.1, Bootstrap 4.0.0's JavaScript, and `clipboard@2` /
`code-prettify@master`, which load whatever their authors publish next).

Not clean, and known: Bootstrap 4.3.1's JavaScript still has CVE-2024-6531 (the carousel's `data-slide` attribute), which
no 4.x release fixes. No page has a carousel and member HTML is sanitized; moving to Bootstrap 5 is its own job."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "ruqqus" / "templates"
CDNS = ("code.jquery.com", "cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "bootstrapcdn.com")
SCRIPT = re.compile(r"<script\b[^>]*\bsrc=\"(https://[^\"]+)\"[^>]*>", re.I)


def _cdn_scripts():
    for path in TEMPLATES.rglob("*.html"):
        for match in SCRIPT.finditer(path.read_text(encoding="utf-8")):
            if any(cdn in match.group(1) for cdn in CDNS):
                yield path.relative_to(TEMPLATES).as_posix(), match.group(1), match.group(0)


def test_no_cdn_script_follows_a_moving_version():
    moving = [(name, src) for name, src, _ in _cdn_scripts()
              if re.search(r"@(master|main|latest|\d+(\.\d+)?)/", src)]
    assert moving == []


def test_jquery_and_bootstraps_javascript_are_versions_without_known_holes():
    for name, src, _ in _cdn_scripts():
        jquery = re.search(r"jquery-(\d+)\.(\d+)\.(\d+)", src)
        if jquery:
            assert tuple(map(int, jquery.groups())) >= (3, 5, 0), (name, src)       # CVE-2020-11022 / 11023
        bootstrap = re.search(r"bootstrap/(\d+)\.(\d+)\.(\d+)/js/", src)
        if bootstrap:
            assert tuple(map(int, bootstrap.groups())) >= (4, 3, 1), (name, src)    # CVE-2019-8331


def test_every_cdn_script_and_stylesheet_is_hash_checked():
    # every one, not a list of names (2026-11-19: the map on /regions and the animations on About and Donate had none): a script
    # from another host runs with a signed-in member's full powers, since the site has no script-src policy
    for name, src, tag in _cdn_scripts():
        assert 'integrity="sha' in tag and 'crossorigin="anonymous"' in tag, (name, src)
    # the stylesheets that come with those two (a stylesheet runs no script: Font Awesome's, on every page, has no hash yet)
    sheet = re.compile(r"<link\b[^>]*\bhref=\"(https://[^\"]+(?:jsvectormap|aos)[^\"]*)\"[^>]*>", re.I)
    found = [(path.name, match.group(0)) for path in TEMPLATES.rglob("*.html") for match in sheet.finditer(path.read_text(encoding="utf-8"))]
    assert len(found) == 3
    for name, tag in found:
        assert 'integrity="sha' in tag and 'crossorigin="anonymous"' in tag, (name, tag)


def test_no_template_loads_a_social_sites_script():
    """The owner's rule (CLAUDE.md, Social embeds): no page loads a social site's script or widget. The old per-domain
    templates for X and Instagram did, and drew the site's oEmbed HTML raw."""
    loads = re.compile(r"platform\.twitter\.com|platform\.instagram\.com|connect\.facebook\.net|tiktok\.com/embed\.js", re.I)
    found = [p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html") if loads.search(p.read_text(encoding="utf-8"))]
    assert found == []
