"""Resuming a candidate's proof from the source song's baseline: it gives the full replay's
verdict, and the baseline notes each datum no later than the tick a change to it is heard."""

import copy

from test_optimise import A, I1, I2_HEARD, LATE, doc, ins_line, row

from unitracker import optimise
from unitracker.format import songio
from unitracker.player.checkpoint import Baseline
from unitracker.player.vm import SongPlayer

I3 = ins_line("i3", f"ad: 9, {LATE}, ctrl: [{A}, end]")
EXPRS = "      x:\n        x1: (v + 32) % 65536\n        x2: (v + 16 + 16) % 65536\n"
LATE_DOC = doc(
    f"{I1}\n{I2_HEARD}\n{I3}",
    "[C4.i1!freq:x1*16, D4.i2*2, E4.i3!freq:x2*2, F4.i1!freq:x3*2]",
    EXPRS + "        x3: (v + 33) % 65536\n",
    steps=22,
    extra=row("p3", "[C4.i2*2, D4*20]"),
)


def candidates(opt, names):
    for name in names:
        for edit in optimise.PASSES[name](opt.base):
            ir = optimise.EDITS[edit[0]](opt.base, *edit[1])
            if ir is not None:
                yield edit, songio.loads(songio.dumps(ir))


def test_a_resumed_proof_gives_the_full_replays_verdict():
    opt = optimise.Optimiser(songio.loads(LATE_DOC), workers=1)
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
    ir = songio.loads(LATE_DOC)
    base = Baseline(SongPlayer(ir))
    reads = dict(base.reads)
    tables = {item: tick for item, tick in reads.items() if item[0] == "T"}
    assert tables and tables == first_table_reads(ir, len(base.rows))
    played = states(ir, len(base.rows))
    kinds = set()
    for item, tick in base.reads:
        changed = perturbed(ir, item)
        if changed is None or not tick:
            continue
        kinds.add(item[0])
        assert states(changed, tick) == played[:tick], item
        resumed = base.resume(SongPlayer(changed))
        assert resumed is None or resumed[0] <= tick, item
    assert kinds == {"F", "T", "P", "X"}
