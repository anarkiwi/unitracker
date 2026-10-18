"""Check that every relative Markdown link in README.md and docs/ resolves: its file or directory
exists and its ``#anchor``, if any, is a heading of the target file (GitHub's heading slugs).

Usage: python tools/doc_links.py [ROOT]; prints each broken link and exits 1 if there is one.
"""

import re
import sys
from functools import cache
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6} (.+?)\s*#*$")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as ``-``."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


@cache
def anchors(doc: Path) -> frozenset:
    """The anchors of ``doc``'s headings outside code fences, repeats suffixed ``-1``, ``-2``."""
    seen, fenced = {}, False
    for line in doc.read_text(encoding="utf-8").splitlines():
        fenced ^= line.startswith("```")
        match = None if fenced else HEADING.match(line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            seen.setdefault(f"{base}-{count}" if count else base, 1)
    return frozenset(seen)


def problem(doc: Path, target: str) -> bool:
    """Whether relative link ``target`` in ``doc`` names a missing file or anchor."""
    path, _, anchor = target.partition("#")
    dest = doc.parent / path if path else doc
    if not dest.exists():
        return True
    return bool(anchor) and dest.is_file() and anchor not in anchors(dest.resolve())


def broken(root: Path) -> list[str]:
    """``file:line: target`` for each relative link under ``root`` that does not resolve."""
    out = []
    for doc in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        fenced = False
        for num, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            fenced ^= line.startswith("```")
            for target in [] if fenced else LINK.findall(line):
                if not EXTERNAL.match(target) and problem(doc, target):
                    out.append(f"{doc.relative_to(root)}:{num}: {target}")
    return out


def main(argv=None) -> int:
    """Print the broken links; 1 when there are any."""
    args = sys.argv[1:] if argv is None else argv
    found = broken(Path(args[0]) if args else Path(__file__).resolve().parents[1])
    print("\n".join(found) or "all links resolve")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
