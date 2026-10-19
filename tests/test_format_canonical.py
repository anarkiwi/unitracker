"""The canonical grammar: every reference document is in its language."""

import pytest
from unitracker_grammar import CanonicalError, parse


def test_the_reference_documents_are_canonical(references):
    """Metal Warrior and Csillag parse; Crosseyed's fixed records are outside the grammar."""
    parse(references["metal_warrior"].text)
    parse(references["csillag"].text)
    with pytest.raises(CanonicalError, match="line 6"):
        parse(references["crosseyed"].text)
