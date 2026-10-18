"""The YAML style of a written document: block structure, flow leaves, plain atoms."""

import re
from collections.abc import Hashable

import yaml


class Flow(list):
    """A list written in flow style."""


class FlowMap(dict):
    """A mapping written in flow style."""


class Atom(str):
    """A plain scalar written as is: event atoms, names, entries."""


class Dumper(yaml.SafeDumper):
    """Block structure, flow leaves, and atoms as unquoted plain scalars."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

    def choose_scalar_style(self):
        if self.event.style == "A":
            return ""
        return super().choose_scalar_style()


class Decimal(float):
    """A decimal number that keeps its text: a version's digits after the point are a number
    of their own (1.10 follows 1.9)."""

    text = ""


def _decimal(loader, node):
    out = Decimal(loader.construct_yaml_float(node))
    out.text = str(node.value)
    return out


class Signed(int):
    """An integer written with its sign (``+0``, ``-3``): a relative value where one is; ``text``
    is its spelling."""

    text = ""


class Quoted(str):
    """A scalar written in quotes; ``style`` is the quote, and ``flow`` whether it is in a flow
    collection."""

    style = "'"
    flow = True


_INTEGER = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$")
_BOOLS = {"true": True, "false": False}
_ATOMS = ("off", "on")


def _integer(_, node):
    if not _INTEGER.match(node.value):
        raise yaml.constructor.ConstructorError(
            None, None, f"{node.value!r} is not a decimal integer", node.start_mark
        )
    if node.value[0] not in "+-":
        return int(node.value)
    out = Signed(node.value)
    out.text = node.value
    return out


def _string(loader, node):
    value = loader.construct_yaml_str(node)
    if node.style not in ("'", '"'):
        return value
    out = Quoted(value)
    out.style = node.style
    return out


def _boolean(_, node):
    """``true`` and ``false`` as booleans, ``off`` and ``on`` as the atoms of those names; YAML's
    other spellings of a boolean are refused."""
    if node.value in _ATOMS:
        return node.value
    if node.value not in _BOOLS:
        raise yaml.constructor.ConstructorError(
            None, None, f"{node.value!r} is not a value of the format", node.start_mark
        )
    return _BOOLS[node.value]


class Loader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """Safe loading with each decimal number's text kept, integers only in decimal (a signed
    one :class:`Signed`), ``off`` and ``on`` as atoms, booleans spelled ``true`` and ``false``,
    a quoted scalar :class:`Quoted`, and each key of a mapping once."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=deep) for k, _ in node.value]
        for k, key in enumerate(keys):
            if isinstance(key, Hashable) and key in keys[:k]:
                raise yaml.constructor.ConstructorError(
                    None, None, f"key {key!r} appears twice", node.value[k][0].start_mark
                )
        out = super().construct_mapping(node, deep)
        _flows([*out, *out.values()], node.flow_style)
        return out

    def construct_sequence(self, node, deep=False):
        out = super().construct_sequence(node, deep)
        _flows(out, node.flow_style)
        return out


def _flows(values, flow: bool) -> None:
    """Mark each :class:`Quoted` of ``values`` as in a flow collection or not."""
    for value in values:
        if isinstance(value, Quoted):
            value.flow = bool(flow)


Loader.add_constructor("tag:yaml.org,2002:float", _decimal)
Loader.add_constructor("tag:yaml.org,2002:int", _integer)
Loader.add_constructor("tag:yaml.org,2002:bool", _boolean)
Loader.add_constructor("tag:yaml.org,2002:str", _string)
for _dumper in (yaml.SafeDumper, Dumper):
    _dumper.add_representer(
        Signed, lambda d, x: d.represent_scalar("tag:yaml.org,2002:int", str(int(x)))
    )
    _dumper.add_representer(Quoted, lambda d, x: d.represent_str(str(x)))
    _dumper.add_representer(
        Decimal, lambda d, x: d.represent_scalar("tag:yaml.org,2002:float", x.text)
    )


def parse(text: str):
    """A document's YAML, its version as written."""
    return yaml.load(text, Loader=Loader)


_SIGNED = re.compile(r"^[+-]\d+$")
_DECIMAL = re.compile(r"^\d+\.\d+$")


def _atom(dumper, data):
    if data in ("off", "on"):
        tag = "tag:yaml.org,2002:bool"
    elif _SIGNED.match(data):
        tag = "tag:yaml.org,2002:int"
    elif _DECIMAL.match(data):
        tag = "tag:yaml.org,2002:float"
    else:
        tag = "tag:yaml.org,2002:str"
    return dumper.represent_scalar(tag, str(data), style="A")


Dumper.add_representer(Flow, lambda d, x: d.represent_sequence("tag:yaml.org,2002:seq", x, True))
Dumper.add_representer(FlowMap, lambda d, x: d.represent_mapping("tag:yaml.org,2002:map", x, True))
Dumper.add_representer(Atom, _atom)
Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "~"))
