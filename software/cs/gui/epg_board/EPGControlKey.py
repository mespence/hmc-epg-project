from enum import Enum, auto

class EPGControlKey(Enum):
    """
    Enum for all of the engineering settings currently in the EPG spec.
    Update if new settings are added to the spec.
    """
    INPUT_RESISTANCE = auto()
    PGA_1 = auto()
    PGA_2 = auto()
    SIGNAL_CHAIN_AMPLIFICATION = auto()
    SIGNAL_CHAIN_OFFSET = auto()
    DDS_AMPLIFICATION = auto()
    DDS_OFFSET = auto()
    DIGIPOT_CHANNEL_0 = auto()
    DIGIPOT_CHANNEL_1 = auto()
    DIGIPOT_CHANNEL_2 = auto()
    DIGIPOT_CHANNEL_3 = auto()
    EXCITATION_FREQUENCY = auto()