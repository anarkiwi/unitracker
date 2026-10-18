"""Tally the written vocabulary of unitracker documents: events, commands, row keys, expressions.

    python tools/vocab_census.py SONG.yaml ... [--json OUT.json]

Prints, per playroutine, how many songs use each event form, command (by name and argument shape),
record type, row key, program ending and expression operator, with the total occurrences, so the
format's vocabulary can be read against a corpus of exports.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tqdm import tqdm

from unitracker.format.yamlstyle import parse

EVENT = re.compile(
    r"^(?P<note>[A-G]#?-?\d+|-|off|on)(?P<ins>\.i\d+)?(?P<cmds>(?:![^!*]+)*)(?P<rep>\*\d+)?$"
)
COMMAND = re.compile(r"!(?P<name>[a-z]+)(?::(?P<arg>[^!]*))?")
NAME = re.compile(r"^(i|c|pw|f|r|x|p)\d+$")
TOKENS = re.compile(r"min\(|max\(|abs\(|//|%|>=|<=|==|!=|>|<|\*\*|\*|[+-]|φ|\bv\b|\d+")
RECORDS = ("ins", "ctrl", "pw", "filter", "restart", "x", "vinit", "ginit", "phrase")


def arg_shape(arg) -> str:
    """The shape of a command argument: ``name:<prefix>``, ``int``, ``none``, ``a/b``, ``expr``."""
    if arg is None:
        return "bare"
    if arg == "none":
        return "none"
    if NAME.match(arg):
        return f"name:{NAME.match(arg).group(1)}"
    if re.fullmatch(r"-?\d+", arg):
        return "int"
    if re.fullmatch(r"-?\d+/-?\d+", arg):
        return "a/b"
    return "expr"


def value_shape(value) -> str:
    """How a program-valued field is written: ``ref``, ``inline``, ``expr``, ``int``, ``none``."""
    if isinstance(value, list):
        return "inline"
    if isinstance(value, (int, float)):
        return "int"
    if value == "none":
        return "none"
    if isinstance(value, str) and NAME.match(value):
        return "ref"
    return "expr"


class Census:
    """Counts keyed by (section, item) for one playroutine, plus which songs used each."""

    def __init__(self):
        self.count = Counter()
        self.songs = defaultdict(set)

    def add(self, song, section, item, n=1):
        """Record ``n`` occurrences of ``item`` in ``section`` for ``song``."""
        self.count[(section, item)] += n
        self.songs[(section, item)].add(song)

    def expression(self, song, section, text):
        """Tally an expression's operators and the names it uses."""
        for tok in set(TOKENS.findall(str(text))):
            if not tok.isdigit():
                self.add(song, f"{section} ops", tok)

    def row(self, song, rtype, row):
        """Tally one row's keys and the forms of its note, route and expression values."""
        for key, val in row.items():
            self.add(song, f"{rtype} row key", key)
            if key == "note":
                self.add(song, f"{rtype} note", "relative" if isinstance(val, int) else "absolute")
            elif key == "route" and isinstance(val, str):
                self.add(song, f"{rtype} route", "edit")
            elif key in ("pw", "cut", "freq") and isinstance(val, str) and val != "none":
                self.expression(song, f"{rtype} row {key}", val)

    def rows(self, song, rtype, program):
        """Tally one program's rows, keys and ending."""
        shape = value_shape(program)
        if shape != "inline":
            self.add(song, f"{rtype} program", shape)
            if shape == "expr":
                self.expression(song, f"{rtype} program", program)
            return
        self.add(song, f"{rtype} program", "rows")
        self.add(song, f"{rtype} rows", "row", sum(isinstance(r, dict) for r in program))
        for row in program:
            if row == "end":
                self.add(song, f"{rtype} program", "end")
            elif isinstance(row, dict) and set(row) == {"loop"}:
                self.add(song, f"{rtype} program", "loop")
            elif isinstance(row, dict):
                self.row(song, rtype, row)
            else:
                self.add(song, f"{rtype} row form", type(row).__name__)

    def instrument(self, song, ins):
        """Tally an instrument's keys and how each program field is written."""
        for key, val in ins.items():
            self.add(song, "ins key", key)
            if key in ("ctrl", "pw", "filter", "restart", "freq"):
                shape = value_shape(val)
                self.add(song, f"ins {key}", shape)
                if shape == "inline":
                    self.rows(song, key if key != "restart" else "restart", val)
                elif shape == "expr":
                    self.expression(song, f"ins {key}", val)

    def phrase(self, song, events):
        """Tally each event's note form, instrument naming, commands and repeat."""
        for atom in events:
            m = EVENT.match(str(atom))
            if not m:
                self.add(song, "event", "unparsed")
                continue
            note = m.group("note")
            self.add(song, "event note", note if note in ("-", "off", "on") else "pitch")
            self.add(song, "event", "names instrument" if m.group("ins") else "no instrument")
            if m.group("rep"):
                self.add(song, "event", "*steps")
            cmds = list(COMMAND.finditer(m.group("cmds") or ""))
            if len(cmds) > 1:
                self.add(song, "event", f"{len(cmds)} commands")
            if len(cmds) > 1:
                self.add(song, "command pair", "+".join("!" + c.group("name") for c in cmds))
            for cmd in cmds:
                name, arg = cmd.group("name"), cmd.group("arg")
                self.add(song, "command", f"!{name}:{arg_shape(arg)}")
                if name == "freq" and arg_shape(arg) == "expr":
                    self.expression(song, "command !freq", arg)

    def record(self, song, rtype, value):
        """Tally one defined record by its type."""
        if rtype == "ins":
            self.instrument(song, value)
        elif rtype == "phrase":
            self.phrase(song, value)
        elif rtype == "x":
            self.add(song, "x", "expression")
            self.expression(song, "x", value)
        elif rtype in ("vinit", "ginit"):
            for key in value:
                self.add(song, f"{rtype} key", key)
        else:
            self.rows(song, rtype, value)

    def document(self, song, doc):
        """Tally one document."""
        pr = doc.get("playroutine")
        self.add(song, "header", "playroutine ref" if isinstance(pr, dict) else "playroutine name")
        for key in doc:
            self.add(song, "document key", key)
        for key in doc.get("meta") or {}:
            self.add(song, "meta key", key)
        if "tempo" in doc:
            self.add(song, "tempo", "swing" if isinstance(doc["tempo"], list) else "ticks")
        for entry in doc.get("song") or []:
            for rtype, records in (entry.get("def") or {}).items():
                self.add(song, "record type", rtype, len(records))
                for value in records.values():
                    self.record(song, rtype, value)
            for play in entry.get("play") or []:
                form = "~" if play is None else "phrase" if NAME.match(str(play)) else "transposed"
                self.add(song, "play entry", form)


def report(name: str, census: Census, total: int) -> str:
    """One table per section: item, songs using it, occurrences."""
    lines = [f"\n## {name}: {total} songs"]
    by_section = defaultdict(list)
    for (section, item), n in census.count.items():
        by_section[section].append((item, len(census.songs[(section, item)]), n))
    for section in sorted(by_section):
        lines.append(f"\n{section}:")
        for item, songs, n in sorted(by_section[section], key=lambda t: (-t[1], -t[2], str(t[0]))):
            lines.append(f"  {str(item):28s} {songs:4d} songs {n:8d}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("songs", type=Path, nargs="+")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    censuses, totals = defaultdict(Census), Counter()
    for path in tqdm(args.songs, unit="song"):
        doc = parse(path.read_text(encoding="utf-8"))
        pr = doc.get("playroutine")
        backend = pr.get("ref", "embedded") if isinstance(pr, dict) else str(pr)
        totals[backend] += 1
        censuses[backend].document(path.name, doc)
    for backend in sorted(censuses):
        print(report(backend, censuses[backend], totals[backend]))
    if args.json:
        out = {
            b: {f"{s}|{i}": [n, len(c.songs[(s, i)])] for (s, i), n in c.count.items()}
            for b, c in censuses.items()
        }
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
