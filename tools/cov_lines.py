"""Source lines one coverage run executes and another does not, per module.

    python tools/cov_lines.py BEFORE.json AFTER.json [--module SUBSTRING ...]

Reads two ``pytest --cov=unitracker --cov-report=json:FILE`` reports; exits 1 if any line is lost.
"""

import argparse
import json
import sys


def covered(path: str) -> dict:
    """``{file: (executed lines, statement count)}`` of a coverage json report."""
    with open(path, encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    return {
        name: (set(data["executed_lines"]), data["summary"]["num_statements"])
        for name, data in files.items()
    }


def ranges(lines) -> str:
    """``1-3,7``: sorted line numbers as runs."""
    out, run = [], []
    for n in sorted(lines):
        if run and n != run[-1] + 1:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
            run = []
        run.append(n)
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ",".join(out)


def main(argv=None) -> int:
    """Print the lines lost between two coverage reports; 1 if any."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--module", nargs="*", default=[], help="only files containing these")
    args = ap.parse_args(argv)
    before, after = covered(args.before), covered(args.after)
    lost_any = False
    for name in sorted(before):
        if args.module and not any(m in name for m in args.module):
            continue
        was, total = before[name]
        now = after.get(name, (set(), total))[0]
        if was - now:
            lost_any = True
            pct = 100 * len(was) / max(total, 1), 100 * len(now) / max(total, 1)
            print(f"{name}: {pct[0]:.1f}% -> {pct[1]:.1f}%, lost {ranges(was - now)}")
    return int(lost_any)


if __name__ == "__main__":
    sys.exit(main())
