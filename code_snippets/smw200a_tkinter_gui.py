#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smw200a_arb_signals.py
=======================
Generates I/Q for several signals on the PC, writes a .wv waveform, uploads it to
the R&S SMW200A, and plays it from the ARB.

License strategy (SMW200A, typical B1044N + permanent BB / ARB / PS)
----------------------------------------------------------------------
**This script only drives the permanent path:** baseband **ARB** (B9 + K515 + K527)
via ``create_waveform_file_from_samples`` → .wv → instrument. That keeps working
after trial options expire (e.g. 2026-06-11).

**[P] = permanent on your unit** (ARB / custom digital / Pulse Sequencer K300+K301).
**[T] = trial** (e.g. K720 AM/FM, K22 pulse modulator, K44/K66 GNSS, EW scenario keys).

Every catalog row below uses **ARB I/Q [P]** here. The ``trial_native`` field only
documents what you *could* use natively while trials are active — this code does
**not** switch to those blocks.

**Pulse Sequencer (K300/K301) [P]:** ideal for radar PRI/chirp trains natively on
the SMW; this script still uses **ARB LFM** for ``radar_*`` (no PS SCPI wired here).

**B1044N:** at high carriers, confirm max I/Q modulation BW in the datasheet before
raising ARB sample rate (e.g. very wide chirps at X/Ka).

Requirements:
    pip install RsSmw numpy PyYAML
    This script uses ``vsg_config.yaml`` in the **same directory as this file** (unless you
    set ``VSG_CONFIG_PATH`` to another file). Same keys as ``vsg_smw200a``: ``smw_ip``,
    ``smw_transport``, ``smw_socket_port``, ``smw_visa``. Optional FSW: ``fsw_ip`` or ``fsw43_ip``.
    Environment overrides: ``SMW_VISA``, ``SMW_IP``, ``SMW_TRANSPORT``, ``SMW_SOCKET_PORT``.

    FSW43 spectrum alignment uses the same SCPI-over-TCP code as rs_smw_fsw_tcp.py
    (``rs_scpi_tcp.py``, stdlib only): pass ``--fsw-ip`` to set spectrum center to the
    RF carrier and span from the ARB sample clock (override with ``--fsw-span-hz``).

Quick usage:
    python smw200a_arb_signals.py --list
    python smw200a_arb_signals.py --list --license-detail
    python smw200a_arb_signals.py --gui
    python smw200a_arb_signals.py --signal ais
    python smw200a_arb_signals.py --signal ais   # SMW/FSW from vsg_config.yaml when keys set
    python smw200a_arb_signals.py --signal ais --smw-ip 192.168.1.10 --fsw-ip 192.168.1.11
    python smw200a_arb_signals.py --signal radar_x --dry-run   # only creates .wv

SECURITY NOTICE
---------------
Several entries use distress/safety service frequencies (EPIRB 406 MHz,
ELT 121.5 MHz, maritime Ch16 and DSC Ch70, Mode S/ADS-B with valid ICAO).
Generate these **only** in a shielded bench (cable + attenuator) or screened
chamber — **never** radiate outdoors — to avoid triggering SAR alerts or
interfering with real traffic.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

# This file lives in code_snippets/; device modules (rs_scpi_tcp, vsg_smw200a) are in
# the sibling SIGINT-RF-DEVICE-VSG_SMW200A/ inner folder — add it to sys.path first.
_device_inner = Path(__file__).resolve().parent.parent / "SIGINT-RF-DEVICE-VSG_SMW200A"
if str(_device_inner) not in sys.path:
    sys.path.insert(0, str(_device_inner))

# Use vsg_config.yaml from the inner folder (where vsg_smw200a.py lives).
_VSG_YAML = _device_inner / "vsg_config.yaml"
if _VSG_YAML.is_file() and not os.environ.get("VSG_CONFIG_PATH", "").strip():
    os.environ["VSG_CONFIG_PATH"] = str(_VSG_YAML)

import numpy as np
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from rs_scpi_tcp import ScpiTcp, fsw_bw_summary, fsw_configure_spectrum
from vsg_smw200a import (
    VsgSmw200a,
    build_visa_resource,
    config_file_path,
    default_smw_ip,
    default_socket_port,
    default_transport,
    default_visa_from_config,
    default_visa_from_env,
    is_rs_smw_installed,
)

# ----------------------------------------------------------------------------
# Instrument connection (vsg_config.yaml + env, same as vsg_smw200a.VsgSmw200a)
# ----------------------------------------------------------------------------
# HiSLIP:  TCPIP::<ip>::HISLIP   |  Socket:  TCPIP::<ip>::<port>::SOCKET

# Working paths on PC and instrument
PC_WV = r"./_tmp_signal.wv"
INSTR_WV = "/var/user/arb_signal.wv"


def _read_vsg_config_mapping() -> dict[str, Any]:
    """Load ``vsg_config.yaml`` for optional keys (FSW defaults) not exposed on vsg_smw200a."""
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _fsw_ip_from_vsg_yaml() -> str | None:
    for key in ("fsw_ip", "fsw43_ip"):
        v = _read_vsg_config_mapping().get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def resolve_instr_addr(instr_addr: str | None, smw_ip: str | None) -> str:
    """
    VISA resource string for the SMW.

    Config file: ``VSG_CONFIG_PATH`` if set, else ``vsg_config.yaml`` next to this script.

    Priority: explicit ``instr_addr``, then ``smw_ip`` (with ``smw_transport`` / ``smw_socket_port``
    from YAML or env), then ``SMW_VISA``, then YAML ``smw_visa``, then YAML ``smw_ip`` + transport
    (same resolution order as ``vsg_smw200a.VsgSmw200a``).
    """
    if instr_addr and instr_addr.strip():
        return instr_addr.strip()
    if smw_ip and smw_ip.strip():
        return build_visa_resource(
            ip=smw_ip.strip(),
            transport=default_transport(),
            socket_port=default_socket_port(),
        )
    env_visa = default_visa_from_env()
    if env_visa:
        return env_visa
    cfg_visa = default_visa_from_config()
    if cfg_visa:
        return cfg_visa
    return build_visa_resource(
        ip=None,
        transport=default_transport(),
        socket_port=default_socket_port(),
    )


def estimate_fsw_span_hz(fs: float) -> float:
    """
    Heuristic spectrum span (Hz) from ARB sample clock ``fs``.
    Wideband waveforms (large ``fs``) get a wider span, capped for typical FSW ranges.
    """
    if fs <= 1e6:
        return max(5e6, 25.0 * fs)
    if fs <= 32e6:
        return max(10e6, min(200e6, 6.0 * fs))
    if fs <= 120e6:
        return max(40e6, min(500e6, 4.0 * fs))
    return min(600e6, max(100e6, 2.5 * fs))


def default_fsw_ref_dbm(rf_power_dbm: float) -> float:
    """Reference level above expected RF output (conducted / short cable)."""
    return float(max(-30.0, min(40.0, rf_power_dbm + 55.0)))


def tune_fsw43(
    fsw_ip: str,
    *,
    port: int = 5025,
    timeout_s: float = 15.0,
    center_hz: float,
    span_hz: float,
    ref_dbm: float,
    channel: int = 1,
    rbw_hz: float | None = None,
    vbw_hz: float | None = None,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """Configure FSW spectrum to match the ARB carrier and estimated bandwidth."""
    emit = log or print
    ip = fsw_ip.strip()
    if not ip:
        return
    rbw_auto = rbw_hz is None
    vbw_auto = vbw_hz is None
    with ScpiTcp(ip, port, timeout_s=timeout_s) as fsw:
        idn = fsw.query("*IDN?")
        emit(f"FSW IDN: {idn}")
        fsw_configure_spectrum(
            fsw,
            center_hz=center_hz,
            span_hz=span_hz,
            ref_level_dbm=ref_dbm,
            channel=channel,
            rbw_hz=rbw_hz,
            vbw_hz=vbw_hz,
            rbw_auto=rbw_auto,
            vbw_auto=vbw_auto,
        )
    bw = fsw_bw_summary(rbw_auto, vbw_auto, rbw_hz, vbw_hz)
    emit(
        f"FSW: center {center_hz / 1e9:.9f} GHz, span {span_hz / 1e6:.3f} MHz, "
        f"RLEV {ref_dbm:.1f} dBm, ch={channel}, {bw}"
    )


# ============================================================================
#  DSP helpers
# ============================================================================
def normalize(iq):
    """Scale complex samples so max |iq| <= 1 (driver auto_scale does the rest)."""
    m = np.max(np.abs(iq))
    return iq / m if m > 0 else iq


def rrc_filter(beta, sps, span=10):
    """Root raised cosine. beta = roll-off, sps = samples/symbol, span in symbols."""
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
    return h / np.sqrt(np.sum(h**2))


def gaussian_pulse(bt, sps, span=4):
    """Gaussian pulse for GMSK/GFSK. bt = B*T."""
    n = np.arange(-span * sps / 2, span * sps / 2 + 1)
    t = n / sps
    alpha = np.sqrt(np.log(2) / 2) / bt
    g = (1 / (np.sqrt(2 * np.pi) * alpha)) * np.exp(-(t**2) / (2 * alpha**2))
    return g / np.sum(g)


def random_bits(n, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, n)


def shape_symbols(symbols, sps, beta=0.35):
    """Upsample complex symbols with zeros between symbols and apply RRC."""
    up = np.zeros(len(symbols) * sps, dtype=complex)
    up[::sps] = symbols
    h = rrc_filter(beta, sps)
    return np.convolve(up, h, mode="same")


# ============================================================================
#  Signal generators  ->  return (complex_iq, fs)
# ============================================================================
def gen_am_dsb(fs=200e3, tone=1e3, depth=0.8, dur=20e-3):
    """AM-DSB (A3E): ATC voice / VOR-style envelope on carrier; Q = 0."""
    t = np.arange(0, dur, 1 / fs)
    env = 1 + depth * np.sin(2 * np.pi * tone * t)
    return normalize(env.astype(complex)), fs


def gen_fm(fs=200e3, tone=1e3, dev=5e3, dur=20e-3):
    """Narrowband FM: maritime voice / LMR / NOAA / GMRS style."""
    t = np.arange(0, dur, 1 / fs)
    msg = np.sin(2 * np.pi * tone * t)
    phase = 2 * np.pi * dev * np.cumsum(msg) / fs
    return normalize(np.exp(1j * phase)), fs


def gen_gmsk(fs=240e3, rb=9600, bt=0.4, nbits=512, seed=1):
    """GMSK (AIS Ch87B/88B). h = 0.5. Real AIS frames -> replace bits with a proper encoder."""
    sps = int(round(fs / rb))
    bits = random_bits(nbits, seed)
    nrz = np.repeat(2 * bits - 1, sps).astype(float)
    g = gaussian_pulse(bt, sps)
    freq = np.convolve(nrz, g, mode="same")
    phase = np.cumsum(freq) * (np.pi * 0.5 / sps)  # h = 0.5
    return normalize(np.exp(1j * phase)), fs


def gen_fsk(fs=120e3, rs=1200, devs=(2100, -1300), nsym=256, seed=2):
    """2-FSK / AFSK-style (DSC Ch70). devs = frequency deviation per symbol (Hz)."""
    sps = int(round(fs / rs))
    levels = np.array(devs)
    syms = np.random.default_rng(seed).integers(0, len(levels), nsym)
    f = np.repeat(levels[syms], sps).astype(float)
    phase = 2 * np.pi * np.cumsum(f) / fs
    return normalize(np.exp(1j * phase)), fs


def gen_4fsk(fs=192e3, rs=4800, dev=1944, nsym=256, seed=3):
    """4-FSK (DMR / P25 C4FM / NXDN). Levels +/-3dev/3 and +/-dev/3."""
    sps = int(round(fs / rs))
    levels = np.array([-dev, -dev / 3, dev / 3, dev])
    syms = np.random.default_rng(seed).integers(0, 4, nsym)
    f = np.repeat(levels[syms], sps).astype(float)
    phase = 2 * np.pi * np.cumsum(f) / fs
    return normalize(np.exp(1j * phase)), fs


def gen_pi4_dqpsk(fs=720e3, rs=18e3, nsym=512, beta=0.35, seed=4):
    """pi/4-DQPSK (TETRA)."""
    sps = int(round(fs / rs))
    dibits = np.random.default_rng(seed).integers(0, 4, nsym)
    dphi = {0: np.pi / 4, 1: 3 * np.pi / 4, 2: -np.pi / 4, 3: -3 * np.pi / 4}
    phase = np.cumsum([dphi[d] for d in dibits])
    syms = np.exp(1j * phase)
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_psk(fs=2e6, rs=250e3, order=4, nsym=1024, beta=0.35, seed=5):
    """Generic M-PSK (UHF SATCOM, TT&C BPSK/QPSK, X-band satcom QPSK/8PSK)."""
    sps = int(round(fs / rs))
    m = np.random.default_rng(seed).integers(0, order, nsym)
    syms = np.exp(1j * (2 * np.pi * m / order))
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_qam(fs=4e6, rs=500e3, order=16, nsym=1024, beta=0.25, seed=6):
    """M-QAM (payload downlinks, data feeds)."""
    sps = int(round(fs / rs))
    k = int(np.sqrt(order))
    lvl = np.arange(-(k - 1), k, 2)
    rng = np.random.default_rng(seed)
    i_arr = rng.choice(lvl, nsym)
    q_arr = rng.choice(lvl, nsym)
    syms = i_arr + 1j * q_arr
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_apsk16(fs=4e6, rs=500e3, nsym=1024, beta=0.2, seed=7):
    """16-APSK (DVB-S2 Ku/Ka). Rings 4+12, gamma = 2.85 (~3/4 code rate)."""
    sps = int(round(fs / rs))
    r1, r2 = 1.0, 2.85
    inner = r1 * np.exp(1j * (np.pi / 4 + np.pi / 2 * np.arange(4)))
    outer = r2 * np.exp(1j * (np.pi / 12 * (2 * np.arange(12))))
    const = np.concatenate([inner, outer])
    idx = np.random.default_rng(seed).integers(0, 16, nsym)
    syms = const[idx]
    return normalize(shape_symbols(syms, sps, beta)), fs


def gen_lfm_pulse(fs=200e6, bw=50e6, pw=10e-6, pri=100e-6, npulses=8):
    """Pulse train with LFM chirp (maritime/air S/C/X radar).
    For X-band BW > 100 MHz, raise fs and check I/Q bandwidth (e.g. B1044N)."""
    npw = int(pw * fs)
    npri = int(pri * fs)
    t = (np.arange(npw) - npw / 2) / fs
    k = bw / pw
    pulse = np.exp(1j * np.pi * k * t**2)
    frame = np.zeros(npri, dtype=complex)
    frame[:npw] = pulse
    return normalize(np.tile(frame, npulses)), fs


def gen_barker_pulse(fs=50e6, chip=1e-6, code=(1, 1, 1, -1, -1, 1, -1), pri=50e-6, npulses=8):
    """Phase-coded Barker pulse (pulse compression)."""
    spc = int(chip * fs)
    pulse = np.repeat(np.array(code, float), spc).astype(complex)
    npri = int(pri * fs)
    frame = np.zeros(npri, dtype=complex)
    frame[: len(pulse)] = pulse
    return normalize(np.tile(frame, npulses)), fs


def gen_ppm_adsb(fs=20e6, nbits=112, seed=8):
    """1 Mbps PPM-style ADS-B 1090ES (modulation placeholder).
    For decodable Mode S frames use a library (e.g. pyModeS) and replace bits
    with CRC-protected frames; here only PPM shape."""
    sps = int(round(fs / 1e6))  # 1 us per bit
    half = sps // 2
    bits = random_bits(nbits, seed)
    preamble = np.array([1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0])
    sig = []
    for p in preamble:
        sig += [1.0] * half + [0.0] * half if p else [0.0] * sps
    for b in bits:  # PPM: '1' = pulse first half, '0' = pulse second half
        sig += (
            ([1.0] * half + [0.0] * half) if b else ([0.0] * half + [1.0] * half)
        )
    return normalize(np.array(sig, dtype=complex)), fs


def gen_multitone(fs=4e6, tones=(-1e6, -0.5e6, 0.5e6, 1e6), dur=2e-3):
    """Multi-tone comb / CW carriers (TT&C combs, IMD tests)."""
    t = np.arange(0, dur, 1 / fs)
    iq = sum(np.exp(1j * 2 * np.pi * f * t) for f in tones)
    return normalize(iq), fs


def gen_awgn(fs=4e6, dur=2e-3, seed=9):
    """Complex Gaussian noise (wideband jamming / interference placeholder)."""
    n = int(fs * dur)
    rng = np.random.default_rng(seed)
    return normalize(rng.normal(size=n) + 1j * rng.normal(size=n)), fs


# ============================================================================
#  Catalog: NamedTuple with permanent ARB path + trial-native notes (not used here)
# ============================================================================
PERMANENT_ARB = "ARB I/Q (B9+K515+K527) [P]"


class CatalogEntry(NamedTuple):
    """Waveform row: generator, RF, level, description, and license routing hints."""

    gen: Callable[..., tuple[Any, float]]
    carrier_hz: float
    power_dbm: float
    description: str
    permanent_delivery: str  # always ARB for this script
    trial_native: str  # native SMW option while trial active; empty if none / N/A


class GenParamSpec(NamedTuple):
    """One generator keyword argument exposed in the GUI (non-fixed partial params)."""

    name: str
    label: str
    kind: str  # "float" | "int" | "tuple_float"
    default: Any


def _unwrap_partial_chain(f: Callable[..., Any]) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Return underlying function and merged keyword bindings from nested functools.partial."""
    merged: dict[str, Any] = {}
    cur: Any = f
    while isinstance(cur, functools.partial):
        if getattr(cur, "args", None) and len(cur.args) > 0:
            raise ValueError("Generator partial() with positional args is not supported.")
        merged = {**merged, **(cur.keywords or {})}
        cur = cur.func
    return cur, merged


def _param_kind_from_default(val: Any) -> str:
    if isinstance(val, tuple):
        return "tuple_float"
    if isinstance(val, bool):
        return "float"  # should not appear; treat as float if it does
    if isinstance(val, int) and not isinstance(val, bool):
        return "int"
    return "float"


def _default_label(name: str) -> str:
    pretty = {
        "fs": "Sample rate fs (Hz)",
        "tone": "Modulation tone (Hz)",
        "depth": "AM depth (0..1)",
        "dur": "Duration (s)",
        "dev": "Peak FM deviation (Hz) / 4FSK spacing scale (Hz)",
        "rb": "Symbol rate (bit/s for GMSK)",
        "bt": "BT product (GMSK)",
        "nbits": "Number of bits",
        "rs": "Symbol rate (sym/s)",
        "nsym": "Number of symbols",
        "seed": "RNG seed",
        "beta": "RRC roll-off",
        "order": "Constellation order M",
        "bw": "Chirp bandwidth (Hz)",
        "pw": "Pulse width (s)",
        "pri": "PRI / pulse repetition interval (s)",
        "npulses": "Pulses in waveform",
        "chip": "Barker chip width (s)",
        "tones": "Tone offsets (Hz), comma-separated",
        "devs": "FSK frequency deviations (Hz), comma-separated",
    }
    return pretty.get(name, name)


def list_generator_param_specs(entry: CatalogEntry) -> list[GenParamSpec]:
    """Parameters the user may edit (underlying signature minus partial-fixed names)."""
    underlying, fixed_kw = _unwrap_partial_chain(entry.gen)
    sig = inspect.signature(underlying)
    out: list[GenParamSpec] = []
    for pname, p in sig.parameters.items():
        if pname in fixed_kw:
            continue
        if p.default is inspect.Parameter.empty:
            continue
        kind = _param_kind_from_default(p.default)
        out.append(GenParamSpec(pname, _default_label(pname), kind, p.default))
    return out


def parse_gen_param_value(spec: GenParamSpec, text: str) -> Any:
    """Parse a single field from the GUI into a Python value."""
    t = text.strip()
    if spec.kind == "tuple_float":
        if not t:
            return spec.default
        parts = [p.strip() for p in t.split(",") if p.strip()]
        return tuple(float(x) for x in parts)
    if spec.kind == "int":
        if not t:
            return spec.default
        return int(float(t))
    if not t:
        return spec.default
    return float(t)


def format_gen_default_for_entry(spec: GenParamSpec) -> str:
    """String to pre-fill a GUI field from the catalog default."""
    v = spec.default
    if spec.kind == "tuple_float":
        return ",".join(str(float(x)) for x in v)
    if spec.kind == "int":
        return str(int(v))
    return str(v)


def build_gen_call_kwargs(entry: CatalogEntry, overrides: dict[str, Any]) -> dict[str, Any]:
    """
    Build kwargs for entry.gen() respecting functools.partial bindings.
    ``overrides`` only needs to include keys the user changed; missing keys use signature defaults.
    """
    underlying, fixed_kw = _unwrap_partial_chain(entry.gen)
    sig = inspect.signature(underlying)
    call_kw: dict[str, Any] = {}
    for pname, p in sig.parameters.items():
        if pname in fixed_kw:
            continue
        if pname in overrides:
            call_kw[pname] = overrides[pname]
        elif p.default is not inspect.Parameter.empty:
            call_kw[pname] = p.default
        else:
            raise ValueError(f"Missing value for generator parameter {pname!r}")
    return call_kw


CATALOG: dict[str, CatalogEntry] = {
    "atc_am": CatalogEntry(
        gen_am_dsb,
        118.000e6,
        -30,
        "ATC voice AM-DSB",
        PERMANENT_ARB,
        "AM nativo K720 [T] (no usado aqui)",
    ),
    "maritime_fm": CatalogEntry(
        gen_fm,
        156.800e6,
        -30,
        "Maritime voice FM (Ch16)",
        PERMANENT_ARB,
        "FM nativo K720 [T] (no usado aqui)",
    ),
    "noaa_fm": CatalogEntry(
        gen_fm,
        162.400e6,
        -30,
        "NOAA WX FM",
        PERMANENT_ARB,
        "FM nativo K720 [T] (no usado aqui)",
    ),
    "gmrs_fm": CatalogEntry(
        gen_fm,
        462.5625e6,
        -30,
        "GMRS FM",
        PERMANENT_ARB,
        "FM nativo K720 [T] (no usado aqui)",
    ),
    "ais": CatalogEntry(
        gen_gmsk,
        161.975e6,
        -40,
        "AIS GMSK",
        PERMANENT_ARB,
        "Custom GMSK / ARB [P] - bits aleatorios (trama AIS real -> encoder externo)",
    ),
    "dsc": CatalogEntry(
        gen_fsk,
        156.525e6,
        -40,
        "DSC Ch70 FSK",
        PERMANENT_ARB,
        "Custom 2FSK / ARB [P]",
    ),
    "dmr": CatalogEntry(
        gen_4fsk,
        466.000e6,
        -40,
        "DMR 4FSK",
        PERMANENT_ARB,
        "Custom 4FSK / ARB [P] - vocoder real -> ARB externo",
    ),
    "p25": CatalogEntry(
        gen_4fsk,
        460.000e6,
        -40,
        "P25 C4FM",
        PERMANENT_ARB,
        "Custom 4FSK / ARB [P]",
    ),
    "tetra": CatalogEntry(
        gen_pi4_dqpsk,
        392.000e6,
        -40,
        "TETRA pi/4-DQPSK",
        PERMANENT_ARB,
        "Custom pi/4-DQPSK / ARB [P]",
    ),
    "uhf_satcom": CatalogEntry(
        gen_psk,
        250.000e6,
        -50,
        "UHF SATCOM QPSK",
        PERMANENT_ARB,
        "Custom PSK / ARB [P]",
    ),
    "adsb": CatalogEntry(
        gen_ppm_adsb,
        1090.000e6,
        -50,
        "ADS-B 1090ES PPM (placeholder)",
        PERMANENT_ARB,
        "ARB [P] - trama Mode S real (p. ej. pyModeS + CRC)",
    ),
    "ism_24": CatalogEntry(
        gen_multitone,
        2440.000e6,
        -40,
        "ISM 2.4 GHz (placeholder)",
        PERMANENT_ARB,
        "WLAN/BT/Zigbee opciones [T] si las tuvieras; multitono ARB [P]",
    ),
    "radar_s": CatalogEntry(
        gen_lfm_pulse,
        3050.000e6,
        -20,
        "Maritime S-band radar LFM",
        PERMANENT_ARB,
        "Pulse Sequencer K300/K301 [P] (nativo); K22 pulso [T]; aqui ARB LFM [P]",
    ),
    "datalink_c": CatalogEntry(
        gen_psk,
        4700.000e6,
        -40,
        "C-band data link QPSK",
        PERMANENT_ARB,
        "Custom / ARB [P]",
    ),
    "radar_c": CatalogEntry(
        gen_lfm_pulse,
        5400.000e6,
        -20,
        "Airborne C-band radar LFM",
        PERMANENT_ARB,
        "Pulse Sequencer K300/K301 [P] (nativo); aqui ARB LFM [P]",
    ),
    "satcom_c": CatalogEntry(
        gen_apsk16,
        5850.000e6,
        -40,
        "SATCOM C-band 16APSK",
        PERMANENT_ARB,
        "Constelacion 16APSK; framing DVB-S2/S2X real -> toolchain o WinIQSIM2 [P/T segun opciones]",
    ),
    "satcom_x": CatalogEntry(
        gen_psk,
        8100.000e6,
        -40,
        "X-band satcom QPSK",
        PERMANENT_ARB,
        "Custom / ARB [P]",
    ),
    "radar_x": CatalogEntry(
        gen_lfm_pulse,
        9400.000e6,
        -20,
        "X-band radar LFM",
        PERMANENT_ARB,
        "Pulse Sequencer K300/K301 [P] (nativo); B1044N BW I/Q a verificar; ARB LFM [P]",
    ),
    "ku_dl": CatalogEntry(
        gen_apsk16,
        12200.000e6,
        -40,
        "Ku downlink 16APSK",
        PERMANENT_ARB,
        "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]",
    ),
    "ka_dl": CatalogEntry(
        gen_apsk16,
        19500.000e6,
        -40,
        "Ka downlink 16APSK",
        PERMANENT_ARB,
        "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]",
    ),
    "ka_ul": CatalogEntry(
        gen_apsk16,
        29500.000e6,
        -40,
        "Ka uplink 16APSK",
        PERMANENT_ARB,
        "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]",
    ),
}

# BPSK for GNSS L1 / TT&C S-band (same gen_psk, order=2)
CATALOG["gnss_l1"] = CatalogEntry(
    functools.partial(gen_psk, order=2),
    1575.420e6,
    -60,
    "GNSS L1 BPSK (1 SV placeholder)",
    PERMANENT_ARB,
    "Opciones GNSS K44/K66/K94/K107 [T]; ARB 1 SV limitado [P]",
)
CATALOG["ttc_sband"] = CatalogEntry(
    functools.partial(gen_psk, order=2),
    2025.000e6,
    -40,
    "TT&C S-band BPSK",
    PERMANENT_ARB,
    "Custom PCM/PSK / ARB [P]",
)


# ============================================================================
#  Playback on SMW200A
# ============================================================================
def play(
    name: str,
    dry_run: bool = False,
    instr_addr: str | None = None,
    *,
    carrier_hz: float | None = None,
    power_dbm: float | None = None,
    gen_kwargs: dict[str, Any] | None = None,
    fsw_ip: str | None = None,
    fsw_port: int = 5025,
    fsw_timeout_s: float = 15.0,
    fsw_span_hz: float | None = None,
    fsw_ref_dbm: float | None = None,
    fsw_ch: int = 1,
    fsw_rbw_hz: float | None = None,
    fsw_vbw_hz: float | None = None,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    emit = log or print
    e = CATALOG[name]
    gkw = gen_kwargs or {}
    call_kw = build_gen_call_kwargs(e, gkw)
    iq, fs = e.gen(**call_kw)
    freq = float(e.carrier_hz if carrier_hz is None else carrier_hz)
    power = float(e.power_dbm if power_dbm is None else power_dbm)
    desc = e.description
    i_data = np.real(iq).astype(float)
    q_data = np.imag(iq).astype(float)
    addr = instr_addr or resolve_instr_addr(None, None)
    span = fsw_span_hz if fsw_span_hz is not None else estimate_fsw_span_hz(fs)
    ref = fsw_ref_dbm if fsw_ref_dbm is not None else default_fsw_ref_dbm(power)
    emit(f"[{name}] generator kwargs: {call_kw}")
    emit(
        f"[{name}] {desc} | fs={fs / 1e6:.3f} MS/s | N={len(iq)} "
        f"| RF={freq / 1e6:.3f} MHz | {power} dBm | {e.permanent_delivery}"
    )
    emit(f"VISA: {addr}")
    if fsw_ip and fsw_ip.strip():
        emit(
            f"FSW (planned): center {freq / 1e9:.9f} GHz, span {span / 1e6:.3f} MHz, "
            f"RLEV {ref:.1f} dBm @ {fsw_ip.strip()}:{fsw_port}"
        )

    with VsgSmw200a(visa_resource=addr) as vsg:
        smw = vsg.smw
        emit(f"IDN: {smw.utilities.idn_string}")

        smw.arb_files.create_waveform_file_from_samples(
            i_data,
            q_data,
            PC_WV,
            clock_freq=fs,
            auto_scale=True,
            comment=f"{name}: {desc}",
        )
        if dry_run:
            emit(f".wv written to {PC_WV} (dry-run, not sent to instrument).")
        else:
            smw.arb_files.send_waveform_file_to_instrument(PC_WV, INSTR_WV)
            smw.source.bb.arbitrary.waveform.set_select(INSTR_WV)
            smw.source.bb.arbitrary.set_state(True)
            smw.source.frequency.set_frequency(freq)
            smw.source.power.level.immediate.set_amplitude(power)
            smw.output.state.set_value(True)
            emit("RF ON. Use conducted/shielded setup only (see script security notice).")

    if fsw_ip and fsw_ip.strip():
        try:
            tune_fsw43(
                fsw_ip,
                port=fsw_port,
                timeout_s=fsw_timeout_s,
                center_hz=freq,
                span_hz=span,
                ref_dbm=ref,
                channel=fsw_ch,
                rbw_hz=fsw_rbw_hz,
                vbw_hz=fsw_vbw_hz,
                log=emit,
            )
        except OSError as e:
            emit(f"FSW: could not connect or configure ({fsw_ip.strip()}:{fsw_port}): {e}")


# ============================================================================
#  Tkinter GUI (also opened from rs_smw_fsw_tcp.py)
# ============================================================================
class ArbSignalsGuiApp:
    """Pick catalog waveform, apply to SMW via VsgSmw200a / YAML, optionally tune FSW (SCPI TCP)."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        smw_ip: str | None = None,
        fsw_ip: str | None = None,
        fsw_port: int = 5025,
        fsw_timeout: float = 15.0,
    ) -> None:
        self.root = master
        self._busy = False
        keys = sorted(CATALOG.keys())

        mainf = ttk.Frame(master, padding=8)
        mainf.pack(fill=tk.BOTH, expand=True)
        mainf.columnconfigure(0, weight=1)

        r = 0
        if not is_rs_smw_installed():
            wfr = ttk.Frame(mainf)
            wfr.grid(row=r, column=0, sticky="ew", pady=(0, 8))
            ttk.Label(
                wfr,
                text=(
                    "RsSmw no está instalado: no se puede generar el .wv ni controlar el SMW hasta instalarlo.\n"
                    "Abrí una consola y ejecutá:  pip install RsSmw\n"
                    "(vsg_smw200a / vsg_config.yaml usan el mismo driver; mismo Python que esta app.)"
                ),
                foreground="#b00000",
                wraplength=640,
                justify=tk.LEFT,
            ).pack(anchor="w")
            r += 1

        conn = ttk.LabelFrame(mainf, text="Connection", padding=6)
        conn.grid(row=r, column=0, sticky="ew", pady=(0, 6))
        r += 1
        conn.columnconfigure(1, weight=1)

        ttk.Label(conn, text="VISA override (optional):").grid(row=0, column=0, sticky="w")
        self.ent_instr = ttk.Entry(conn, width=48)
        self.ent_instr.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(conn, text="SMW IP (HiSLIP/socket desde YAML si vacío):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.ent_smw_ip = ttk.Entry(conn, width=20)
        smw_guess = default_smw_ip(None) if not (smw_ip or "").strip() else str(smw_ip).strip()
        self.ent_smw_ip.insert(0, smw_guess)
        self.ent_smw_ip.grid(row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        ttk.Label(conn, text="FSW IP:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.ent_fsw_ip = ttk.Entry(conn, width=20)
        fsw_guess = (_fsw_ip_from_vsg_yaml() or "") if not (fsw_ip or "").strip() else str(fsw_ip).strip()
        self.ent_fsw_ip.insert(0, fsw_guess)
        self.ent_fsw_ip.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        ttk.Label(conn, text="FSW port:").grid(row=2, column=2, sticky="e", padx=(16, 4), pady=(4, 0))
        self.ent_fsw_port = ttk.Entry(conn, width=8)
        self.ent_fsw_port.insert(0, str(fsw_port))
        self.ent_fsw_port.grid(row=2, column=3, sticky="w", pady=(4, 0))

        opt = ttk.LabelFrame(mainf, text="Signal & FSW options", padding=6)
        opt.grid(row=r, column=0, sticky="ew", pady=(0, 6))
        r += 1
        opt.columnconfigure(1, weight=1)

        ttk.Label(opt, text="Catalog signal:").grid(row=0, column=0, sticky="w")
        self.combo_signal = ttk.Combobox(opt, width=36, values=keys, state="readonly")
        if keys:
            self.combo_signal.current(0)
        self.combo_signal.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.combo_signal.bind("<<ComboboxSelected>>", lambda _e: self._rebuild_param_panel())
        self.var_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Dry-run (.wv only, no upload / no RF)", variable=self.var_dry).grid(
            row=0, column=2, padx=(12, 0)
        )

        ttk.Label(opt, text="RF carrier MHz (empty=catalog):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_rf_mhz = ttk.Entry(opt, width=18)
        self.ent_rf_mhz.grid(row=1, column=1, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Label(opt, text="RF power dBm (empty=catalog):").grid(row=1, column=2, sticky="e", padx=(12, 4), pady=(6, 0))
        self.ent_rf_dbm = ttk.Entry(opt, width=10)
        self.ent_rf_dbm.grid(row=1, column=3, sticky="w", pady=(6, 0))

        ttk.Label(opt, text="FSW span Hz (empty=auto):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ent_fsw_span = ttk.Entry(opt, width=18)
        self.ent_fsw_span.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Label(opt, text="FSW ref dBm (empty=auto):").grid(row=2, column=2, sticky="e", padx=(12, 4), pady=(6, 0))
        self.ent_fsw_ref = ttk.Entry(opt, width=10)
        self.ent_fsw_ref.grid(row=2, column=3, sticky="w", pady=(6, 0))
        ttk.Label(opt, text="FSW ch:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.ent_fsw_ch = ttk.Entry(opt, width=4)
        self.ent_fsw_ch.insert(0, "1")
        self.ent_fsw_ch.grid(row=3, column=1, sticky="w", padx=(4, 0), pady=(6, 0))

        self.fsw_timeout = fsw_timeout

        param_lf = ttk.LabelFrame(mainf, text="Waveform parameters (I/Q generator)", padding=6)
        param_lf.grid(row=r, column=0, sticky="nsew", pady=(0, 6))
        r += 1
        param_lf.columnconfigure(0, weight=1)
        self.params_inner = ttk.Frame(param_lf)
        self.params_inner.grid(row=0, column=0, sticky="nsew")
        self.params_inner.columnconfigure(1, weight=1)
        self._param_widgets: dict[str, tuple[GenParamSpec, ttk.Entry]] = {}

        bf = ttk.Frame(mainf)
        bf.grid(row=r, column=0, sticky="w", pady=(0, 6))
        r += 1
        self.btn_play = ttk.Button(bf, text="Generate & apply (SMW + optional FSW)", command=self._on_play)
        self.btn_play.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="List catalog in log", command=self._on_list).pack(side=tk.LEFT)

        ttk.Label(mainf, text="Log:").grid(row=r, column=0, sticky="nw")
        r += 1
        self.txt = scrolledtext.ScrolledText(mainf, height=14, wrap=tk.WORD, state=tk.NORMAL)
        self.txt.grid(row=r, column=0, sticky="nsew", pady=(2, 0))
        mainf.rowconfigure(r, weight=1)
        self._append_log("Select a signal, edit waveform parameters if needed, then Generate.")

        self._rebuild_param_panel()

    def _append_log(self, msg: str) -> None:
        self.txt.insert(tk.END, msg + "\n")
        self.txt.see(tk.END)

    def _rebuild_param_panel(self) -> None:
        for w in self.params_inner.winfo_children():
            w.destroy()
        self._param_widgets.clear()
        name = self.combo_signal.get().strip()
        if name not in CATALOG:
            ttk.Label(self.params_inner, text="(invalid catalog selection)").grid(row=0, column=0, sticky="w")
            return
        specs = list_generator_param_specs(CATALOG[name])
        if not specs:
            ttk.Label(self.params_inner, text="(no editable parameters)").grid(row=0, column=0, sticky="w")
            return
        for i, spec in enumerate(specs):
            ttk.Label(self.params_inner, text=f"{spec.label}:").grid(row=i, column=0, sticky="nw", pady=2)
            ent = ttk.Entry(self.params_inner, width=48)
            ent.insert(0, format_gen_default_for_entry(spec))
            ent.grid(row=i, column=1, sticky="ew", pady=2, padx=(6, 0))
            self._param_widgets[spec.name] = (spec, ent)

    def _collect_gen_kwargs_from_gui(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, (spec, ent) in self._param_widgets.items():
            try:
                out[name] = parse_gen_param_value(spec, ent.get())
            except ValueError as err:
                raise ValueError(f"{spec.label}: valor invalido ({err})") from err
        return out

    def _gui_log(self, msg: str) -> None:
        self.root.after(0, lambda m=msg: self._append_log(m))

    @staticmethod
    def _parse_opt_float(s: str) -> float | None:
        t = s.strip()
        return None if not t else float(t)

    def _run_async(self, title: str, fn: Callable[[], None]) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "An operation is in progress; please wait.")
            return
        self._busy = True
        self.btn_play.config(state=tk.DISABLED)

        def target() -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self.root.after(0, lambda: messagebox.showerror(title, err))
                self.root.after(0, lambda: self._append_log(f"[{title}] ERROR: {err}"))
            finally:
                self._busy = False
                self.root.after(0, lambda: self.btn_play.config(state=tk.NORMAL))

        threading.Thread(target=target, daemon=True).start()

    def _on_list(self) -> None:
        self._append_log("Available signals - este script usa solo ARB I/Q [P] (B9+K515+K527):")
        for k, e in CATALOG.items():
            self._append_log(
                f"  {k:14s} {e.carrier_hz / 1e6:10.3f} MHz  {e.power_dbm:>4} dBm  {e.description}"
            )
            self._append_log(f"       {e.permanent_delivery}")
            if e.trial_native:
                self._append_log(f"       (Nativo trial, no usado aqui: {e.trial_native})")

    def _on_play(self) -> None:
        name = self.combo_signal.get().strip()
        if not name or name not in CATALOG:
            messagebox.showwarning("Signal", "Select a catalog signal from the list.")
            return
        instr_override = self.ent_instr.get().strip() or None
        smw_ip = self.ent_smw_ip.get().strip() or None
        try:
            fsw_port = int(self.ent_fsw_port.get().strip())
            fsw_ch = int(self.ent_fsw_ch.get().strip())
        except ValueError:
            messagebox.showerror("Parameters", "FSW port and FSW ch must be integers.")
            return
        fsw_ip = self.ent_fsw_ip.get().strip() or None
        fsw_span = self._parse_opt_float(self.ent_fsw_span.get())
        fsw_ref = self._parse_opt_float(self.ent_fsw_ref.get())
        addr = resolve_instr_addr(instr_override, smw_ip)

        try:
            gen_kwargs = self._collect_gen_kwargs_from_gui()
        except ValueError as e:
            messagebox.showerror("Waveform parameters", str(e))
            return

        rf_mhz = self._parse_opt_float(self.ent_rf_mhz.get())
        rf_dbm = self._parse_opt_float(self.ent_rf_dbm.get())
        carrier_hz = None if rf_mhz is None else float(rf_mhz) * 1e6
        power_dbm = rf_dbm

        def work() -> None:
            play(
                name,
                dry_run=bool(self.var_dry.get()),
                instr_addr=addr,
                carrier_hz=carrier_hz,
                power_dbm=power_dbm,
                gen_kwargs=gen_kwargs,
                fsw_ip=fsw_ip,
                fsw_port=fsw_port,
                fsw_timeout_s=self.fsw_timeout,
                fsw_span_hz=fsw_span,
                fsw_ref_dbm=fsw_ref,
                fsw_ch=fsw_ch,
                log=self._gui_log,
            )
            self._gui_log("Done.")

        self._run_async("ARB catalog", work)


def open_arb_signals_gui(
    parent: tk.Misc,
    *,
    smw_ip: str | None = None,
    fsw_ip: str | None = None,
    fsw_port: int = 5025,
    fsw_timeout: float = 15.0,
) -> tk.Toplevel:
    """
    Open ARB catalog UI as a child window (same process as rs_smw_fsw_tcp GUI).
    """
    try:
        parent.update_idletasks()
    except tk.TclError:
        pass
    win = tk.Toplevel(parent)
    # ASCII title avoids rare Tcl/encoding issues on Windows consoles.
    win.title("R&S SMW200A - ARB catalog signals")
    win.minsize(680, 560)
    win.geometry("720x600")
    try:
        win.transient(parent.winfo_toplevel())
    except tk.TclError:
        pass
    ArbSignalsGuiApp(win, smw_ip=smw_ip, fsw_ip=fsw_ip, fsw_port=fsw_port, fsw_timeout=fsw_timeout)
    win.deiconify()
    win.lift()
    win.focus_force()
    try:
        if sys.platform == "win32":
            win.attributes("-topmost", True)
            win.after(200, lambda w=win: w.attributes("-topmost", False))
    except tk.TclError:
        pass
    win.update_idletasks()
    return win


def run_arb_gui_standalone() -> int:
    root = tk.Tk()
    root.title("R&S SMW200A - ARB catalog signals")
    root.minsize(680, 560)
    ArbSignalsGuiApp(root)
    root.mainloop()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="ARB waveform generator for R&S SMW200A (VsgSmw200a; vsg_config.yaml + env)."
    )
    ap.add_argument("--signal", help="Catalog entry name (see --list).")
    ap.add_argument("--list", action="store_true", help="List catalog entries and exit.")
    ap.add_argument(
        "--license-detail",
        action="store_true",
        help="With --list, show permanent ARB path and trial-native notes per row.",
    )
    ap.add_argument(
        "--gui",
        action="store_true",
        help="Open the Tkinter catalog window (standalone; does not need --signal).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only create the .wv file; do not upload or change the instrument.",
    )
    ap.add_argument(
        "--instr-addr",
        default=None,
        metavar="VISA",
        help="Full VISA resource string (overrides --smw-ip and YAML/env SMW resolution).",
    )
    ap.add_argument(
        "--smw-ip",
        default=None,
        metavar="IP",
        help="SMW IP; resource built with smw_transport / smw_socket_port from vsg_config.yaml (or env). "
        "If omitted with no --instr-addr, uses YAML smw_visa or smw_ip (same as vsg_smw200a).",
    )
    ap.add_argument(
        "--fsw-ip",
        default=None,
        metavar="IP",
        help="FSW43 IP (SCPI TCP). If omitted, uses fsw_ip or fsw43_ip from vsg_config.yaml when set.",
    )
    ap.add_argument("--fsw-port", type=int, default=5025, help="FSW SCPI TCP port (default 5025).")
    ap.add_argument("--fsw-timeout", type=float, default=15.0, help="FSW socket timeout in seconds.")
    ap.add_argument(
        "--fsw-span-hz",
        type=float,
        default=None,
        help="Override spectrum span (Hz); default is derived from waveform sample rate.",
    )
    ap.add_argument(
        "--fsw-ref-db",
        type=float,
        default=None,
        help="FSW reference level (dBm). Default: RF power + margin from catalog.",
    )
    ap.add_argument("--fsw-ch", type=int, default=1, help="FSW measurement channel (default 1).")
    ap.add_argument("--fsw-rbw-hz", type=float, default=None, help="FSW RBW (Hz); omit for AUTO.")
    ap.add_argument("--fsw-vbw-hz", type=float, default=None, help="FSW VBW (Hz); omit for AUTO.")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    if args.gui:
        return run_arb_gui_standalone()
    instr = resolve_instr_addr(args.instr_addr, args.smw_ip)
    fsw_ip_eff = (args.fsw_ip or "").strip() or _fsw_ip_from_vsg_yaml()

    if args.list or not args.signal:
        print("Available signals (this script -> permanent ARB I/Q [P], B9+K515+K527):")
        for k, e in CATALOG.items():
            print(f"  {k:14s} {e.carrier_hz / 1e6:10.3f} MHz  {e.power_dbm:>4} dBm  {e.description}")
            if args.license_detail:
                print(f"       {e.permanent_delivery}")
                if e.trial_native:
                    print(f"       Nativo trial (no usado aqui): {e.trial_native}")
        print(f"\nVSG config file: {config_file_path()}")
        print(f"Instrument address in use: {instr}")
        if fsw_ip_eff:
            print(f"FSW IP (CLI or YAML): {fsw_ip_eff}")
        print("Tip: --license-detail for trial vs permanent notes; --fsw-ip aligns FSW43 spectrum.")
        return 0

    if args.signal not in CATALOG:
        print(f"Unknown signal {args.signal!r}. Use --list.", file=sys.stderr)
        return 1

    try:
        play(
            args.signal,
            dry_run=args.dry_run,
            instr_addr=instr,
            fsw_ip=fsw_ip_eff,
            fsw_port=args.fsw_port,
            fsw_timeout_s=args.fsw_timeout,
            fsw_span_hz=args.fsw_span_hz,
            fsw_ref_dbm=args.fsw_ref_db,
            fsw_ch=args.fsw_ch,
            fsw_rbw_hz=args.fsw_rbw_hz,
            fsw_vbw_hz=args.fsw_vbw_hz,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
