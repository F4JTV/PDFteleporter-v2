"""Quality presets and transfer estimates.

The preset values are fixed by the .psdi interoperability contract: a receiving
station running another implementation must be able to rebuild an archive that
was produced here, so the numbers below must not be changed casually.
"""

from __future__ import annotations

QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_ULTRA_LOW = "ultra_low"

QUALITY_ORDER = (QUALITY_ULTRA_LOW, QUALITY_LOW, QUALITY_MEDIUM, QUALITY_HIGH)

# Encoding modes, as the operator sees them. engine.MODE_* remain the
# identifiers written into archives and accepted on the command line.
MODE_LABELS = {
    "struct": "structuré",
    "image": "image de page",
}

# Operator-facing names. The constants above stay as identifiers: they travel
# through the CLI, so renaming a label must never change a command line.
QUALITY_LABELS = {
    QUALITY_ULTRA_LOW: "Très basse",
    QUALITY_LOW: "Basse",
    QUALITY_MEDIUM: "Moyenne",
    QUALITY_HIGH: "Haute",
}

# coord_precision is the number of decimals kept for geometry. Dropping to
# whole points removes a digit and a separator from every coordinate in the
# manifest, which is worth about 6% of the compressed size, at the cost of up
# to half a point of placement drift. That is irrelevant on the presets meant
# for 1200-baud packet and acceptable nowhere else, so the higher presets keep
# the tenth of a point that dense spreadsheet tables need.
QUALITY_PRESETS = {
    QUALITY_ULTRA_LOW: {
        "dpi": 72,
        "jpeg_quality": 20,
        "img_max_dim": 400,
        "map_max_dim": 300,
        "banner_max_h": 25,
        "lzma_preset": 6,
        "coord_precision": 0,
        "description": "Urgence (Packet 1200 bauds)",
    },
    QUALITY_LOW: {
        "dpi": 90,
        "jpeg_quality": 30,
        "img_max_dim": 600,
        "map_max_dim": 400,
        "banner_max_h": 30,
        "lzma_preset": 9,
        "coord_precision": 0,
        "description": "Qualité réduite (Packet 9600 / VARA HF lent)",
    },
    QUALITY_MEDIUM: {
        "dpi": 120,
        "jpeg_quality": 45,
        "img_max_dim": 800,
        "map_max_dim": 600,
        "banner_max_h": 40,
        "lzma_preset": 9,
        "coord_precision": 1,
        "description": "Qualité moyenne (VARA HF / FM)",
    },
    QUALITY_HIGH: {
        "dpi": 150,
        "jpeg_quality": 55,
        "img_max_dim": 900,
        "map_max_dim": 700,
        "banner_max_h": 50,
        "lzma_preset": 9,
        "coord_precision": 1,
        "description": "Haute qualité (VARA FM rapide)",
    },
}

# Nominal payload throughput per radio mode, in bits per second.
#
# The keys are stable identifiers, never display text. An earlier version used
# the French labels directly as keys, which meant rewording a label silently
# broke every lookup that referenced it.
MODE_VARA_HF = "vara_hf"
MODE_VARA_HF_TURBO = "vara_hf_turbo"
MODE_VARA_FM_NARROW = "vara_fm_narrow"
MODE_VARA_FM_WIDE = "vara_fm_wide"
MODE_PACKET_9600 = "packet_9600"
MODE_PACKET_1200 = "packet_1200"
MODE_ARDOP = "ardop"
MODE_WINLINK_HF = "winlink_hf"
MODE_LORA = "lora"

RADIO_BITRATES = {
    MODE_VARA_HF: 2400,
    MODE_VARA_HF_TURBO: 4800,
    MODE_VARA_FM_NARROW: 9600,
    MODE_VARA_FM_WIDE: 25000,
    MODE_PACKET_9600: 9600,
    MODE_PACKET_1200: 1200,
    MODE_ARDOP: 2000,
    MODE_WINLINK_HF: 3200,
    MODE_LORA: 1200,
}

# Operator-facing labels. Protocol names are left alone; only the descriptive
# parts are in French.
RADIO_LABELS = {
    MODE_VARA_HF: "VARA HF (2400 bps)",
    MODE_VARA_HF_TURBO: "VARA HF turbo (4800 bps)",
    MODE_VARA_FM_NARROW: "VARA FM étroit (9600 bps)",
    MODE_VARA_FM_WIDE: "VARA FM large (25000 bps)",
    MODE_PACKET_9600: "Packet 9600 bps",
    MODE_PACKET_1200: "Packet 1200 bps",
    MODE_ARDOP: "ARDOP (2000 bps)",
    MODE_WINLINK_HF: "Winlink VARA HF",
    MODE_LORA: "LoRa (~1200 bps)",
}

# Winlink Express refuses attachments above this size.
WINLINK_MAX_ATTACHMENT = 120 * 1024


def estimate_times(size_bytes: int) -> dict[str, float]:
    """Return the estimated on-air time in seconds for each radio mode.

    Real throughput never reaches the nominal bitrate, so a 70% efficiency
    factor is applied. The result is indicative, not a guarantee.
    """
    bits = size_bytes * 8
    return {
        mode: round(bits / (bitrate * 0.7), 1)
        for mode, bitrate in RADIO_BITRATES.items()
    }


def format_bytes(size: int) -> str:
    """Render a byte count for a French operator.

    Precision is kept where the operator actually needs it: an archive of
    3812 octets shown as "4 ko" hides the difference between fitting a Winlink
    attachment and not, so kilobytes carry a decimal until the value is large
    enough for the rounding to be irrelevant.
    """
    if size < 1024:
        return f"{size} o"
    if size < 100 * 1024:
        return _decimal(f"{size / 1024:.1f}") + " ko"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} ko"
    return _decimal(f"{size / (1024 * 1024):.1f}") + " Mo"


def _decimal(text: str) -> str:
    """Swap the decimal point for the comma French typography expects."""
    return text.replace(".", ",")


def format_percent(value: float) -> str:
    """Render a percentage with a French decimal comma."""
    return _decimal(f"{value:.1f}") + " %"


def format_count(value: int) -> str:
    """Group thousands with a narrow no-break space, as French typography asks."""
    return f"{value:,}".replace(",", "\u202f")


def format_duration(seconds: float) -> str:
    """Render a duration the way an operator reads it off a screen."""
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"
