"""The four states, and how each labelled second maps to one.

Priority, first match wins: spin > fill > drain > wash. The heater label is not a
state: heating runs while the drum washes (or fills), and vibration can't tell it
apart (the full example's fit/diagnose_wash_heating.py), so those seconds take their drum/water state.
"""
import numpy as np

STATES = ["fill", "wash", "spin", "drain"]
CHANNELS = [f"{s}.{a}" for s in ("back", "side", "top") for a in "xyz"]
LABEL_COLUMNS = ["centrifuge_label", "water_label", "heating_label"]


def seconds_state(g):
    """One state per row of a 1 Hz label table."""
    water = g.water_label.to_numpy()
    s = np.full(len(g), "wash", dtype=object)
    s[(water == -1) | (water == 2)] = "drain"   # 2 = inlet and outlet at once (Stage 1a)
    s[water == 1] = "fill"
    s[g.centrifuge_label.to_numpy() == 1] = "spin"
    return s
