#!/usr/bin/env python3
"""
Minimal SCPI over TCP (port 5025) for R&S instruments — shared by rs_smw_fsw_tcp.py
and smw200a_arb_signals.py so ARB helpers do not import the Tkinter GUI stack.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import Optional


class ScpiTcp:
    """Minimal SCPI client over TCP (R&S default port 5025)."""

    def __init__(self, host: str, port: int = 5025, timeout_s: float = 10.0) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._timeout = timeout_s

    def connect(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self._timeout)
        s.connect((self.host, self.port))
        self._sock = s

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "ScpiTcp":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def write(self, cmd: str) -> None:
        if self._sock is None:
            raise RuntimeError("Socket not connected")
        line = cmd.rstrip() + "\n"
        self._sock.sendall(line.encode("utf-8"))

    def read_raw(self, max_bytes: int = 65536) -> bytes:
        if self._sock is None:
            raise RuntimeError("Socket not connected")
        return self._sock.recv(max_bytes)

    def query(self, cmd: str, delay_s: float = 0.05) -> str:
        self.write(cmd)
        time.sleep(delay_s)
        chunks: list[bytes] = []
        if self._sock is None:
            raise RuntimeError("Socket not connected")
        self._sock.settimeout(self._timeout)
        try:
            while True:
                part = self._sock.recv(4096)
                if not part:
                    break
                chunks.append(part)
                if b"\n" in part:
                    break
        except socket.timeout:
            pass
        raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
        return raw


def opc_wait(dev: ScpiTcp) -> None:
    """Wait until pending operations complete (*OPC?)."""
    dev.query("*OPC?")


def smw_query_idn(
    host: str,
    port: int = 5025,
    timeout_s: float = 10.0,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Query *IDN? on the SMW (or any SCPI device) over TCP ``port`` (default 5025).

    If ``progress`` is set, it is called with short status lines (connect / send / close).
    """
    h = host.strip()
    if not h:
        raise ValueError("SMW host/IP is empty")

    def p(msg: str) -> None:
        if progress is not None:
            progress(msg)

    p(f"Connecting TCP {h}:{port} (timeout {timeout_s:g} s)…")
    with ScpiTcp(h, port, timeout_s=timeout_s) as dev:
        p("Socket open. Sending *IDN? …")
        r = dev.query("*IDN?")
    p("Socket closed.")
    return r


def smw_set_cw(
    dev: ScpiTcp,
    freq_hz: float,
    power_dbm: float,
    rf_on: bool,
    path: int = 1,
) -> None:
    """Configure CW on the given RF path (default 1). Adjust SCPI if firmware differs."""
    p = path
    dev.write(f"SOURce{p}:FREQuency:CW {freq_hz:.12g}")
    dev.write(f"SOURce{p}:POWer:LEVel:IMMediate:AMPLitude {power_dbm:.6g}")
    dev.write(f"OUTPut{p}:STATe {'ON' if rf_on else 'OFF'}")


def fsw_configure_spectrum(
    dev: ScpiTcp,
    center_hz: float,
    span_hz: float,
    ref_level_dbm: float = 0.0,
    channel: int = 1,
    rbw_hz: Optional[float] = None,
    vbw_hz: Optional[float] = None,
    rbw_auto: bool = True,
    vbw_auto: bool = True,
) -> None:
    """
    Configure spectrum analyzer on ``channel`` (typical FSW: SENSe1...).
    RBW/VBW: AUTO when ``rbw_auto``/``vbw_auto`` are True; otherwise value in Hz.
    """
    c = channel
    dev.write(f"SENSe{c}:FREQuency:CENTer {center_hz:.12g}")
    dev.write(f"SENSe{c}:FREQuency:SPAN {span_hz:.12g}")
    if rbw_auto:
        dev.write(f"SENSe{c}:BANDwidth:RESolution:AUTO ON")
    else:
        dev.write(f"SENSe{c}:BANDwidth:RESolution:AUTO OFF")
        if rbw_hz is not None:
            dev.write(f"SENSe{c}:BANDwidth:RESolution {rbw_hz:.12g}")
    if vbw_auto:
        dev.write(f"SENSe{c}:BANDwidth:VIDeo:AUTO ON")
    else:
        dev.write(f"SENSe{c}:BANDwidth:VIDeo:AUTO OFF")
        if vbw_hz is not None:
            dev.write(f"SENSe{c}:BANDwidth:VIDeo {vbw_hz:.12g}")
    dev.write(f"DISPlay:WINDow{c}:TRACe1:Y:SCALe:RLEVel {ref_level_dbm:.6g}")
    dev.write(f"INITiate{c}:CONTinuous ON")
    opc_wait(dev)


def fsw_bw_summary(
    rbw_auto: bool,
    vbw_auto: bool,
    rbw_hz: Optional[float],
    vbw_hz: Optional[float],
) -> str:
    rbw = "AUTO" if rbw_auto else f"{rbw_hz:.12g} Hz"
    vbw = "AUTO" if vbw_auto else f"{vbw_hz:.12g} Hz"
    return f"RBW={rbw}, VBW={vbw}"
