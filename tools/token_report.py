"""Token counts of unitracker documents, split into header, sound design and phrases.

Usage: python tools/token_report.py SONG.yaml [...]   (needs tiktoken; cl100k_base proxy)
"""

import sys

import tiktoken
import yaml


def _kind(line: str, current: str) -> str:
    """The part of a v1.x song a line belongs to, given the part of the line before it."""
    stripped = line.strip()
    indent = len(line) - len(line.lstrip())
    if stripped.startswith("play:"):
        return "plays"
    if indent == 6 and stripped.endswith(":"):
        return "phrases" if stripped == "phrase:" else "definitions"
    if indent <= 4:
        return "structure"
    return current


def report(path: str, enc) -> str:
    """One line: total tokens, the header, and the song's definitions, phrases and plays."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    total = len(enc.encode(text))
    if yaml.safe_load(text).get("unitracker") not in (1, 1.1, 1.2, 1.3, 1.4, 1.5):
        return f"{path}: {total} tokens"
    head, _, song = text.partition("\nsong:")
    counts, current = {}, "structure"
    for line in song.splitlines(keepends=True):
        current = _kind(line, current)
        counts[current] = counts.get(current, 0) + len(enc.encode(line))
    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    return f"{path}: {total} tokens; header {len(enc.encode(head))}; song: {parts}"


def main(paths) -> int:
    """Print a report line per path."""
    enc = tiktoken.get_encoding("cl100k_base")
    for path in paths:
        print(report(path, enc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
