"""Print each library playroutine's command and ``ctrl`` row-key tables as its doc should hold them.

    python tools/doc_tables.py [--write]

The tables are the ones ``tests/test_playroutine_docs.py`` checks against the playroutine docs;
``--write`` replaces them in the docs.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import test_playroutine_docs as T  # noqa: E402  pylint: disable=wrong-import-position


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    for player in T.PLAYERS:
        path = T.DOCS / f"{player}.md"
        lines = path.read_text().splitlines()
        for header, new in T.tables(player).items():
            if header not in lines:
                continue
            start = lines.index(header)
            end = next(
                (k for k in range(start, len(lines)) if not lines[k].startswith("|")), len(lines)
            )
            print(f"{player}:\n" + "\n".join(new) + "\n")
            lines[start:end] = new
        if args.write:
            path.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
