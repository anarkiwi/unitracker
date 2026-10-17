"""Token counts of unitracker documents per section, under each tokenizer of the token study.

Usage: python tools/token_report.py SONG.yaml [...]   (needs tiktoken and tokenizers)
"""

import sys

from unitracker.analysis.tokenstudy import SECTIONS, sections, tokenizers


def report(path: str, name: str, split) -> str:
    """One line: the tokenizer, total tokens, and the header, definitions, phrases and plays."""
    with open(path, encoding="utf-8") as fh:
        counts = sections(fh.read(), split)
    parts = ", ".join(f"{s} {counts[s]}" for s in SECTIONS)
    return f"{path} ({name}): {counts['total']} tokens; {parts}"


def main(paths) -> int:
    """Print a report line per path and tokenizer."""
    for path in paths:
        for name, split in tokenizers().items():
            print(report(path, name, split))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
