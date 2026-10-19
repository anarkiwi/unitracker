"""The canonical grammar: the generic document of every reference tune is in its language, and
the internal form, which names its playroutine, is not."""

import pytest
from unitracker_grammar import CanonicalError, parse

TUNES = ("metal_warrior", "crosseyed", "csillag")


@pytest.mark.parametrize("name", TUNES)
def test_the_generic_documents_are_canonical(references, name):
    ref = references[name]
    parse(ref.generic)
    assert "playroutine" not in ref.generic
    with pytest.raises(CanonicalError, match="line 2"):
        parse(ref.text)
