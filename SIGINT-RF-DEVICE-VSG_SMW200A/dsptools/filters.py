"""
DSP utility functions for I/Q waveform generation.

  normalize(iq)                  — scale complex samples to |iq| <= 1
  rrc_filter(beta, sps, span)    — root raised cosine FIR
  gaussian_pulse(bt, sps, span)  — Gaussian pulse for GMSK/GFSK
  random_bits(n, seed)           — reproducible binary sequence
  shape_symbols(symbols, sps, beta) — upsample + RRC filter
"""

from __future__ import annotations

from typing import Any

import numpy as np


def normalize(iq: Any) -> Any:
    """Scale complex samples so max |iq| <= 1 (RsSmw auto_scale does fine gain)."""
    m = np.max(np.abs(iq))
    return iq / m if m > 0 else iq


def rrc_filter(beta: float, sps: int, span: int = 10) -> Any:
    """
    Root raised cosine FIR.

    beta  — roll-off factor (0..1)
    sps   — samples per symbol
    span  — filter length in symbols
    """
    n = np.arange(-span * sps / 2, span * sps / 2 + 1)
    t = n / sps
    h = np.zeros_like(t, dtype=float)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-9:
            h[i] = 1.0 - beta + 4 * beta / np.pi
        elif beta > 0 and abs(abs(ti) - 1 / (4 * beta)) < 1e-9:
            h[i] = (beta / np.sqrt(2)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta))
            )
        else:
            num = np.sin(np.pi * ti * (1 - beta)) + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta))
            den = np.pi * ti * (1 - (4 * beta * ti) ** 2)
            h[i] = num / den
    return h / np.sqrt(np.sum(h ** 2))


def gaussian_pulse(bt: float, sps: int, span: int = 4) -> Any:
    """
    Gaussian pulse shape for GMSK / GFSK.

    bt   — bandwidth–symbol-period product (e.g. 0.3 for GSM, 0.4 for AIS)
    sps  — samples per symbol
    span — filter length in symbols
    """
    n = np.arange(-span * sps / 2, span * sps / 2 + 1)
    t = n / sps
    alpha = np.sqrt(np.log(2) / 2) / bt
    g = (1 / (np.sqrt(2 * np.pi) * alpha)) * np.exp(-(t ** 2) / (2 * alpha ** 2))
    return g / np.sum(g)


def random_bits(n: int, seed: int = 0) -> Any:
    """Return n reproducible random bits (0/1) using a seeded Generator."""
    return np.random.default_rng(seed).integers(0, 2, n)


def shape_symbols(symbols: Any, sps: int, beta: float = 0.35) -> Any:
    """
    Upsample complex symbols (zero-insert) and apply RRC pulse shaping.

    symbols — complex baseband symbol array
    sps     — samples per symbol
    beta    — RRC roll-off factor
    """
    up = np.zeros(len(symbols) * sps, dtype=complex)
    up[::sps] = symbols
    h = rrc_filter(beta, sps)
    return np.convolve(up, h, mode="same")
