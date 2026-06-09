"""
I/Q signal generators for R&S SMW200A ARB waveforms.

Each function returns ``(complex_iq_array, sample_rate_hz)`` ready for
``smw.arb_files.create_waveform_file_from_samples()``.

All generators accept keyword-only defaults so that ``functools.partial``
can fix parameters (e.g. ``order=2`` for BPSK) and ``list_generator_param_specs``
can introspect the remaining editable parameters for the GUI.

RF SAFETY
---------
Several entries reference distress / safety frequencies (EPIRB 406 MHz,
ELT 121.5 MHz, maritime Ch16 / DSC Ch70, Mode S / ADS-B 1090 MHz).
Generate only in a shielded bench (cable + attenuator or screened chamber).
Never radiate outdoors.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .filters import gaussian_pulse, normalize, random_bits, shape_symbols


# ---------------------------------------------------------------------------
# Analog / narrowband
# ---------------------------------------------------------------------------

def gen_am_dsb(fs: float = 200e3, tone: float = 1e3, depth: float = 0.8, dur: float = 20e-3) -> tuple[Any, float]:
    """AM-DSB (A3E): ATC voice / VOR-style envelope on carrier; Q = 0."""
    t = np.arange(0, dur, 1 / fs)
    env = 1 + depth * np.sin(2 * np.pi * tone * t)
    return normalize(env.astype(complex)), fs


def gen_fm(fs: float = 200e3, tone: float = 1e3, dev: float = 5e3, dur: float = 20e-3) -> tuple[Any, float]:
    """Narrowband FM: maritime voice / LMR / NOAA / GMRS style."""
    t = np.arange(0, dur, 1 / fs)
    msg = np.sin(2 * np.pi * tone * t)
    phase = 2 * np.pi * dev * np.cumsum(msg) / fs
    return normalize(np.exp(1j * phase)), fs


# ---------------------------------------------------------------------------
# Digital narrowband
# ---------------------------------------------------------------------------

def gen_gmsk(fs: float = 240e3, rb: float = 9600, bt: float = 0.4, nbits: int = 512, seed: int = 1) -> tuple[Any, float]:
    """GMSK (AIS Ch87B/88B). h = 0.5. Replace bits with a proper AIS encoder for real frames."""
    sps = int(round(fs / rb))
    bits = random_bits(nbits, seed)
    nrz = np.repeat(2 * bits - 1, sps).astype(float)
    g = gaussian_pulse(bt, sps)
    freq = np.convolve(nrz, g, mode="same")
    phase = np.cumsum(freq) * (np.pi * 0.5 / sps)  # h = 0.5
    return normalize(np.exp(1j * phase)), fs


def gen_fsk(fs: float = 120e3, rs: float = 1200, devs: tuple = (2100.0, -1300.0), nsym: int = 256, seed: int = 2) -> tuple[Any, float]:
    """2-FSK / AFSK-style (DSC Ch70). devs = frequency deviation per symbol (Hz)."""
    sps = int(round(fs / rs))
    levels = np.array(devs)
    syms = np.random.default_rng(seed).integers(0, len(levels), nsym)
    f = np.repeat(levels[syms], sps).astype(float)
    phase = 2 * np.pi * np.cumsum(f) / fs
    return normalize(np.exp(1j * phase)), fs


def gen_4fsk(fs: float = 192e3, rs: float = 4800, dev: float = 1944, nsym: int = 256, seed: int = 3) -> tuple[Any, float]:
    """4-FSK (DMR / P25 C4FM / NXDN). Levels ±3·dev/3 and ±dev/3."""
    sps = int(round(fs / rs))
    levels = np.array([-dev, -dev / 3, dev / 3, dev])
    syms = np.random.default_rng(seed).integers(0, 4, nsym)
    f = np.repeat(levels[syms], sps).astype(float)
    phase = 2 * np.pi * np.cumsum(f) / fs
    return normalize(np.exp(1j * phase)), fs


def gen_pi4_dqpsk(fs: float = 720e3, rs: float = 18e3, nsym: int = 512, beta: float = 0.35, seed: int = 4) -> tuple[Any, float]:
    """pi/4-DQPSK (TETRA)."""
    sps = int(round(fs / rs))
    dibits = np.random.default_rng(seed).integers(0, 4, nsym)
    dphi = {0: np.pi / 4, 1: 3 * np.pi / 4, 2: -np.pi / 4, 3: -3 * np.pi / 4}
    phase = np.cumsum([dphi[int(d)] for d in dibits])
    syms = np.exp(1j * phase)
    return normalize(shape_symbols(syms, sps, beta)), fs


# ---------------------------------------------------------------------------
# Digital wideband
# ---------------------------------------------------------------------------

def gen_psk(fs: float = 2e6, rs: float = 250e3, order: int = 4, nsym: int = 1024, beta: float = 0.35, seed: int = 5) -> tuple[Any, float]:
    """Generic M-PSK with RRC shaping (BPSK order=2, QPSK order=4, 8PSK order=8)."""
    sps = int(round(fs / rs))
    m = np.random.default_rng(seed).integers(0, order, nsym)
    syms = np.exp(1j * (2 * np.pi * m / order))
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_qam(fs: float = 4e6, rs: float = 500e3, order: int = 16, nsym: int = 1024, beta: float = 0.25, seed: int = 6) -> tuple[Any, float]:
    """M-QAM with RRC shaping (payload downlinks, data feeds)."""
    sps = int(round(fs / rs))
    k = int(np.sqrt(order))
    lvl = np.arange(-(k - 1), k, 2)
    rng = np.random.default_rng(seed)
    syms = rng.choice(lvl, nsym) + 1j * rng.choice(lvl, nsym)
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_apsk16(fs: float = 4e6, rs: float = 500e3, nsym: int = 1024, beta: float = 0.2, seed: int = 7) -> tuple[Any, float]:
    """16-APSK with RRC shaping (DVB-S2 Ku/Ka). Rings 4+12, gamma = 2.85 (~3/4 code rate)."""
    sps = int(round(fs / rs))
    r1, r2 = 1.0, 2.85
    inner = r1 * np.exp(1j * (np.pi / 4 + np.pi / 2 * np.arange(4)))
    outer = r2 * np.exp(1j * (np.pi / 12 * (2 * np.arange(12))))
    const = np.concatenate([inner, outer])
    idx = np.random.default_rng(seed).integers(0, 16, nsym)
    syms = const[idx]
    return normalize(shape_symbols(syms, sps, beta)), fs


# ---------------------------------------------------------------------------
# Radar / pulsed
# ---------------------------------------------------------------------------

def gen_lfm_pulse(fs: float = 200e6, bw: float = 50e6, pw: float = 10e-6, pri: float = 100e-6, npulses: int = 8) -> tuple[Any, float]:
    """
    Pulse train with LFM chirp (maritime/air S/C/X radar).

    For X-band BW > 100 MHz raise fs and verify max I/Q modulation BW (e.g. B1044N).
    """
    npw = int(pw * fs)
    npri = int(pri * fs)
    t = (np.arange(npw) - npw / 2) / fs
    k = bw / pw
    pulse = np.exp(1j * np.pi * k * t ** 2)
    frame = np.zeros(npri, dtype=complex)
    frame[:npw] = pulse
    return normalize(np.tile(frame, npulses)), fs


def gen_barker_pulse(fs: float = 50e6, chip: float = 1e-6, code: tuple = (1, 1, 1, -1, -1, 1, -1), pri: float = 50e-6, npulses: int = 8) -> tuple[Any, float]:
    """Phase-coded Barker pulse train (pulse compression)."""
    spc = int(chip * fs)
    pulse = np.repeat(np.array(code, float), spc).astype(complex)
    npri = int(pri * fs)
    frame = np.zeros(npri, dtype=complex)
    frame[: len(pulse)] = pulse
    return normalize(np.tile(frame, npulses)), fs


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def gen_ppm_adsb(fs: float = 20e6, nbits: int = 112, seed: int = 8) -> tuple[Any, float]:
    """
    1 Mbps PPM-style ADS-B 1090ES (modulation placeholder).

    For decodable Mode S frames replace bits with CRC-protected frames
    from a library such as pyModeS.
    """
    sps = int(round(fs / 1e6))  # 1 µs per bit
    half = sps // 2
    bits = random_bits(nbits, seed)
    preamble = np.array([1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0])
    sig: list[float] = []
    for p in preamble:
        sig += [1.0] * half + [0.0] * half if p else [0.0] * sps
    for b in bits:  # PPM: '1' = pulse first half, '0' = pulse second half
        sig += [1.0] * half + [0.0] * half if b else [0.0] * half + [1.0] * half
    return normalize(np.array(sig, dtype=complex)), fs


def gen_multitone(fs: float = 4e6, tones: tuple = (-1e6, -0.5e6, 0.5e6, 1e6), dur: float = 2e-3) -> tuple[Any, float]:
    """Multi-tone comb / CW carriers (TT&C combs, IMD tests, ISM placeholder)."""
    t = np.arange(0, dur, 1 / fs)
    iq = sum(np.exp(1j * 2 * np.pi * f * t) for f in tones)
    return normalize(iq), fs


def gen_awgn(fs: float = 4e6, dur: float = 2e-3, seed: int = 9) -> tuple[Any, float]:
    """Complex Gaussian noise (wideband jamming / interference placeholder)."""
    n = int(fs * dur)
    rng = np.random.default_rng(seed)
    return normalize(rng.normal(size=n) + 1j * rng.normal(size=n)), fs
