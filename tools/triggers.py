"""What starts each program of documents (a note, an event's or a row's ``run:``, a restart), the
starts replaced unplayed, modulator bindings and phase resets, and programs that do nothing.

``python tools/triggers.py DOC.yaml|LIST.txt ... [--ticks N] [--jobs J] [--out TRIGGERS.json]``
"""

import argparse
import ast
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pysidtracker.jobs import deadline, imap
from tqdm import tqdm
from unitracker_grammar.cells import VOICE_CELLS
from unitracker_player.compiler import CONTROL, FILTER, PULSE, RESET
from unitracker_player.player import Player, load

KINDS = {CONTROL: "ctrl", PULSE: "pw", FILTER: "filter"}
REL0 = ("note", ("rel", 0))
KEYS = (
    "static",
    "starts",
    "replaced",
    "instruments",
    "rel0",
    "restart_ctrl",
    "restart_movable",
    "binds",
    "zeroed",
)


class Tracer:  # pylint: disable=too-many-instance-attributes
    """An observer of a play recording each program start's trigger and whether it played a
    row, each modulator binding's outcome and, per slot and tick, what zeroed its phase."""

    def __init__(self):
        self.player, self.via, self.starts, self.live = None, [], Counter(), {}
        self.replaced, self.ins_starts, self.rel0 = Counter(), defaultdict(Counter), {}
        self.noted, self.cur_ins, self.restart_ctrl, self.moved = {}, {}, Counter(), set()
        self.binds, self.zeroed = Counter(), defaultdict(list)

    def __call__(self, kind: str, *args):
        getattr(self, kind, lambda *_: None)(*args)

    def _trigger(self) -> str:
        return self.via[-1][0] if self.via else "init"

    def _mods(self, v: int, cell: str) -> list:
        return (
            self.player.voices[v].mods[cell] if cell in VOICE_CELLS else self.player.song_mods[cell]
        )

    def _zero(self, v, name: str, how: str):
        self.zeroed["cut" if name.endswith(".cut") else v, name, self.player.tick].append(how)

    def event(self, v, event, ins):
        """Notes the voice's instrument and the call a note sounds at."""
        if isinstance(event.note, int) and not event.legato:
            self.noted[v] = self.player.call
        self.cur_ins[v] = ins

    def enter(self, scope, _v, *args):
        """Pushes what the keys about to apply are triggered by."""
        if scope == "instrument":
            ins = args[0]
            self.via.append(("note", id(ins) if ins.ctrl is not None else None))
        else:
            first, command = args
            self.via.append(("event" if command else "restart" if first else "program", None))

    def leave(self, _scope):
        """Pops the trigger."""
        self.via.pop()

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def bind(self, v, cell, slot, _command, fn, once, fresh):
        """Counts a binding's outcome and zeroes the phase it restarts; a plain integer's write
        is no binding."""
        if once and isinstance(getattr(fn, "node", None), ast.Constant):
            return
        mod, trigger = self._mods(v, cell)[slot], self._trigger()
        if not (fresh or once) and mod is not None and mod[0] is fn and not mod[5]:
            self.binds[cell, trigger, "same modulator, phase kept"] += 1
            return
        new = mod is None or mod[0] is not fn
        how = "fresh" if fresh else "once" if once else "new" if new else "rebound"
        self.binds[cell, trigger, how] += 1
        self._zero(v, f"{self.player.song.slots[cell][slot]}.{cell}", f"bind {trigger} {how}")

    def stop(self, v, cell, value, ends):
        """Counts the resets and zeroes the phases they reset."""
        if value != RESET:
            return
        trigger, mods = self._trigger(), self._mods(v, cell)
        for k in ends:
            self.binds[cell, trigger, "reset command" if mods[k] else "reset of nothing"] += 1
            if mods[k] is not None:
                self._zero(v, mods[k][3], f"reset {trigger}")

    def rewind(self, v, name):
        """Zeroes the phase of a slot on its reset event."""
        self._zero(v, name, "slot reset event")

    def row(self, v, slot):
        """Marks the program in the slot as having played a row."""
        if (v, slot) in self.live:
            self.live[v, slot][2] = True

    def start(self, v, slot, prog):
        """Counts a program start, whether it replaced one unplayed, and what a restart's
        control program was."""
        kind, t = self.player.song.kinds[slot], self.player.call
        trigger, ins = self.via[-1] if self.via else ("init", None)
        self.starts[KINDS[kind], trigger] += 1
        old = self.live.get((v, slot))
        if old and old[1] == t and not old[2] and old[0][0] != "init":
            self.replaced[KINDS[kind], old[0][0], trigger] += 1
            if old[0][1] is not None and kind == CONTROL:
                self.ins_starts[old[0][1]]["replaced"] += 1
        if ins is not None and kind == CONTROL:
            self.ins_starts[ins]["starts"] += 1
        self.live[v, slot] = [(trigger, ins), t, False]
        at = self.noted.get(v) == t
        if trigger == "restart" and kind == CONTROL:
            cur = self.cur_ins.get(v)
            has = cur is not None and cur.ctrl is not None
            self.restart_ctrl[
                "empty" if prog is None or not prog.rows else "program",
                "instrument has ctrl" if has else "no ctrl",
                "at note" if at else "before note",
            ] += 1
            if cur is not None and not has and at:
                self.moved.add((id(cur), id(prog)))
        if kind == CONTROL and prog is not None and prog.rows and REL0 in prog.rows[0][2]:
            self.rel0.setdefault(id(prog), Counter())[trigger, at] += 1


def programs(song) -> dict:
    """``{id: (kind, program)}`` of every program a song can start."""
    out, todo = {}, []

    def add(kind, prog):
        if prog is not None and id(prog) not in out:
            out[id(prog)] = (kind, prog)
            todo.append(prog)

    def ops(items):
        for key, value in items:
            if key == "run" and value is not None:
                add(*value)

    phrases = (
        e for row in song.timeline for entry in row if entry[0] == "phrase" for e in entry[1]
    )
    for e in [*phrases, *song.init]:
        ops(e.ops)
        if e.ins is not None:
            for kind, prog in ((CONTROL, e.ins.ctrl), (PULSE, e.ins.pw), (FILTER, e.ins.filter)):
                add(kind, prog)
            for row in e.ins.restart or ():
                ops(row[0][2] if isinstance(row[0], tuple) else ())
    while todo:
        for _, _, items in todo.pop().rows:
            ops(items)
    return out


def static(song) -> Counter:
    """Per kind, the song's programs, those empty and those ending in an empty row; the control
    programs whose first row is ``note:+0``."""
    stat = Counter()
    for kind, prog in programs(song).values():
        name = KINDS[kind]
        stat[f"{name} programs"] += 1
        if not prog.rows:
            stat[f"{name} empty"] += 1
        elif prog.loop is None and not prog.rows[-1][2] and len(prog.rows) > 1:
            stat[f"{name} trailing empty row"] += 1
        if kind == CONTROL and prog.rows and REL0 in prog.rows[0][2]:
            stat["ctrl first row note:+0"] += 1
    return stat


def flat(counts: Counter) -> dict:
    """``counts`` keyed by its tuple keys joined with ``/``."""
    return {"/".join(k): n for k, n in counts.items()}


def measure(path: Path, ticks: int) -> dict:
    """One document's static program counts and its traced program starts over ``ticks``."""
    meta = path.parent.parent / "meta" / f"{path.stem}.json"
    limit = (
        min(ticks, json.loads(meta.read_text()).get("ticks") or ticks) if meta.exists() else ticks
    )
    song = load(path.read_text(encoding="utf-8"))
    tracer = Tracer()
    player = tracer.player = Player(song, tracer)
    while player.tick < limit and player.step() is not None:
        pass
    ins = Counter()
    for c in tracer.ins_starts.values():
        ins["instruments with ctrl started at notes"] += 1
        ins["… every start replaced"] += c["replaced"] == c["starts"]
        ins["… some starts replaced"] += 0 < c["replaced"] < c["starts"]
    rel0 = Counter()
    for c in tracer.rel0.values():
        rel0["started"] += 1
        rel0["only at notes (note:+0 does nothing)"] += all(at for _, at in c)
        rel0.update(
            f"starts {trigger} {'at' if at else 'off'} note" for trigger, at in c.elements()
        )
    combos = Counter(
        (name.rsplit(".", 1)[1], " + ".join(sorted(how)))
        for (_, name, _), how in tracer.zeroed.items()
    )
    return {
        "doc": path.stem,
        "restart_ctrl": flat(tracer.restart_ctrl),
        "restart_movable": {"instrument and program pairs": len(tracer.moved)},
        "binds": flat(tracer.binds),
        "zeroed": flat(combos),
        "ticks": player.tick,
        "static": dict(static(song)),
        "starts": flat(tracer.starts),
        "replaced": flat(tracer.replaced),
        "instruments": dict(ins),
        "rel0": dict(rel0),
    }


def timed(path: Path, ticks: int, timeout: float) -> dict:
    """:func:`measure` within ``timeout`` seconds."""
    with deadline(timeout):
        return measure(path, ticks)


def documents(paths: list) -> list:
    """The documents of ``paths``, a ``.txt`` one a whitespace-separated list of them."""
    return [Path(d) for p in paths for d in (p.read_text().split() if p.suffix == ".txt" else [p])]


def summary(recs: list) -> str:
    """Each key's counts summed over the measured documents, most first, and the commonest
    errors."""
    ok = [r for r in recs if "error" not in r]
    lines = [
        f"{len(ok)} documents, {len(recs) - len(ok)} failed, {sum(r['ticks'] for r in ok)} ticks"
    ]
    for key in KEYS:
        total = Counter()
        for r in ok:
            total.update(r[key])
        lines += [key, *(f"  {n:10d}  {k}" for k, n in total.most_common())]
    errors = Counter(r["error"][:80] for r in recs if "error" in r)
    return "\n".join([*lines, f"errors {errors.most_common(5)}"])


def main(argv=None) -> int:
    """Parse the command line, measure the documents in parallel and print the summed counts."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("documents", nargs="+", type=Path, help="documents, or .txt lists of them")
    ap.add_argument("--ticks", type=int, default=6000, help="most ticks played per document")
    ap.add_argument("--timeout", type=float, default=120, help="seconds per document")
    ap.add_argument("--jobs", type=int, default=max(1, len(os.sched_getaffinity(0)) - 2))
    ap.add_argument("--out", type=Path, default=Path("triggers.json"))
    args = ap.parse_args(argv)
    paths = documents(args.documents)
    tasks = [(p, args.ticks, args.timeout) for p in paths]
    recs = []
    for i, rec, err in tqdm(imap(timed, tasks, args.jobs), total=len(tasks), unit="doc"):
        recs.append(rec or {"doc": paths[i].stem, "error": repr(err)})
    print(summary(recs))
    args.out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
