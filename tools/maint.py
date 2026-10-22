"""Repository upkeep: the docs' relative links, the playroutine docs' generated tables, and the
lines one coverage run executes that another does not."""

import argparse
import json
import re
import sys
from functools import cache
from pathlib import Path

from coverage.results import format_lines

import shared

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6} (.+?)\s*#*$")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as ``-``."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def unfenced(doc: Path):
    """``(line number, line)`` of ``doc``'s lines outside code fences."""
    fenced = False
    for num, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        fenced ^= line.startswith("```")
        if not fenced:
            yield num, line


@cache
def anchors(doc: Path) -> frozenset:
    """The anchors of ``doc``'s headings, repeats suffixed ``-1``, ``-2``."""
    seen = {}
    for _, line in unfenced(doc):
        match = HEADING.match(line)
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
    """``file:line: target`` for each relative link in README.md and docs/ that does not
    resolve: a missing file or directory, or an ``#anchor`` no heading of the target has."""
    return [
        f"{doc.relative_to(root)}:{num}: {target}"
        for doc in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
        for num, line in unfenced(doc)
        for target in LINK.findall(line)
        if not EXTERNAL.match(target) and problem(doc, target)
    ]


def tables(write: bool) -> None:
    """Print each library playroutine's command and ``ctrl`` row-key tables as its doc should
    hold them (``tests/test_playroutine_docs.py``); ``write`` replaces them in the docs."""
    sys.path.insert(0, str(shared.ROOT / "tests"))
    import test_playroutine_docs as T  # pylint: disable=import-outside-toplevel,import-error

    for player in T.PLAYERS:
        path = T.DOCS / f"{player}.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        for header, new in T.tables(player).items():
            if header in lines:
                start = lines.index(header)
                lines[start : start + len(T.documented(player, header))] = new
                print(f"{player}:\n" + "\n".join(new) + "\n")
        if write:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def lost(before: str, after: str, modules=()) -> list[str]:
    """Per module, the lines coverage report ``before`` executes and ``after`` does not (two
    ``pytest --cov-report=json:FILE`` reports)."""

    def files(path: str) -> dict:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["files"]

    was, now, out = files(before), files(after), []
    for name in sorted(was):
        if modules and not any(m in name for m in modules):
            continue
        ran = set(was[name]["executed_lines"])
        gone = ran - set(now.get(name, {}).get("executed_lines", ()))
        if gone:
            total = max(was[name]["summary"]["num_statements"], 1)
            pct = 100 * len(ran) / total, 100 * (len(ran) - len(gone)) / total
            out.append(f"{name}: {pct[0]:.1f}% -> {pct[1]:.1f}%, lost {format_lines(ran, gone)}")
    return out


def main(argv=None) -> int:
    """Parse the command line and run the command; 1 when links break or lines are lost."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("links", help="every relative link in README.md and docs/ resolves")
    p.add_argument("root", type=Path, nargs="?", default=shared.ROOT)
    p = sub.add_parser("tables", help="the playroutine docs' generated tables")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("coverage", help="lines a coverage run loses against an earlier one")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--module", nargs="*", default=[], help="only files containing these")
    args = ap.parse_args(argv)
    if args.command == "tables":
        tables(args.write)
        return 0
    if args.command == "links":
        found = broken(args.root)
        print("\n".join(found) or "all links resolve")
    else:
        found = lost(args.before, args.after, args.module)
        print("\n".join(found))
    return int(bool(found))


if __name__ == "__main__":
    sys.exit(main())
