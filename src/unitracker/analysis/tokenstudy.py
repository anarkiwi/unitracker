"""Token studies of unitracker documents: per-section counts and context-efficiency mock-ups,
each measured with several real tokenizers (see docs/optimise.md).

A mock-up rewrites the written structure of a document (``songio.dumps(..., respell=)``); none
of them is a format the reader accepts.
"""

import copy
import functools
import re
import sys

from tqdm import tqdm

from unitracker.format import songio
from unitracker.format.atoms import parse_atom
from unitracker.format.yamlstyle import Atom, Flow, FlowMap
from unitracker.optimise import one_line

SECTIONS = ("header", "definitions", "phrases", "plays", "structure")


LLAMA = "hf-internal-testing/llama-tokenizer"


TIKTOKEN = ("cl100k_base", "o200k_base")


WAVES = {1: "tri", 2: "saw", 4: "pul", 8: "noi"}


_INT = re.compile(r"(?<![\w.])\d+(?![\w.])")


_TRI = re.compile(r"abs\(\((φ(?: [+-] \d+)?)\) % (\d+) - (\d+)\)")


_CLAMP = re.compile(r"min\((\d+), max\(0, (φ(?: [+-] \d+)?)\)\)")


@functools.cache
def llama_file():
    """The Llama 2 ``tokenizer.json`` in the local Hugging Face cache, or None; nothing is fetched
    (``huggingface-cli download hf-internal-testing/llama-tokenizer tokenizer.json`` caches it)."""
    from huggingface_hub import try_to_load_from_cache  # pylint: disable=import-outside-toplevel

    found = try_to_load_from_cache(LLAMA, "tokenizer.json")
    return found if isinstance(found, str) else None


def tokenizers() -> dict:
    """``{name: text -> token pieces}``: GPT-4's and GPT-4o's BPE, and Llama 2's SentencePiece
    when :func:`llama_file` finds it cached."""
    import tiktoken  # pylint: disable=import-outside-toplevel
    from tokenizers import Tokenizer  # pylint: disable=import-outside-toplevel

    out = {}
    for name in TIKTOKEN:
        enc = tiktoken.get_encoding(name)
        out[name] = lambda t, enc=enc: [
            enc.decode_single_token_bytes(x).decode("utf-8", "replace") for x in enc.encode(t)
        ]
    path = llama_file()
    if path is not None:
        llama = Tokenizer.from_file(path)
        out["llama2"] = lambda t: llama.encode(t, add_special_tokens=False).tokens
    return out


def _kind(line: str, current: str, types: int) -> str:
    stripped, indent = line.strip(), len(line) - len(line.lstrip())
    if stripped.startswith(("play:", "- play:")):
        return "plays"
    if indent == types and stripped.endswith(":"):
        return "phrases" if stripped == "phrase:" else "definitions"
    return "structure" if indent < types else current


def sections(text: str, split) -> dict:
    """Tokens per section (``SECTIONS``, each line counted on its own) and ``total``; record
    types sit at indent 6 in the timeline's rows, or at 2 under a top-level ``defs``."""
    head, sep, song = text.partition("\nsong:")
    counts = dict.fromkeys(SECTIONS, 0)
    counts["header"] = len(split(head + sep))
    current, types = "structure", 6
    for line in song.splitlines(keepends=True):
        types = 2 if line.startswith("defs:") else types
        current = _kind(line, current, types)
        counts[current] += len(split(line))
    counts["total"] = len(split(text))
    return counts


def phrase_defs(doc: dict):
    """``(definitions, name)`` of every phrase a written document defines, in order."""
    for row in doc["song"]:
        phrases = (row.get("def") or {}).get("phrase") or {}
        for name in list(phrases):
            yield phrases, name


def _leaves(value, path=()):
    """``(container, key)`` of every plain-string leaf (expressions; names are atoms)."""
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for k, v in list(items):
        if isinstance(v, (dict, list)):
            yield from _leaves(v, (*path, k))
        elif isinstance(v, str) and not isinstance(v, Atom):
            yield value, k


def _definitions(doc: dict):
    for row in doc["song"]:
        for tname, entries in (row.get("def") or {}).items():
            if tname != "phrase":
                yield from _leaves(entries)


def _template(skeleton: str, texts: list) -> tuple[str, list]:
    """``(body, varying)``: the family's text with each integer that varies as a parameter
    ``a``, ``b``, ..., and the positions of those integers."""
    ints = [[int(m) for m in _INT.findall(t)] for t in texts]
    vary = [i for i in range(len(ints[0])) if len({row[i] for row in ints}) > 1]
    parts = skeleton.split("#")
    body = parts[0]
    for i, (lit, part) in enumerate(zip(ints[0], parts[1:])):
        body += (chr(ord("a") + vary.index(i)) if i in vary else str(lit)) + part
    return body, vary


def forms(doc: dict) -> dict:
    """Expressions alike up to their integers written as uses of one parameterised form."""
    groups = {}
    for box, k in _definitions(doc):
        groups.setdefault(_INT.sub("#", box[k]), []).append((box, k))
    table = {}
    for skeleton, uses in groups.items():
        texts = sorted({box[k] for box, k in uses})
        if len(texts) < 2:
            continue
        body, vary = _template(skeleton, texts)
        name = f"f{len(table) + 1}"
        table[Atom(name)] = f"({', '.join(chr(ord('a') + i) for i in range(len(vary)))}) {body}"
        for box, k in uses:
            got = [int(m) for m in _INT.findall(box[k])]
            box[k] = f"{name}({', '.join(str(got[i]) for i in vary)})"
    return _withheader(doc, "forms", FlowMap(table)) if table else doc


def primitives(doc: dict) -> dict:
    """``tri(x, p)`` for ``abs(x % p - p/2)`` and ``clamp(x, 0, n)`` for ``min(n, max(0, x))``."""

    def tri(m):
        return f"tri({m[1]}, {m[2]})" if 2 * int(m[3]) == int(m[2]) else m[0]

    for box, k in list(_definitions(doc)):
        box[k] = _CLAMP.sub(r"clamp(\2, 0, \1)", _TRI.sub(tri, box[k]))
    return doc


def primitive_uses(doc: dict) -> dict:
    """How often each primitive's pattern occurs in the document's expressions."""
    texts = [box[k] for box, k in _definitions(doc)]
    return {
        "tri": sum(2 * int(m[3]) == int(m[2]) for t in texts for m in _TRI.finditer(t)),
        "clamp": sum(len(_CLAMP.findall(t)) for t in texts),
    }


def _withheader(doc: dict, key: str, value) -> dict:
    out = {}
    for k, v in doc.items():
        if k == "song":
            out[key] = value
        out[k] = v
    return out


def _instrument_free(atom: str) -> tuple:
    return (re.sub(r"\.i\d+", "", atom), re.findall(r"\.(i\d+)", atom))


def hoisted_instruments(doc: dict) -> dict:
    """Phrases equal up to a renaming of their instruments played as the first, the renaming
    in the play entry (``p1(i1=i4)``)."""
    seen, alias = {}, {}
    for table, name in phrase_defs(doc):
        parts = [_instrument_free(a) for a in table[name]]
        key = tuple(p[0] for p in parts)
        names = [n for p in parts for n in p[1]]
        if key in seen and len(seen[key][1]) == len(names):
            first, theirs = seen[key]
            pairs = sorted({(a, b) for a, b in zip(theirs, names) if a != b})
            if len({a for a, _ in pairs}) == len(pairs) == len({b for _, b in pairs}):
                alias[name] = f"{first}({', '.join(f'{a}={b}' for a, b in pairs)})"
                del table[name]
                continue
        seen.setdefault(key, (name, names))
    for row in doc["song"]:
        row["play"] = Flow(_entry_alias(e, alias) for e in row["play"])
    return doc


def _entry_alias(entry, alias: dict):
    if entry is None:
        return None
    name, shift = re.match(r"^(p\d+)(.*)$", str(entry)).groups()
    return Atom(alias[name] + shift) if name in alias else entry


def _runs(lists: list, gram: tuple, ref=None) -> int:
    """How many runs ``gram`` the atom lists hold apart; with ``ref``, each replaced by it."""
    hits = 0
    for atoms in lists:
        i = 0
        while i <= len(atoms) - len(gram):
            if tuple(atoms[i : i + len(gram)]) == gram:
                if ref is not None:
                    atoms[i : i + len(gram)] = [ref]
                hits += 1
                i += 1 if ref is not None else len(gram)
            else:
                i += 1
    return hits


def subphrases(doc: dict, width: int = 4) -> dict:
    """Runs of ``width`` atoms or more that recur apart, written once as ``s1`` and referenced
    in the phrases as ``&s1``."""
    named = list(phrase_defs(doc))
    lists = [list(t[n]) for t, n in named]
    subs = {}
    for size in range(max(map(len, lists), default=0) // 2, width - 1, -1):
        grams = {
            tuple(a[i : i + size])
            for a in lists
            for i in range(len(a) - size + 1)
            if not any(x.startswith("&") for x in a[i : i + size])
        }
        for gram in sorted(grams):
            if _runs(lists, gram) >= 2:
                _runs(lists, gram, f"&s{len(subs) + 1}")
                subs[Atom(f"s{len(subs) + 1}")] = Flow(Atom(x) for x in gram)
    for (table, name), atoms in zip(named, lists):
        table[name] = Flow(Atom(a) for a in atoms)
    if subs:
        doc["song"][0].setdefault("def", {})["sub"] = subs
    return doc


def rests(doc: dict, split) -> tuple[int, int]:
    """``(tokens of rest atoms, tokens of every atom)`` in the phrases."""
    atoms = [a for t, n in phrase_defs(doc) for a in t[n]]
    total = sum(len(split(a)) for a in atoms)
    return sum(len(split(a)) for a in atoms if parse_atom(a)["kind"] in (0, 2)), total


def grouped(doc: dict) -> dict:
    """Every definition in the first row."""
    merged = {}
    for row in doc["song"]:
        for tname, entries in (row.pop("def", None) or {}).items():
            merged.setdefault(tname, {}).update(entries)
    doc["song"][0] = {"def": merged, **doc["song"][0]}
    return doc


def _wave(value) -> str:
    """The first waveform an instrument's written definition sets."""
    for match in re.finditer(r"ctrl: (\d+)", one_line(value)):
        if int(match[1]) >> 4:
            return WAVES.get(int(match[1]) >> 4, "mix")
    return "none"


def _instruments(doc: dict) -> dict:
    out = {}
    for row in doc["song"]:
        for tname, entries in (row.get("def") or {}).items():
            if tname in ("ins", "sidtab"):
                out.update(entries)
    return out


def summaries(doc: dict) -> dict:
    """A one-line summary per instrument, from its keys, in the header."""
    about = {}
    for name, value in _instruments(doc).items():
        keys = list(value) if isinstance(value, dict) else ["program"]
        about[Atom(name)] = f"{_wave(value)} wave; {', '.join(map(str, keys))}"
    return _withheader(doc, "about", FlowMap(about))


def mnemonics(doc: dict, text: str) -> str:
    """Instrument names with their first waveform: ``i1`` as ``i1_pul``."""
    names = {str(n): f"{n}_{_wave(v)}" for n, v in _instruments(doc).items()}
    return re.sub(r"\b([it]\d+)\b", lambda m: names.get(m[1], m[1]), text)


def tempo(doc: dict, ir: dict) -> dict:
    """The tempo in beats per minute of four steps, beside ``ticks_per_step``."""
    hints = dict(doc.get("hints") or {})
    clock = ir.get("clock") or {}
    if "ticks_per_step" in hints and clock:
        rate = clock["clock_hz"] / clock["cycles_per_tick"]
        hints["bpm"] = round(60 * rate / (4 * hints["ticks_per_step"]))
    doc["hints"] = FlowMap(hints)
    return doc


def contents(doc: dict) -> dict:
    """Each voice's phrases in playing order, in the header."""
    voices = [[] for _ in doc["song"][0]["play"]]
    for row in doc["song"]:
        for v, entry in enumerate(row["play"]):
            if entry is not None:
                voices[v].append(Atom(entry))
    return _withheader(doc, "voices", Flow(Flow(v) for v in voices))


def plays_first(doc: dict) -> dict:
    """The play rows first, then every definition under ``defs``."""
    merged = {}
    for row in doc["song"]:
        for tname, entries in (row.pop("def", None) or {}).items():
            merged.setdefault(tname, {}).update(entries)
    doc["defs"] = merged
    return doc


def mockups(ir: dict) -> dict:
    """``{name: written text}`` of the context-efficiency mock-ups of song ``ir``."""
    out = {"baseline": songio.dumps(ir)}
    for name, fn in (
        ("forms", forms),
        ("primitives", primitives),
        ("hoisted instruments", hoisted_instruments),
        ("sub-phrases", subphrases),
        ("grouped definitions", grouped),
        ("summaries", summaries),
        ("tempo", lambda d: tempo(d, ir)),
        ("contents", contents),
        ("plays first", plays_first),
    ):
        out[name] = songio.dumps(ir, respell=fn)
    holder = {}
    text = songio.dumps(ir, respell=lambda d: holder.setdefault("doc", copy.deepcopy(d)))
    out["mnemonics"] = mnemonics(holder["doc"], text)
    return out


def table_header(cols: list) -> list:
    """A Markdown table's header rows."""
    return ["| " + " | ".join(cols) + " |", "|---" * len(cols) + "|"]


def facts(ir: dict) -> list:
    """Markdown lines: the rests' share of the phrases, and the expression primitives."""
    holder = {}
    songio.dumps(ir, respell=lambda d: holder.setdefault("doc", d))
    rest, total = rests(holder["doc"], tokenizers()["cl100k_base"])
    return [
        f"- rest and gate-off atoms: {rest} of the phrases' {total} atom tokens (cl100k_base)",
        f"- expression primitives: {primitive_uses(holder['doc'])}",
    ]


def count_rows(paths: list, texts: dict) -> list:
    """Markdown rows: each document's tokens per section under each tokenizer."""
    out = table_header(["document", "tokenizer", "total", *SECTIONS])
    for p in paths:
        for t, split in tokenizers().items():
            c = sections(texts[p], split)
            out.append(
                f"| {p} | {t} | {c['total']} | " + " | ".join(str(c[s]) for s in SECTIONS) + " |"
            )
    return out


def delta_rows(name: str, bases: dict, variants: dict) -> list:
    """Markdown rows: each variant's token deltas from its document's ``bases`` text, the total
    under every tokenizer and the sections under the first; ``variants`` is ``{doc: {name: text}}``.
    """
    toks = tokenizers()
    first = next(iter(toks))
    cols = ["variant", "document", *(f"total {t}" for t in toks), *SECTIONS]
    out = ["", f"### {name}", "", *table_header(cols)]
    labels = list(dict.fromkeys(k for v in variants.values() for k in v))
    for label in labels:
        for doc, texts in variants.items():
            got = {t: (sections(bases[doc], s), sections(texts[label], s)) for t, s in toks.items()}
            cells = [f"{o['total'] - b['total']:+d}" for b, o in got.values()]
            base, other = got[first]
            cells += [f"{other[s] - base[s]:+d}" for s in SECTIONS]
            out.append(f"| {label} | {doc} | " + " | ".join(cells) + " |")
    return out


def report(paths: list) -> str:
    """Markdown tables of the context-efficiency mock-ups over the documents at ``paths``."""
    songs = {p: songio.load(p) for p in paths}
    texts = {p: songio.dumps(ir) for p, ir in songs.items()}
    out = count_rows(paths, texts)
    variants = {}
    for p, ir in tqdm(songs.items(), desc="mock-ups", unit="song"):
        out += ["", f"### facts: {p}", "", *facts(ir)]
        variants[p] = mockups(ir)
    return "\n".join(out + delta_rows("mock-ups", texts, variants))


if __name__ == "__main__":
    print(report(sys.argv[1:]))
