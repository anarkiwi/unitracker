"""The canonical SID write order and the register names."""

REG_COUNT = 25

CANONICAL_ORDER = (0x15, 0x16, 0x17, 0x18) + tuple(
    7 * voice + off for voice in range(3) for off in (2, 3, 6, 5, 0, 1, 4)
)
_RANK = {reg: i for i, reg in enumerate(CANONICAL_ORDER)}

# Each voice's registers, low to high frequency, then the shared filter's word and controls.
_VOICE = ("freq lo", "freq hi", "pw lo", "pw hi", "ctrl", "ad", "sr")
REGISTERS = (
    *[f"v{n} {name}" for n in range(1, 4) for name in _VOICE],
    "cut lo",
    "cut hi",
    "res/route",
    "mode/vol",
)
