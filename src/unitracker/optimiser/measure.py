"""A document's size and the record of a rewrite of it."""

import functools
from dataclasses import dataclass, field

from unitracker_grammar.document import Document, printed

ENCODING = "cl100k_base"


@functools.cache
def _encoding():
    import tiktoken  # pylint: disable=import-outside-toplevel

    return tiktoken.get_encoding(ENCODING)


def size(doc: Document | str) -> int:
    """A document's size: its canonical text's token count under :data:`ENCODING`."""
    return len(_encoding().encode(doc if isinstance(doc, str) else printed(doc)))


@dataclass
class Rewrite:
    """A rewritten document, its text, the sizes before and after, and what was kept and
    refused."""

    doc: Document
    text: str
    before: int
    after: int
    kept: list = field(default_factory=list)
    refused: list = field(default_factory=list)
