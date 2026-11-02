"""Ducking: lower music while a voice is present, computed here and not by an ffmpeg filter.

`sidechaincompress` is stateful and consumes its two inputs in chunks whose sizes depend on
decoder-thread timing, so the same graph gave different bytes on repeated runs. The gain curve
is instead computed from the decoded voice (one deterministic decode, plain numpy) and applied
with `amultiply`, which is stateless: chunking cannot change a product of samples.

The curve is smooth (its fastest move takes tens of milliseconds), so it is handed to ffmpeg as
a 1 kHz control track, 1/48 the size of a full-rate one, and resampled up there.

The model is a gate-driven ducker: while the voice is louder than a threshold the music is
reduced by `duck_db`; the reduction eases in with a look-ahead (so the first syllable is not
covered by loud music) and eases out slowly (so the music does not pump between words).
"""

from __future__ import annotations

import numpy as np

ANALYSIS_RATE = 8000  # the voice is analyzed at this rate; speech energy is below 4 kHz
CONTROL_RATE = 1000  # the gain track handed to ffmpeg
TAIL_S = 0.05  # the control track runs slightly past the voice so a rounding never cuts music
HOP_S = 0.01  # gain is decided every 10 ms and interpolated between
THRESHOLD_DB = -45.0  # voice louder than this counts as speaking
ATTACK_S = 0.06
RELEASE_S = 0.45


def duck_curve(
    voice: np.ndarray,
    *,
    duck_db: float,
    rate: int = ANALYSIS_RATE,
    threshold_db: float = THRESHOLD_DB,
    attack_s: float = ATTACK_S,
    release_s: float = RELEASE_S,
) -> np.ndarray:
    """Linear gain (1.0 = unchanged) at CONTROL_RATE for music that should sit under `voice`
    (mono samples at `rate`). It covers the whole voice plus a short tail."""
    n = len(voice)
    if n == 0:
        return np.ones(0, dtype=np.float32)
    hop = max(1, round(HOP_S * rate))
    blocks = max(1, -(-n // hop))
    pad = np.zeros(blocks * hop, dtype=np.float64)
    pad[:n] = voice
    rms = np.sqrt(np.mean(pad.reshape(blocks, hop) ** 2, axis=1))
    level_db = 20 * np.log10(np.maximum(rms, 1e-9))
    speaking = level_db > threshold_db

    ahead = max(1, round(attack_s / HOP_S))  # start ducking before the voice does
    target = np.zeros(blocks)
    idx = np.flatnonzero(speaking)
    for shift in range(ahead + 1):
        target[np.clip(idx - shift, 0, blocks - 1)] = -duck_db

    down = 1 - np.exp(-HOP_S / attack_s)
    up = 1 - np.exp(-HOP_S / release_s)
    gain_db = np.empty(blocks)
    g = 0.0
    for k in range(blocks):
        g += (target[k] - g) * (down if target[k] < g else up)
        gain_db[k] = g
    out = -(-n * CONTROL_RATE // rate) + round(TAIL_S * CONTROL_RATE)
    centers = (np.arange(blocks) + 0.5) * hop / rate
    gains = np.interp(np.arange(out) / CONTROL_RATE, centers, 10 ** (gain_db / 20))
    return gains.astype(np.float32)
