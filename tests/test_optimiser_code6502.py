"""The 6502 code optimiser: symbolic semantics against the player, saturation, codegen round
trips with z3 proofs, and canonical output for equivalent routines."""

import random
from types import SimpleNamespace

import pytest
import z3
from unitracker_grammar.code import printed, read

from unitracker.direct.gt2code import CODE, STATE
from unitracker.optimiser.code6502 import __main__ as cli
from unitracker.optimiser.code6502.concrete import Player, evaluate, sample
from unitracker.optimiser.code6502.groups import equivalent, groups, rename, renamings
from unitracker.optimiser.code6502.pipeline import Measure, check, optimise, prove
from unitracker.optimiser.code6502.semantics import SemanticsError, Shape, semantics
from unitracker.optimiser.code6502.superopt import Leaves
from unitracker.optimiser.code6502.terms import from_z3, parse, sexp, to_z3
from unitracker.optimiser.code6502.vector import Bank

CELLS = {name: cli.cell_state(text, STATE) for name, text in CODE.items()}

# Pairs written two ways; each pair's optimised texts must agree.
VARIANTS = {
    "note_a": "lda phi\nora phi+1\nbne done\nsta t\nlda nn\nand #127\nsta m\ntay\n"
    "lda F.lo,y\nsta v\nlda F.hi,y\nsta v+1\ndone:\nrts\n",
    "note_b": "lda phi+1\nbne out\nlda phi\nbne out\nlda nn\nand #127\ntax\nstx m\n"
    "lda F.hi,x\nsta v+1\nlda F.lo,x\nsta v\nlda #0\nsta t\nout:\nrts\n",
    "add_a": "lda #g\nbmi dn\nclc\nlda v\nadc #<s\nsta v\nlda v+1\nadc #>s\nsta v+1\nrts\n"
    "dn:\nsec\nlda v\nsbc #<s\nsta v\nlda v+1\nsbc #>s\nsta v+1\nrts\n",
    "add_b": "lda #g\nasl\nbcs dn\nlda #<s\nclc\nadc v\nsta v\nlda #>s\nadc v+1\nsta v+1\nrts\n"
    "dn:\nlda v\nsec\nsbc #<s\ntax\nlda v+1\nsbc #>s\nsta v+1\nstx v\nrts\n",
}
VARIANT_STATE = {"t": 1, "m": 1}


@pytest.mark.parametrize("name", sorted(CODE))
def test_semantics_match_player(name):
    statements = read(CODE[name])
    sem = semantics(statements, CELLS[name])
    assert check(statements, sem, 2000, seed=len(name)) > 1900


def test_loop_summarised_as_shift():
    sem = semantics(read(CODE["portan"]), CELLS["portan"])
    assert sem.loops == [("lsr", "phi", 16)]


def test_bound_parameter_unrolls():
    text = "ldx #k\nloop:\nlsr phi+1\nror phi\ndex\nbne loop\nlda phi\nsta v\nrts\n"
    sem = semantics(read(text), {}, {"k": 3})
    env = sample(sem.shape, random.Random(1))
    env["#k"] = 3
    assert evaluate(sem.outputs, env)["v"] & 0xFF == (env["phi"] >> 3) & 0xFF


def test_refused():
    with pytest.raises(SemanticsError):
        semantics(read("ldx nn\nlda v,x\nsta v\nrts\n"), {})
    with pytest.raises(SemanticsError):
        semantics(read("loop:\njmp loop\nrts\n"), {})


def test_terms_round_trip():
    sem = semantics(read(CODE["tporta"]), CELLS["tporta"])
    inputs = sem.shape.inputs()
    memo = {}
    for name, term in sem.outputs.items():
        back = to_z3(from_z3(term, memo), inputs)
        assert prove({name: term}, {name: back})
        names, lets = {}, []
        root = sexp(from_z3(term, memo), names, lets)
        assert lets and root.startswith("$t")
    assert parse('(Add (BV "x") (BC 3))') == ("Add", ("BV", "x"), ("BC", 3))


def test_bank_matches_concrete():
    sem = semantics(read(CODE["vib"]), CELLS["vib"])
    bank = Bank(sem.shape, count=64, seed=3)
    memo = {}
    terms = {k: from_z3(t, memo) for k, t in sem.outputs.items()}
    for k in range(64):
        env = {n: (list(a[k]) if a.ndim == 2 else int(a[k])) for n, a in bank.env.items()}
        want = evaluate(sem.outputs, env)
        got = {n: int(bank.evaluate(t, {})[k]) for n, t in terms.items()}
        assert got == want


@pytest.mark.parametrize("name", ["note", "snap1", "porta1", "vib", "tporta", "portan"])
def test_round_trip(name):
    result = optimise(name, CODE[name], CELLS[name], cases=500)
    assert result.proved and result.differential[0] == result.differential[1] > 400
    read(result.after)
    old, new = result.measures
    if name != "tporta":
        assert new.size <= old.size + 10


def test_measure():
    shape = Shape.of(read("ldx #3\nl:\ndex\nbne l\nrts\n"), {})
    measure = Measure.of(read("ldx #3\nl:\ndex\nbne l\nrts\n"), shape)
    assert (measure.instructions, measure.size) == (4, 6)
    assert measure.cycles > 255 * 5


def test_variants_converge():
    results = [optimise(n, t, VARIANT_STATE, cases=200) for n, t in VARIANTS.items()]
    found = {tuple(sorted(n for n, _ in members)): same for members, same in groups(results)}
    assert found == {("note_a", "note_b"): True, ("add_a", "add_b"): True}


def test_renaming_equivalence():
    a = semantics(read("lda t\nsta v\nlda #0\nsta v+1\nrts\n"), {"t": 1})
    b = semantics(read("lda q\nsta v\nlda #0\nsta v+1\nrts\n"), {"q": 1})
    assert list(renamings(b.shape, a.shape)) == [{"q": "t"}]
    assert equivalent(a, b) == {"q": "t"}
    c = semantics(read("lda q\nsta v+1\nrts\n"), {"q": 1})
    assert equivalent(a, c) is None
    assert rename("lda q\nadc #k\nlda T.lo,x\nrts\n", {"q": "t", "#k": "#j", "T": "U"}) == (
        "lda t\nadc #j\nlda U.lo,x\nrts\n"
    )


def test_player_wrapper():
    shape = Shape.of(read(CODE["note"]), CELLS["note"])
    env = sample(shape, random.Random(0))
    env["phi"] = 0
    out = Player(read(CODE["note"]), shape)(env)
    k = env["nn"] & 127
    assert out["v"] == env["F"][k] | env["F"][256 + k] << 8


def test_cli(tmp_path, capsys):
    module = tmp_path / "mod.py"
    module.write_text(
        "STATE = {'freq': {'t': 1, 'm': 1}}\n"
        f"CODE = {{'a': {VARIANTS['note_a']!r}, 'b': {VARIANTS['note_b']!r}}}\n",
        encoding="utf-8",
    )
    out = tmp_path / "table.md"
    args = ["--cases", "50", "--show", "-j", "2", "--superopt", "2", "--backend", "numpy"]
    assert cli.main([str(module), *args, "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| mod.a, mod.b | yes |" in text and "superopt:" in text
    assert "mod.a:" in capsys.readouterr().out


def test_z3_baseline_smaller_than_lifted():
    result = optimise("note", CODE["note"], CELLS["note"], cases=10)
    assert result.sizes["z3"] < result.sizes["lifted"]
    assert all(z3.is_expr(t) for t in vars(result.semantics)["outputs"].values())


def test_leaf_superoptimised():
    shape = Shape.of(read("lda t\nclc\nadc #1\nsta t\nrts\n"), {"t": 1})
    leaves = Leaves(shape, backend="numpy", max_length=2)
    machine = SimpleNamespace(reg={"a": None, "x": ("BC", 0), "y": None}, carry=None)
    better = leaves.improve(read("lda t\nclc\nadc #1\nsta t\nrts\n")[:-1], ["t"], machine)
    assert printed(better) == "inc t\n"
    assert leaves.improve(read("sta t\nrts\n")[:-1], ["t"], machine) is None
    found = leaves.searches[0][2]
    assert found.size == 2 and found.candidates > 0


def test_superopt_pipeline():
    text = "lda phi\nora phi+1\nbne out\nlda t\nclc\nadc #1\nsta t\nout:\nrts\n"
    result = optimise(
        "inc", text, {"t": 1}, cases=50, superopt={"backend": "numpy", "max_length": 2}
    )
    assert result.proved and "inc t" in result.after and result.searches
