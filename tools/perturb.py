"""Edit a written song and replay it: against the native player, or against another edit.

    python tools/perturb.py DOC.yaml --sid TUNE.sid 'old=>new' ... [--ticks N]
    python tools/perturb.py DOC.yaml --same 'old=>new;;old=>new' 'old=>new;;...' [--ticks N]

With ``--sid`` each edit (the first occurrence of ``old`` replaced by ``new``) prints the first
mismatch of its replay against the tune's native player, or the error reading it; with ``--same``
the two edit sets (``;;``-separated edits) print whether their replays are identical.
"""

import argparse
from pathlib import Path

import numpy as np

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch


def edited(text: str, edits: str) -> str:
    """``text`` with each ``old=>new`` of ``;;``-separated ``edits`` applied once."""
    for edit in filter(None, edits.split(";;")):
        old, new = edit.split("=>")
        if old not in text:
            raise ValueError(f"{old!r} is not in the document")
        text = text.replace(old, new, 1)
    return text


def against_native(text: str, sid: Path, edits: list, ticks: int) -> list[str]:
    """One line per edit: its replay's first mismatch against the native player."""
    native = np.asarray(
        backends.native_player(sid.read_bytes(), None).render_grid(ticks), dtype=np.uint8
    )
    out = []
    for edit in edits:
        try:
            player = SongPlayer(songio.loads(edited(text, edit)))
            out.append(
                f"{edit} -> {first_mismatch(player.render_grid(ticks), native, player.starts)}"
            )
        except ValueError as exc:
            out.append(f"{edit} -> error {type(exc).__name__}: {exc}")
    return out


def same(text: str, first: str, second: str, ticks: int) -> str:
    """Whether the replays of two edit sets are identical, else the first tick and register."""
    a, b = (SongPlayer(songio.loads(edited(text, e))).render_grid(ticks) for e in (first, second))
    diff = np.argwhere(a != b)
    return "identical" if diff.size == 0 else f"differ first at {tuple(int(x) for x in diff[0])}"


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc", type=Path)
    parser.add_argument("edits", nargs="+")
    parser.add_argument("--sid", type=Path)
    parser.add_argument("--same", action="store_true")
    parser.add_argument("--ticks", type=int, default=3007)
    args = parser.parse_args(argv)
    text = args.doc.read_text()
    if args.same:
        if len(args.edits) != 2:
            parser.error("--same compares two edit sets")
        print(same(text, *args.edits, args.ticks))
    elif args.sid is not None:
        print("\n".join(against_native(text, args.sid, args.edits, args.ticks)))
    else:
        parser.error("give --sid or --same")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
