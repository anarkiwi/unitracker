"""Note and duration spellings of phrases, each with its exact inverse, counted with the
token study's tokenizers.
"""

import copy
import re
import sys
from collections import Counter

from tqdm import tqdm

from unitracker.analysis.tokenstudy import (
    count_rows,
    delta_rows,
    phrase_defs,
    table_header,
    tokenizers,
)
from unitracker.format import songio
from unitracker.format.atoms import NOTE_NAMES, note_name, parse_atom
from unitracker.format.yamlstyle import Atom, Flow

SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10)}


_NOTE = re.compile(r"^[A-G]#?\d+")


def _with_note(atom: str, text: str) -> str:
    return _NOTE.sub(text, atom, count=1)


def _notes(atoms) -> list:
    return [parse_atom(a)["note"] for a in atoms if parse_atom(a)["kind"] == 1]


def _respell(doc: dict, spell) -> dict:
    for table, name in phrase_defs(doc):
        table[name] = Flow(Atom(a) for a in spell(list(table[name])))
    return doc


def relative(atoms: list) -> list:
    """Notes as intervals from the phrase's root, which a first atom ``=C4`` states."""
    notes = _notes(atoms)
    if not notes:
        return atoms
    root = notes[0]
    out = [f"={note_name(root)}"]
    for a in atoms:
        p = parse_atom(a)
        out.append(_with_note(a, f"{p['note'] - root:+d}") if p["kind"] == 1 else a)
    return out


def delta(atoms: list) -> list:
    """The first note by name, every later one as the interval from the note before it."""
    out, prev = [], None
    for a in atoms:
        p = parse_atom(a)
        if p["kind"] == 1:
            out.append(a if prev is None else _with_note(a, f"{p['note'] - prev:+d}"))
            prev = p["note"]
        else:
            out.append(a)
    return out


def degree_text(note: int, key: tuple) -> str:
    """A note as scale degree, sharp when outside the scale, and octave: ``3o4``, ``4#o2``."""
    tonic, scale = key[0], SCALES[key[1]]
    rel = note - tonic
    pc, octave = rel % 12, rel // 12
    sharp = pc not in scale
    return f"{scale.index(pc - sharp) + 1}{'#' if sharp else ''}o{octave}"


def degrees(atoms: list, key: tuple) -> list:
    """Notes as degrees of ``key`` (``(tonic, mode)``)."""
    out = []
    for a in atoms:
        p = parse_atom(a)
        out.append(_with_note(a, degree_text(p["note"], key)) if p["kind"] == 1 else a)
    return out


def best_key(doc: dict) -> tuple:
    """``(tonic, mode)`` whose scale holds the most of the document's notes, the lowest tonic
    and major first on a tie."""
    counts = Counter(n % 12 for t, k in phrase_defs(doc) for n in _notes(t[k]))
    keys = [(t, m) for t in range(12) for m in SCALES]
    return max(keys, key=lambda k: (sum(counts[(k[0] + d) % 12] for d in SCALES[k[1]]), -k[0]))


def expanded(atoms: list) -> list:
    """Every step written: ``C4*3`` as ``C4, -, -``."""
    out = []
    for a in atoms:
        steps = parse_atom(a)["steps"]
        out += [re.sub(r"\*\d+$", "", a)] + ["-"] * (steps - 1)
    return out


def slashed(atoms: list) -> list:
    """Durations as ``C4/3``."""
    return [re.sub(r"\*(\d+)$", r"/\1", a) for a in atoms]


_INVERSE_NOTE = re.compile(r"^([+-]\d+|\d#?o-?\d+)")


def _collapsed(atoms: list) -> list:
    """Empty steps folded into the event before them, as the canonical form writes them."""
    out, steps = [], []
    for a in atoms:
        if a == "-" and out:
            steps[-1] += 1
        else:
            out.append(a)
            steps.append(1)
    return [a if n == 1 else f"{a}*{n}" for a, n in zip(out, steps)]


def absolute(atoms: list, spelling: str, key: tuple | None = None) -> list:
    """The canonical atoms of a respelt phrase: each spelling's exact inverse."""
    if spelling == "slashed":
        return [re.sub(r"/(\d+)$", r"*\1", a) for a in atoms]
    if spelling == "expanded":
        return _collapsed(atoms)
    out, root, prev = [], None, None
    if spelling == "relative" and atoms and atoms[0].startswith("="):
        root, atoms = parse_atom(atoms[0][1:])["note"], atoms[1:]
    for a in atoms:
        match = _INVERSE_NOTE.match(a)
        if match is None or spelling not in ("relative", "delta", "degrees"):
            out.append(a)
            prev = parse_atom(a)["note"] if parse_atom(a)["kind"] == 1 else prev
            continue
        text = match[1]
        if spelling == "degrees":
            deg, octave = text.split("o")
            pc = SCALES[key[1]][int(deg.rstrip("#")) - 1] + deg.endswith("#")
            note = key[0] + 12 * int(octave) + pc
        else:
            note = (root if spelling == "relative" else prev) + int(text)
        out.append(note_name(note) + a[len(text) :])
        prev = note
    return out


def spellings(doc: dict) -> dict:
    """``{name: respell(doc)}`` of the note and duration spellings studied."""
    key = best_key(doc)

    def keyed(d):
        d["key"] = f"{NOTE_NAMES[key[0]]} {key[1]}"
        return _respell(d, lambda a: degrees(a, key))

    return {
        "absolute": lambda d: d,
        "relative": lambda d: _respell(d, relative),
        "degrees": keyed,
        "delta": lambda d: _respell(d, delta),
        "expanded": lambda d: _respell(d, expanded),
        "slashed": lambda d: _respell(d, slashed),
    }


def identical(doc: dict) -> int:
    """How many phrase definitions repeat an earlier one's atoms exactly."""
    seen, out = set(), 0
    for table, name in phrase_defs(doc):
        key = tuple(table[name])
        out += key in seen
        seen.add(key)
    return out


def shapes(doc: dict, width: int = 4) -> tuple[int, int]:
    """``(windows, in other keys)``: runs of ``width`` notes, and those whose interval shape
    also occurs from another first note, which absolute names spell as other tokens."""
    windows = []
    for table, name in phrase_defs(doc):
        notes = _notes(table[name])
        windows += [tuple(notes[i : i + width]) for i in range(len(notes) - width + 1)]
    starts = {}
    for w in windows:
        starts.setdefault(tuple(b - a for a, b in zip(w, w[1:])), set()).add(w[0])
    shape = [tuple(b - a for a, b in zip(w, w[1:])) for w in windows]
    return len(windows), sum(len(starts[s]) > 1 for s in shape)


def respelt(ir: dict) -> dict:
    """``{spelling: (text, written document)}`` of the note and duration spellings of ``ir``."""
    holder = {}
    songio.dumps(ir, respell=lambda d: holder.setdefault("doc", copy.deepcopy(d)))
    out = {}
    for name, fn in spellings(holder["doc"]).items():
        got = {}
        text = songio.dumps(ir, respell=lambda d, fn=fn, got=got: got.setdefault("doc", fn(d)))
        out[name] = (text, got["doc"])
    return out


SAMPLES = ("C#4", "A#1", "C10", "G#4.i7*3", "-!freq:x12*2", "+12", "-3", "3#o4", "C4/4", "&s12")


def boundaries(samples=SAMPLES) -> list:
    """Markdown rows: how each tokenizer splits each sample atom."""
    toks = tokenizers()
    out = table_header(["atom", *toks])
    for atom in samples:
        cells = [" ".join(f"`{p}`" for p in split(atom)) for split in toks.values()]
        out.append(f"| `{atom}` | " + " | ".join(cells) + " |")
    return out


def facts(ir: dict) -> list:
    """Markdown lines: the key, the shapes recurring in other keys, and identical phrases."""
    variants = respelt(ir)
    base = variants["absolute"][1]
    key = best_key(base)
    windows, other = shapes(base)
    same = ", ".join(f"{name} {identical(doc)}" for name, (_, doc) in variants.items())
    return [
        f"- key {NOTE_NAMES[key[0]]} {key[1]}; {windows} runs of four notes, {other} of whose"
        " interval shapes also start from another note",
        f"- phrase definitions identical to an earlier one: {same}",
    ]


def report(paths: list) -> str:
    """Markdown tables of the note and duration spellings over the documents at ``paths``."""
    songs = {p: songio.load(p) for p in paths}
    texts = {p: songio.dumps(ir) for p, ir in songs.items()}
    out = [*count_rows(paths, texts), "", "### token boundaries", "", *boundaries()]
    variants = {}
    for p, ir in tqdm(songs.items(), desc="spellings", unit="song"):
        out += ["", f"### facts: {p}", "", *facts(ir)]
        variants[p] = {k: v[0] for k, v in respelt(ir).items()}
    return "\n".join(out + delta_rows("spellings", texts, variants))


if __name__ == "__main__":
    print(report(sys.argv[1:]))
