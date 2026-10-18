"""Check that every relative Markdown link in README.md and docs/ resolves to a file or directory.

Usage: python tools/doc_links.py [ROOT]; prints each broken link and exits 1 if there is one.
"""

import re
import sys
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:|^#", re.IGNORECASE)


def broken(root: Path) -> list[str]:
    """``file:line: target`` for each relative link under ``root`` whose target is missing."""
    out = []
    for doc in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        for num, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for target in LINK.findall(line):
                path = target.split("#", 1)[0]
                if not EXTERNAL.match(target) and not (doc.parent / path).exists():
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
