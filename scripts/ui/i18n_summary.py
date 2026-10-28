"""What the language audit (i18n.spec.ts) found, by English label: in how many languages and places a translation
broke the design, and the catalogue entry behind it.

    PYTHONPATH=. .venv/bin/python scripts/ui/i18n_summary.py before          # reports/i18n-before/*.json
    ... before --json labels.json                                            # the labels, for the glossary's LABELS

A label is matched to its catalogue entry by its English as drawn: exactly, or with its values (%(name)s) as wildcards.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from scripts import i18n_catalogs as cat

ROOT = Path(__file__).resolve().parents[2]

SEVERE = ("page scrolls sideways", "sticks out of its box", "cut off", "wraps onto another line")


def entries():
    """(msgid, context, domain) for every singular entry of the templates, and a matcher for each."""
    out = []
    for domain in cat.DOMAINS:
        for message in cat.read(cat.OUT / f"{domain}.pot"):
            if not message.id or not isinstance(message.id, str) or len(message.id) > 80:
                continue
            text = re.sub(r"<[^>]+>", "", message.id).replace("%%", "%")
            pattern = re.escape(text)
            pattern = re.sub(r"%\\\(\w+\\\)[sd]", ".+?", pattern)
            out.append((message.id, message.context or "", domain, re.compile(rf"\s*{pattern}\s*$")))
    return out


def main(argv):
    label = argv[0] if argv else "before"
    folder = ROOT / "scripts" / "ui" / "reports" / f"i18n-{label}"
    found = defaultdict(lambda: {"languages": set(), "problems": defaultdict(int), "examples": {}, "pages": set()})
    for report in sorted(folder.glob("*.json")):
        if report.stem in ("en", "summary"):
            continue
        data = json.loads(report.read_text())
        for p in data["problems"]:
            if not p["english"]:
                found[("(page)", "")]["problems"][p["problem"]] += 1
                found[("(page)", "")]["languages"].add(data["lang"])
                found[("(page)", "")]["pages"].add(f'{p["page"]}@{p["width"]}')
                continue
            row = found[(p["english"], "")]
            row["languages"].add(data["lang"])
            row["problems"][p["problem"]] += 1
            row["pages"].add(f'{p["page"]}@{p["width"]}')
            row["examples"].setdefault(data["lang"], p["text"])
    matchers = entries()
    labels = []
    for (english, _c), row in sorted(found.items(), key=lambda kv: (-len(kv[1]["languages"]), kv[0][0])):
        hits = [(msgid, ctx, domain) for msgid, ctx, domain, rx in matchers if rx.match(english)]
        severe = sum(n for k, n in row["problems"].items() if k in SEVERE)
        labels.append({"english": english, "entries": hits[:4], "languages": len(row["languages"]),
                       "severe": severe, "problems": dict(row["problems"]), "pages": sorted(row["pages"])[:6],
                       "examples": dict(list(row["examples"].items())[:6])})
    for item in labels:
        print(f'{item["languages"]:>2} langs  severe {item["severe"]:>3}  {item["english"]!r:40} '
              f'{[e[0] if not e[1] else e[0] + " |" + e[1] for e in item["entries"]][:2]}  {item["problems"]}')
    if len(argv) > 2 and argv[1] == "--json":
        Path(argv[2]).write_text(json.dumps(labels, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
