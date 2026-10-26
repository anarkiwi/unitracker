"""SID-Wizard's per-tick frequency modulators as 6502 code templates.

The freq cell's state is the player's: ``z`` FREQLO/FREQHI, which WF pitch rows load and
VIBSLIDE steps in place, ``dc`` VIDELCNT, ``vc`` VIBRACNT, ``p`` VIBFREQU,
``fm`` FREQMOD, ``k`` SLIDEVIB, ``dd`` DETUNER, ``cm``/``ca``/``cv`` WRPITCH's carry; the pw
cell's ``pl`` PWLOGHO/PWHIGHO, ``pk`` PKBDTRK, mirrored in the freq cell's ``ml``/``mk`` for
WRPULS's carry ``cs`` (``mt`` its scratch), ``vt``/``vi``/``vq``/``vr``/``vg`` the instrument's
vibrato a portamento's end restores; the cut cell's ``ch``, ``cl``, ``ck`` CTFH, CTFL and its
keyboard track.
"""

import re

STATE = {
    "freq": {
        "z": 2,
        "dc": 1,
        "vc": 1,
        "p": 1,
        "fm": 2,
        "k": 1,
        "dd": 1,
        "cm": 1,
        "ca": 1,
        "cv": 1,
        "ml": 2,
        "mk": 1,
        "mt": 2,
        "cs": 1,
        "vt": 1,
        "vi": 1,
        "vq": 1,
        "vr": 1,
        "vg": 2,
        "mp": 1,
        "mf": 1,
        "mc": 1,
    },
    "pw": {"pl": 2, "pk": 1, "pc": 1},
    "cut": {"ch": 1, "cl": 1, "ck": 1, "fc": 1},
}

_FIRST = "lda phi\nora phi+1\nbne step\n"
_ADD = "clc\nlda z\nadc fm\nsta z\nlda z+1\nadc fm+1\nsta z+1\nrts\n"
_SUB = "sec\nlda z\nsbc fm\nsta z\nlda z+1\nsbc fm+1\nsta z+1\nrts\n"
_INCVIBR = "clc\nlda fm\nadc dc\nsta fm\nlda fm+1\nadc #0\nsta fm+1\njmp dov\n"
_STEP = (
    "step:\nlda k\ncmp #131\nbeq pm\ncmp #255\nbeq {np}\ncmp #129\nbeq up\ncmp #130\nbeq dn\n"
    "tax\nbmi done\nbne nv\n{inc}nv:\nlda dc\nbmi dov\ndec dc\ndone:\nrts\ndov:\nlda vc\n"
    "bne cnt\nlda p\nsta vc\ncnt:\ndec vc\nlda vc\nasl\ncmp p\nbcc up\ndn:\n"
    + _SUB
    + "up:\n"
    + _ADD
)
# PORTAME: d = FREQTBL[DPITCH] - z - c (17 bits, c the PORTAVIBRA borrow); within FREQMOD of
# the target PORTEND ({e}), else z steps FREQMOD + 1 toward it. TARGETN (tgt) loads the target.
_PORTAME = (
    "tgt:\nldx ne\ninx\nlda F.lo-1,x\nsta z\nlda F.hi-1,x\nsta z+1\nrts\npm:\nldx ne\ninx\n{c}\n"
    "lda F.lo-1,x\nsbc z\ntay\nlda F.hi-1,x\nsbc z+1\nbcc pdn\ncpy fm\nsbc fm+1\nbcc {e}\nsec\n"
    "lda z\nadc fm\nsta z\nlda z+1\nadc fm+1\nsta z+1\nrts\npdn:\npha\ntya\nclc\nadc fm\npla\n"
    "adc fm+1\nbcs {e}\nclc\nlda z\nsbc fm\nsta z\nlda z+1\nsbc fm+1\nsta z+1\nrts\n{end}"
)
_CALC = (
    "lda #r\nbeq az\nlda ne\nclc\nadc #r\ncmp #204\nbcc ay\nlda #203\nay:\ncmp #107\n"
    "bcs ar\nsec\nsbc #11\nbcc az\ntax\nlda F.hi-1,x\nsta fm\nlda #0\nsta fm+1\njmp ad\naz:\n"
    "lda #0\nsta fm\nsta fm+1\njmp ad\nar:\nsec\nsbc #107\ntax\nlda F.lo-1,x\nsta fm\n"
    "lda F.hi-1,x\nsta fm+1\nad:\n"
)
_CONST = "lda #<g\nsta fm\nlda #>g\nsta fm+1\n"
_WRPITCH = (
    "go:\nlda cm\nbeq c0\ncmp #1\nbeq c1\ncmp #3\nbeq c3\nlda ne\nclc\nadc #1\nclc\nadc ca\n"
    "jmp sum\nc3:\nlda cv\ncmp #255\nbne cvo\nlda ca\ncmp #2\nbne cvo\nlda cs\ncvo:\nlsr\njmp sum\n"
    "c1:\nsec\njmp sum\n"
    "c0:\nclc\nsum:\nlda z\nadc dd\nsta v\nlda z+1\nadc #0\nsta v+1\nrts\n"
)


# EXPTABH[idx] (11 bytes of zeros, then FREQTBH) for idx = (DPITCH & $7F) + the track amount.
_TRACK = (
    "lda ne\nclc\nadc #1\nand #127\nclc\nadc {k}\nphp\ntax\nlda #0\n"
    "cpx #11\nbcc e0\nlda F.hi-12,x\ne0:\n"
)
_WRPULS = (
    "go:\nlda pk\nbeq plain\n"
    + _TRACK.format(k="pk")
    + "sta v+1\nlda #0\ncpx #12\nbcc e1\nlda F.hi-13,x\ne1:\nsta v\nplp\nlda v+1\nsbc v\n"
    "adc pl+1\nsta v+1\nlda pl\nsta v\nrts\nplain:\nlda pl\nsta v\nlda pl+1\nsta v+1\nrts\n"
)
_CUTOFF = (
    "go:\nlda ck\nbeq fp\n"
    + _TRACK.format(k="ck")
    + "plp\nadc ch\nadc #0\njmp fo\nfp:\nlda ch\nfo:\nsta v\nlda #0\nsta v+1\nasl v\nrol v+1\n"
    "asl v\nrol v+1\nasl v\nrol v+1\nlda cl\nand #7\nora v\nsta v\nrts\n"
)


def _mirrored(text: str) -> str:
    """A pulse template on the freq cell's copy: its WRPULS keeps only the carry, in ``cs``."""
    text = re.sub(r"\bpk\b", "mk", re.sub(r"\bpl\b", "ml", text)).replace("sta v\n", "sta mt\n")
    text = text.replace("sta v+1\n", "sta mt+1\n").replace("sbc v\n", "sbc mt\n")
    text = text.replace("lda v+1\n", "lda mt+1\n")
    text = text.replace(
        "adc ml+1\nsta mt+1\nlda ml\nsta mt\nrts\n", "adc ml+1\nlda #0\nrol\nsta cs\nrts\n"
    )
    return text.replace(
        "plain:\nlda ml\nsta mt\nlda ml+1\nsta mt+1\nrts\n", "plain:\nlda #0\nsta cs\nrts\n"
    )


def paced(text: str, speed: int, counter: str) -> str:
    """Sweep template ``text`` stepping on every ``speed``-th firing from its first, counted in
    ``counter``: a table a multispeed tune walks on its PLAYER calls only."""
    gate = f"lda phi\nora phi+1\nbne gp\nlda #1\nsta {counter}\ngp:\ndec {counter}\nbne go\n"
    return gate + f"lda #{speed}\nsta {counter}\n" + text


def unnoted(text: str) -> str:
    """Template ``text`` on a voice yet to play a note: DPITCH 0, the note -1."""
    return re.sub(r"\bne\b", "#255", text)


def mirror_templates(tracked: bool = True, speed: int = 1) -> dict:
    """The pulse templates mirrored on the freq cell, for WRPITCH's carry on skipped WF ticks:
    WRPULS's where the pulse tracks the keyboard, else SETPWID's (``ml`` PWLOGHO)."""
    if not tracked:
        return {
            # SETPULW: PWLOGHO, the carry ``c`` (a jump's ``cmp PWTPOS``).
            "kst": "lda phi\nora phi+1\nbne go\nlda #w\nsta ml\nlda #c\nsta cs\ngo:\nrts\n",
            # PWSWEEP's step: the carry of PWLOGHO's add.
            "ksw": "clc\nlda ml\nadc #d\nsta ml\nlda #0\nrol\nsta cs\nrts\n",
            # PWADVAN, a jump to a sweep, the table's end: a carry ``c``.
            "kcs": "lda #c\nsta cs\nrts\n",
        } | (
            {
                "ksws": paced(
                    "clc\nlda ml\nadc #d\nsta ml\nlda #0\nrol\nsta cs\ngo:\nrts\n", speed, "mc"
                )
            }
            if speed > 1
            else {}
        )
    pulse = track_templates(False, speed)
    names = ("pst", "psw", "pid", "pwr") + (("psws",) if speed > 1 else ())
    return {"m" + name[1:]: re.sub(r"\bpc\b", "mc", _mirrored(pulse[name])) for name in names}


def track_templates(finefiltsweep: bool, speed: int = 1) -> dict:
    """The pulse and filter tables' templates where the tune tracks the keyboard: each binding
    keeps the player's own value and writes it with the note's track term (a multispeed tune's
    sweeps also paced, ``psws``/``fsws``)."""
    out = _tracked(finefiltsweep)
    if speed > 1:
        out |= {"psws": paced(out["psw"], speed, "pc"), "fsws": paced(out["fsw"], speed, "fc")}
    return out


def _tracked(finefiltsweep: bool) -> dict:
    return {
        # SETPULW: the width and PKBDTRK; then WRPULS.
        "pst": "lda phi\nora phi+1\nbne go\nlda #<w\nsta pl\nlda #>w\nsta pl+1\nlda #j\nsta pk\n"
        + _WRPULS,
        # PWSWEEP's step.
        "psw": "clc\nlda pl\nadc #<d\nsta pl\nlda pl+1\nadc #>d\nsta pl+1\n" + _WRPULS,
        # PWADVAN past a sweep: PKBDTRK.
        "pid": "lda phi\nora phi+1\nbne go\nlda #j\nsta pk\n" + _WRPULS,
        # WRPULS of the width the table rests on.
        "pwr": _WRPULS,
        # SETFILT: CTFH (CTFL cleared with FINEFILTSWEEP) and the track amount.
        "fst": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\n"
        + ("lda #0\nsta cl\n" if finefiltsweep else "")
        + "lda #j\nsta ck\n"
        + _CUTOFF,
        # FILTPRG's sweep step: the 11-bit cutoff (FINEFILTSWEEP) or CTFH.
        "fsw": (
            "lda ch\nsta v\nlda #0\nsta v+1\nasl v\nrol v+1\nasl v\nrol v+1\nasl v\nrol v+1\n"
            "lda cl\nand #7\nora v\nclc\nadc #<d\nsta v\nlda v+1\nadc #>d\nand #7\nsta v+1\n"
            "lda v\nand #7\nsta cl\nlsr v+1\nror v\nlsr v+1\nror v\nlsr v+1\nror v\nlda v\n"
            "sta ch\n"
            if finefiltsweep
            else "clc\nlda ch\nadc #d\nsta ch\n"
        )
        + _CUTOFF,
        # The cutoff and track the previous filter voice's walk left on a handover tick.
        "fho": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\nlda #l\nsta cl\nlda #j\nsta ck\n"
        + _CUTOFF,
        # FILTPRG past a sweep: the track amount.
        "fid": "lda phi\nora phi+1\nbne go\nlda #j\nsta ck\n" + _CUTOFF,
        # BIGFX0F: CTFH, under the filter walk's binding.
        "fhi": "lda phi\nora phi+1\nbne go\nlda #c\nsta ch\n" + _CUTOFF,
        # $D416/$D415 of the cutoff the table rests on.
        "frs": _CUTOFF,
    }


def templates(
    calcvibrato: bool, vibratotypes: bool, portavibra: bool = False, speed: int = 1
) -> dict:
    """The templates' texts for a build: its SETFMOD, whether SLIDEVIB 0 is INCVIBR, and
    whether a portamento ends in the instrument's vibrato; a multispeed tune's VIBSLIDE steps
    on the PLAYER calls only, the phase ``mp`` its ``ph`` counts."""
    amp = _CALC if calcvibrato else _CONST
    keep, end = "", ""
    if portavibra:
        held = _CALC.replace("#r", "vr") if calcvibrato else _CONST.replace("#<g", "vg")
        held = re.sub(r"\ba([zyrd])\b", r"p\1", held.replace("#>g", "vg+1"))
        end = "pe:\nlda vt\nsta k\nlda vi\nsta vc\nlda vq\nsta p\n" + held + "jmp tgt\n"
        stored = "lda #r\nsta vr\n" if calcvibrato else "lda #<g\nsta vg\nlda #>g\nsta vg+1\n"
        keep = "lda #c\nsta vt\nlda #i\nsta vi\nlda #q\nsta vq\n" + stored
    step = _STEP.format(inc=_INCVIBR if vibratotypes else "", np="tgt" if portavibra else "pm")
    if speed > 1:
        step = step.replace("step:\n", f"step:\nlda mp\ncmp #{speed - 1}\nbeq run\nrts\nrun:\n", 1)
    step += _PORTAME.format(
        c="clc" if portavibra else "sec", e="pe" if portavibra else "tgt", end=end
    )
    out = {
        # ABSPTCH/RELPTCH: a WF pitch row (phi 0) loads FREQLO/FREQHI of the note masked to 7 bits.
        "tone": "lda phi\nora phi+1\nbne go\nlda nn\nclc\nadc #1\nand #127\ntax\nlda F.lo-1,x\n"
        "sta z\nlda F.hi-1,x\nsta z+1\ngo:\nlda z\nsta v\nlda z+1\nsta v+1\nrts\n",
        # WRPITCH: a WF row (phi 0) sets the detune (f 1) and the carry, mode c (3 skipped), b.
        "det": "lda phi\nora phi+1\nbne go\nlda #f\nbeq keep\nlda #d\nsta dd\nkeep:\nlda #c\n"
        "sta cm\nlda #b\nsta ca\n" + _WRPITCH,
        # SETDETU: a FX's detune, the carry left as the WF walk made it.
        "dfx": "lda phi\nora phi+1\nbne go\nlda #d\nsta dd\n" + _WRPITCH,
        # STRTSND: SLIDEVIB, SETVIB0, SETVIBR, no detune; h 1 or 2 writes FREQHI.
        "snd": "lda phi\nora phi+1\nbeq init\nrts\ninit:\nsta dd\nlda #c\nsta k\n"
        "lda #255\nsta cv\n"
        "lda #d\nsta dc\nlda #i\nsta vc\nlda #q\nsta p\n"
        + keep
        + amp
        + "lda #h\nbeq nh\nldx ne\ninx\ncmp #1\nbeq hw\nldx #t\ninx\nhw:\nlda F.hi-1,x\n"
        "sta v+1\nnh:\nrts\n",
        # HARDRST's carry on a new note's skipped WF ticks.
        "hrc": "lda phi\nora phi+1\nbne kept\nlda #c\nsta cv\nkept:\nrts\n",
        # VIBSLIDE from the next tick: a tick the player's hard restart skips it.
        "wt": _FIRST + "rts\n" + step,
        # VIBSLIDE: the vibrato or slide SLIDEVIB selects.
        "stp": step,
        # The PLAYER call's phase: mp is speed - 1 on it, counting down on the MULPLY calls
        # (the first PLAYER call the speed-th).
        "ph": f"lda phi\nora phi+1\nbne st\nlda #{speed - 1}\nsta mp\nst:\n"
        f"dec mp\nbpl pd\nlda #{speed - 1}\nsta mp\nlda #0\nsta mf\npd:\nrts\n",
        # FORCVIB and SETVIBR, then step.
        "vfx": _FIRST + "lda #c\nsta k\nlda #i\nsta vc\nlda #q\nsta p\n" + amp + step,
        # SMALFX9: VIBFREQU.
        "vfq": _FIRST + "lda #q\nsta p\n" + step,
        # BIGFX16: SLIDEVIB.
        "kty": _FIRST + "lda #c\nsta k\n" + step,
        # BIGFX01/02 (m 129, 130), BIGFX03 without a note (131), NPORTAM (255).
        "sld": _FIRST + "lda #m\nsta k\n" + amp + step,
        # BIGFX03 on a note, or a legato's BIGFX01/02 (m): no step on the tick.
        "por": _FIRST + "lda #m\nsta k\n" + amp + "rts\n" + step,
        # Legato $3F: as BIGFX03 with FREQMODH $7F.
        "leg": _FIRST + "lda #131\nsta k\nlda #127\nsta fm+1\nrts\n" + step,
        # A note after NPORTAM: PORTAME at NPORTAM's FREQMOD.
        "npn": _FIRST + "lda #131\nsta k\nrts\n" + step,
    }
    if speed > 1:
        # STRTSND's ARPSCNT keeps MULCNTP off the voice to the next PLAYER call: no write.
        out["snd"] = out["snd"].replace("init:\nsta dd\n", "init:\nsta dd\nlda #1\nsta mf\n", 1)
        for name in ("tone", "det", "dfx"):
            out[name] = "lda mf\nbeq on\nrts\non:\n" + out[name]
    return out
