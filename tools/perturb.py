"""Edit a written song and replay it: against the native player, against another edit, or
against the unedited song with what the reader and writer make of the edit.

    python tools/perturb.py DOC.yaml --sid TUNE.sid 'old=>new' ... [--ticks N]
    python tools/perturb.py DOC.yaml --same 'old=>new;;old=>new' 'old=>new;;...' [--ticks N]
    python tools/perturb.py DOC.yaml --probe 'old=>new;;...' 're:pattern=>repl' ... [--ticks N]
    python tools/perturb.py DOC.yaml --fuzz

Each edit set is ``;;``-separated edits, each replacing the first occurrence of ``old`` by
``new`` (``re:`` edits substitute a regular expression everywhere). With ``--sid`` each edit set
prints the first mismatch of its replay against the tune's native player, or the error reading
it; with ``--same`` the two edit sets print whether their replays are identical; with
``--probe`` each edit set prints whether the reader refuses it, or accepts it and the writer
keeps or rewrites it, and whether it plays as the unedited song; with ``--fuzz`` each value of the
document replaced by each of a few others prints any error the reader raises but its own.
"""

import argparse
import copy
import difflib
import re
from pathlib import Path

from tqdm import tqdm

import numpy as np

from unitracker.format import songio
from unitracker.format.yamlstyle import dump, parse
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer, first_mismatch


def edited(text: str, edits: str) -> str:
    """``text`` with each edit of ``;;``-separated ``edits`` applied: ``old=>new`` once, and
    ``re:pattern=>repl`` everywhere."""
    for edit in filter(None, edits.split(";;")):
        if edit.startswith("re:"):
            pattern, new = edit[3:].split("=>")
            text, count = re.subn(pattern, new, text)
            if not count:
                raise ValueError(f"{pattern!r} matches nothing in the document")
            continue
        old, new = edit.split("=>")
        if old not in text:
            raise ValueError(f"{old!r} is not in the document")
        text = text.replace(old, new, 1)
    return text


def grid(text: str, ticks: int) -> np.ndarray:
    """The register grid of document ``text``'s first ``ticks`` ticks."""
    return np.asarray(SongPlayer(songio.loads(text)).render_grid(ticks))


def outcome(text: str) -> tuple[str, str | None]:
    """``(verdict, written)`` of document ``text``: ``refused`` with no text, else ``kept`` or
    ``rewritten`` with the text the writer writes for what the reader reads."""
    try:
        written = songio.dumps(songio.loads(text))
    except songio.SongFormatError:
        return "refused", None
    return ("kept" if written == text else "rewritten"), written


def probe(text: str, edits: str, ticks: int, base=None) -> str:
    """One line for an edit set of ``text``: its outcome, the changed lines of a rewrite, and
    whether it plays as ``text`` (grid ``base``) does."""
    try:
        new = edited(text, edits)
    except ValueError as exc:
        return f"{edits} -> edit fails: {exc}"
    try:
        verdict, written = outcome(new)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return f"{edits} -> error {type(exc).__name__}: {exc}"
    if written is None:
        return f"{edits} -> {verdict}: {_problem(new)}"
    base = grid(text, ticks) if base is None else base
    diff = np.argwhere(grid(new, ticks) != base)
    plays = "as before" if diff.size == 0 else f"differently at {tuple(map(int, diff[0]))}"
    changed = [
        line
        for line in difflib.unified_diff(new.splitlines(), written.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in ("---", "+++")
    ]
    shown = f"; {' | '.join(changed[:6])}" if changed else ""
    return f"{edits} -> {verdict}, plays {plays}{shown}"


def _problem(text: str) -> str:
    try:
        songio.loads(text)
    except songio.SongFormatError as exc:
        return str(exc)
    return ""


ODD = ("x", [1], -1, 99999, True, None, {"a": 1}, "v + 1")


def _paths(node, path=()):
    """Each path into parsed document ``node``, leaves first."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _paths(v, (*path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _paths(v, (*path, i))
    if path:
        yield path


def fuzz(text: str, odd=ODD, progress: bool = False) -> list[str]:
    """One line per error other than ``SongFormatError`` the reader raises for ``text`` with one
    value replaced by one of ``odd``."""
    base, out, seen = parse(text), [], set()
    edits = [(p, v) for p in _paths(base) if p[0] != "unitracker" for v in odd]
    for path, value in tqdm(edits, desc="fuzz", unit="doc", disable=not progress):
        doc = copy.deepcopy(base)
        node = doc
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
        try:
            songio.loads(dump(doc))
        except songio.SongFormatError:
            continue
        except Exception as exc:  # pylint: disable=broad-exception-caught
            kind = (type(exc).__name__, str(exc)[:60])
            if kind not in seen:
                seen.add(kind)
                out.append(f"{'.'.join(map(str, path))} = {value!r} -> {type(exc).__name__}: {exc}")
    return out


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
    a, b = (grid(edited(text, e), ticks) for e in (first, second))
    diff = np.argwhere(a != b)
    return "identical" if diff.size == 0 else f"differ first at {tuple(int(x) for x in diff[0])}"


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc", type=Path)
    parser.add_argument("edits", nargs="*")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sid", type=Path)
    mode.add_argument("--same", action="store_true")
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--fuzz", action="store_true")
    parser.add_argument("--ticks", type=int, default=3007)
    args = parser.parse_args(argv)
    text = args.doc.read_text()
    if args.same:
        if len(args.edits) != 2:
            parser.error("--same compares two edit sets")
        print(same(text, *args.edits, args.ticks))
    elif args.fuzz:
        print("\n".join(fuzz(text, progress=True)) or "the reader raises only SongFormatError")
    elif args.probe:
        base = grid(text, args.ticks)
        for edit in args.edits:
            print(probe(text, edit, args.ticks, base), flush=True)
    else:
        print("\n".join(against_native(text, args.sid, args.edits, args.ticks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
