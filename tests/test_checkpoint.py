"""Resuming a candidate's proof from the source song's baseline: it gives the full replay's
verdict, and the baseline notes each datum no later than the tick a change to it is heard."""

import copy

from test_optimise import A, DRIFT, FREQ, LATE, ev, ins, line, song

from unitracker import optimise
from unitracker.player.checkpoint import Baseline
from unitracker.player.vm import SongPlayer

LATE_DOC = song(
    [ev("C4", command=[FREQ, 2], instrument=1), *[{"command": [FREQ, 2]}] * 15]
    + line("D4", 2, [2])
    + [ev("E4", command=[FREQ, 3], instrument=3), {"command": [FREQ, 3]}]
    + [ev("F4", command=[FREQ, 4], instrument=1), {"command": [FREQ, 4]}],
    {1: ins(), 2: ins(ad=10, ctrl=2), 3: ins(ctrl=3, freq=1)},
    ([A], [A], [A]),
    (LATE, DRIFT, "(v + 16 + 16) % 65536", "(v + 33) % 65536"),
    steps=22,
    more={"p3": line("C4", 2, [2]) + line("D4", 20)},
)


def candidates(opt, names):
    for name in names:
        for edit in optimise.PASSES[name](opt.base):
            ir = optimise.EDITS[edit[0]](opt.base, *edit[1])
            if ir is not None:
                yield edit, ir


def test_a_resumed_proof_gives_the_full_replays_verdict():
    opt = optimise.Optimiser(LATE_DOC, workers=1)
    assert opt.ref.baseline is not None
    full = optimise.Trace(opt.source)
    resumed = set()
    for edit, ir in candidates(opt, ("instruments", "expressions")):
        verdict = optimise.mismatch(full, ir)
        assert optimise.mismatch(opt.ref, ir) == verdict, edit
        if opt.ref.baseline.resume(SongPlayer(ir)) is not None:
            resumed.add(verdict is None)
    assert resumed == {True, False}


def first_table_reads(ir: dict, ticks: int) -> dict:
    player, seen = SongPlayer(ir, record_reads=True), {}
    for tick in range(ticks):
        player.play_frame()
        for name, indices in player.table_reads.items():
            for index in indices:
                seen.setdefault(("T", name, index), tick)
    return seen


def perturbed(ir: dict, item: tuple) -> dict | None:
    """``ir`` with the datum ``item`` changed, or None for a kind not changed here."""
    out, (kind, name, key) = copy.deepcopy(ir), item
    if kind == "F":
        fields = out["records"][name[0]][key]
        fields[name[1]] = int(fields.get(name[1], 0)) + 1
    elif kind == "T":
        out["tables"][name][key] += 1
    elif kind == "P":
        play = out["tracks"][name]["plays"][key]
        play["transpose"] = play.get("transpose", 0) + 1
    elif kind == "X" and key in out["exprs"]:
        out["exprs"][key] = f"({out['exprs'][key]} + 1) % 65536"
    else:
        return None
    return out


def states(ir: dict, ticks: int) -> list:
    player = SongPlayer(ir)
    return [(player.play_frame(), player.state())[1] for _ in range(ticks)]


def test_the_baseline_notes_each_read_when_the_full_play_first_reads_it():
    ir = LATE_DOC
    base = Baseline(SongPlayer(ir))
    reads = dict(base.reads)
    tables = {item: tick for item, tick in reads.items() if item[0] == "T"}
    assert tables and tables == first_table_reads(ir, len(base.rows))
    played = states(ir, len(base.rows))
    kinds = set()
    for item, tick in base.reads:
        if not tick or (changed := perturbed(ir, item)) is None:
            continue
        kinds.add(item[0])
        assert states(changed, tick) == played[:tick], item
        resumed = base.resume(SongPlayer(changed))
        assert resumed is None or resumed[0] <= tick, item
    assert kinds == {"F", "T", "P", "X"}
