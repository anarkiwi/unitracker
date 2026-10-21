"""Lift the reference tunes and print each one's verify verdict on unitracker-player.

Prints each tune's verdict, its differing registers and the forbidden words it holds;
``--json PATH`` writes the machine-readable diffs of the tuned tunes.

    python tools/reference_check.py OUTDIR [--forbid WORD ...] [--tunes SID ...] [--json PATH]
"""

import argparse
import json
import re
import time
from pathlib import Path

from unitracker.lift import backends
from unitracker.player.vm import runs

CACHE = Path(__file__).resolve().parent.parent / ".tunecache"
TUNES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
)


def span(a: int, b: int) -> str:
    """A tick or a run of ticks."""
    return str(a) if a == b else f"{a}-{b}"


def brief(verdict) -> str:
    """A mismatch's differing registers on one line: each, its span, and its first values."""
    entries = runs(list(verdict.cells))
    if not entries:
        return ""
    shown = entries[:6]
    parts = [
        f"${e['reg']:02X} {e['name']}: {e['cells']} {'tick' if e['cells'] == 1 else 'ticks'} "
        f"{span(e['from'], e['to'])} (doc {e['runs'][0]['values'][0][2]:02x}, "
        f"native {e['runs'][0]['values'][0][3]:02x})"
        for e in shown
    ]
    extra = len(entries) - len(shown)
    if extra:
        parts.append(f"+{extra} more")
    return "; ".join(parts)


def check(sid: Path, out: Path, forbid) -> tuple[str, object]:
    """One tune's report line and its verdict, or :class:`None` without one."""
    lift = backends.lifter(sid.read_bytes())
    lift.run()
    if lift.document is None:
        return f"no generic document: {lift.gap}", None
    out.write_text(lift.document, encoding="utf-8")
    found = sorted({w for w in forbid if re.search(rf"(?<![\w]){re.escape(w)}\b", lift.document)})
    verdict = lift.verdict
    if verdict is None:
        return f"unverified: {lift.gap}; forbidden: {found or 'none'}", None
    line = verdict.line()
    if not verdict.ok:
        line += f" | {brief(verdict)}"
    return f"{line}; forbidden: {found or 'none'}", verdict


def main(argv=None) -> int:
    """Command-line entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--forbid", nargs="*", default=[])
    parser.add_argument("--tunes", nargs="*", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    tunes = args.tunes or [CACHE / t for t in TUNES]
    verdicts = {}
    for k, sid in enumerate(tunes, start=1):
        start = time.monotonic()
        line, verdict = check(sid, args.out / f"{sid.stem}.yaml", args.forbid)
        if verdict is not None:
            verdicts[sid.stem] = verdict.summary()
        print(
            f"[{k}/{len(tunes)}] {sid.stem} ({time.monotonic() - start:.0f}s): {line}", flush=True
        )
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(verdicts, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
