"""What a Music Assembler lift needs of a tune before it lifts: the typed refusals, the core (the
song, the memory its subtune's init left and the layout the spec binding resolves) and the
refusals its layout shows."""

from dataclasses import dataclass

import pymusicassembler as ma
from pymusicassembler import constants as MA
from pymusicassembler import playspec
from pymusicassembler.model import InitState, Song
from pysidtracker import NTSC_CLOCK_HZ, PAL_CLOCK_HZ, parse_sid_header

VOICES = MA.VOICES
SID = 0xD400
JMP_ABS = 0x4C


class LiftError(ValueError):
    """A tune the lift cannot state exactly, or whose document differs from the reference."""


class Refusal(LiftError):
    """A construct of a tune the document cannot state; ``kind`` names its class."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Divergence(LiftError):
    """The document's grid differs from the reference player's."""


@dataclass(frozen=True)
class Core:
    """What a lift needs of a tune: the song, the memory its subtune's init left (with the bound
    spec and layout), the machine clock and the spec binder the reference plays."""

    song: Song
    state: InitState
    clock: int
    bind: object = playspec.bind

    @property
    def found(self) -> dict:
        """The layout the spec binding resolved."""
        return self.state.found

    @property
    def spec(self):
        """The bound spec."""
        return self.state.spec

    @property
    def subtune(self) -> int:
        """The 0-based subtune."""
        return self.state.subtune

    def cell(self, name: str, at: int = 0) -> int:
        """The byte of layout cell ``name`` (voice ``at``) after init."""
        return self.state.cell(name, at)


def _clock_of(song: Song) -> int:
    head = song.container_header
    ntsc = head[:4] in (b"PSID", b"RSID") and parse_sid_header(head).is_ntsc
    return NTSC_CLOCK_HZ if ntsc else PAL_CLOCK_HZ


def core_of_song(song: Song, clock: int | None = None) -> Core:
    """The core of ``song``, as parsed: the subtune its init state names."""
    return Core(song, song.init_state, clock or _clock_of(song))


def core_of_sid(raw: bytes, subtune: int = 0) -> Core:
    """The core of a SID's subtune."""
    return core_of_song(ma.parse(raw, subtune))


ALIASES = (
    ("ctrl", "gate_ctrl"),
    ("ctrl_mask", "gate_mask"),
    ("vib_freq_lo", "vib_out_lo"),
    ("vib_freq_hi", "vib_out_hi"),
    ("sweep_dec", "sweep_ctr"),
    ("sweep_ctr", "fc_ctr"),
    ("sweep_acc", "sweep_sta"),
    ("sweep_acc", "program_acc"),
    ("sweep_acc", "fc_acc"),
    ("trigger_owner", "filter_owner"),
    ("trigger_owner", "sweep_owner"),
    ("trigger_acc", "fc_acc_reset"),
    ("trigger_ctr", "fc_ctr_reset"),
    ("sweep_step", "fc_step"),
    ("transrep", "transrep_byte"),
    ("pw_substep", "pw_substep_cmp"),
)


def _arp_cells(found: dict) -> dict:
    """The cells the arpeggio code's operands name in a build that keeps them where the spec
    puts them."""
    return {
        ("arp_porta_dec", "arp_porta_inc"): found["porta_ctr"],
        ("arp_phase_rd", "arp_phase_inc"): found["arp_phase"],
        ("arp_substep_inc", "arp_substep_cmp", "arp_substep_clr"): found["arp_substep"],
        ("arp_down_src", "arp_down_dst", "arp_up_src", "arp_up_dst"): found["freq_lo_w"],
        ("arp_down_hi", "arp_up_hi"): found["freq_hi_w"],
        ("arp_down_step", "arp_up_step"): found["instr"] + MA.INSTR_ARP_STEP,
        ("arp_speed",): found["instr"] + MA.INSTR_ARP_SPEED,
    }


def _entered(core: Core) -> bool:
    """Whether the play address reaches the play entry through nothing but ``JMP abs``; the
    model carries no player code, so the jumps are read from the memory."""
    state, at, seen = core.state, core.song.play_addr, set()
    while state.window(at, 1)[0] == JMP_ABS and at not in seen:
        seen.add(at)
        at = int.from_bytes(state.window(at + 1, 2), "little")
    return at == core.found["entry"]


def _layout(core: Core) -> None:
    """Refuse the layouts whose cells the document's model does not keep apart or where the
    spec puts them."""
    found = core.found
    for one, other in ALIASES:
        if found[one] != found[other]:
            raise Refusal("layout", f"{one} apart from {other}")
    if found["arp_down_cmp"] or found["pulse_cmp_adc"]:
        raise Refusal("arpeggio code", "a comparing variant")
    for names, cell in _arp_cells(found).items():
        if any(core.cell(n) | core.cell(n, 1) << 8 != cell for n in names):
            raise Refusal("relocated arpeggio operands", names[0])
    if found["order_restart"] % 2:
        raise Refusal("orderlist", "restart inside an entry")


def refuse(core: Core) -> None:
    """Refuse what the document cannot state, before lifting."""
    found = core.found
    if core.song.play_addr == 0:
        raise Refusal("interrupt", "play address 0")
    if not _entered(core):
        raise Refusal("play routine", "the play address is not the play entry")
    if playspec.has_native(core.spec):
        raise Refusal("native", "the spec has native blocks")
    if len(playspec.entries(core.state.memory)) > 1:
        raise Refusal("multi-SID", "several play entries")
    if (found["sid"], found["fc_sid"], found["res_sta"]) != (SID, SID, SID + 0x17):
        raise Refusal("SID mirror", hex(found["sid"]))
    if tuple(found["output_voices"]) != (0, 1, 2):
        raise Refusal("voice dispatch", str(found["output_voices"]))
    if tuple(core.cell("voice_reg", v) for v in range(VOICES)) != (0, 7, 14):
        raise Refusal("voice dispatch", "voice registers")
    _layout(core)
    if core.cell("tempo") or any(core.cell("dur", v) for v in range(VOICES)):
        raise Refusal("init", "a first play call that reads no row")
