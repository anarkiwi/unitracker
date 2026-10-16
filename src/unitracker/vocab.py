"""The shared vocabulary: every key, spec parameter and command name a song may use.

Songs from every player draw from these sets only (docs/format.md documents each).
"""

from unitracker.spec import AT_OPTIONS, PARAMS

# Register targets: a value sets the register's lane, a spec starts an accumulator on it.
TARGETS = ("ctrl", "ad", "sr", "pw", "freq", "bend", "cut", "resfilt", "modevol")
REGISTER_PARTS = ("vol", "filtmode", "ctrlxor", "cutbias", "filtvoice")
TIMING = ("wait", "hold", "note", "notebase")
PROGRAMS = ("ctrlprog", "pwprog", "filtprog", "layer1", "layer2", "layer1wait", "layer2wait")
INSTRUMENT = ("delay", "gateoff", "keepgate", "nohr", "firstctrl", "firstgate")
ROW_KEYS = frozenset(TARGETS + REGISTER_PARTS + TIMING + PROGRAMS + INSTRUMENT)
SPEC_PARAMS = frozenset(PARAMS)
PHASE_KEYS = frozenset(("step", "carry_from"))
AT_KEYS = frozenset(AT_OPTIONS)
SPEC_COMMANDS = ("freq",)
COMMANDS = frozenset(
    SPEC_COMMANDS
    + ("ad", "sr", "ctrl", "cut", "resfilt", "vol", "tempo", "vtempo", "funk")
    + ("ctrlprog", "pwprog", "filtprog", "layer2")
)
DEFINITIONS = frozenset(
    ("ins", "ctrlprog", "pwprog", "filtprog", "prog", "voiceinit", "globalinit", "spec", "phrase")
)
