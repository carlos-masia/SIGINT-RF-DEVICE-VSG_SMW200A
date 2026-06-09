#!/usr/bin/env python3
"""
Basic TCP/IP (SCPI) control for R&S SMW200* generator and FSW* analyzer.

Includes **band presets** (VHF/UHF/L/S/C/X/Ku/Ka) to align an SMW CW with the
service center frequency and tune the FSW in spectrum mode (center + span).

Requirements
------------
1. **Network**: PC and instruments on the same LAN (or routable IP). Know the
   **IP addresses** of the SMW and FSW (instrument menu: Setup / Network).

2. **Remote control enabled** on the instruments (SCPI over Ethernet). R&S
   typically uses **TCP port 5025** (VISA: ``TCPIP0::<IP>::5025::SOCKET``).

3. **Firewall**: allow outbound TCP to port 5025 on the PC.

4. **Python 3.10+**, stdlib only (`socket`). Optional: **PyVISA** with the same
   resource string.

5. **SCPI** depends on firmware and options. If a command fails, use the
   instrument **SCPI macro recorder** and adjust ``smw_set_cw()`` /
   ``fsw_configure_spectrum()``.

Examples
--------
  python rs_smw_fsw_tcp.py --smw-ip 192.168.1.10 --fsw-ip 192.168.1.11 --idn-only

  python rs_smw_fsw_tcp.py --list-bands

  python rs_smw_fsw_tcp.py --smw-ip 10.0.0.1 --fsw-ip 10.0.0.2 --band vhf-atc \\
      --power-dbm -10 --rf-on --fsw-span-hz 30e6

  python rs_smw_fsw_tcp.py --gui

  # Windows: console stays visible with --gui; hide with --gui-hide-console
  # No console: pythonw rs_smw_fsw_tcp.py --gui

  GUI button "SMW baseband modulation..." opens rs_smw200_baseband.py in a child window.

  GUI: **Open ARB window (this app)** opens smw200a_arb_signals in a Tk child window;
  **Open ARB in new CMD** starts the CLI in a separate console (Windows).

  Console helper (separate cmd window on Windows):

    python rs_smw_fsw_tcp.py --arb-signals-console
    python rs_smw_fsw_tcp.py --arb-signals-console --smw-ip 192.168.1.10 --fsw-ip 192.168.1.11

  On Windows this opens a **new** console running smw200a_arb_signals.py; on other
  OS the helper runs in the foreground in the current terminal when invoked from
  the CLI (same shell).

  (ES) Ventana del catálogo ARB: **Open ARB window (this app)**. Consola aparte: **Open ARB in new CMD**
  o ``python rs_smw_fsw_tcp.py --arb-signals-console``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from rs_scpi_tcp import ScpiTcp, fsw_bw_summary, fsw_configure_spectrum, opc_wait, smw_set_cw


@dataclass(frozen=True)
class BandApplyParams:
    """Parameters captured on the main thread to apply a band preset."""

    band_id: str
    band_title: str
    smw_ip: str
    fsw_ip: str
    port: int
    timeout: float
    do_smw: bool
    do_fsw: bool
    rf_on: bool
    power_dbm: float
    path: int
    gen_hz: float
    center_hz: float
    span_hz: float
    ref_dbm: float
    fsw_ch: int
    rbw_auto: bool
    vbw_auto: bool
    rbw_hz: Optional[float]
    vbw_hz: Optional[float]


def apply_band_to_instruments(p: BandApplyParams) -> tuple[list[str], list[str]]:
    """Configure SMW and/or FSW from ``BandApplyParams``. Returns SCPI error queues."""
    smw_errs: list[str] = []
    fsw_errs: list[str] = []
    if p.do_smw and p.smw_ip:
        with ScpiTcp(p.smw_ip, p.port, timeout_s=p.timeout) as smw:
            smw_set_cw(smw, p.gen_hz, p.power_dbm, rf_on=p.rf_on, path=p.path)
            smw_errs = collect_scpi_errors(smw)
    if p.do_fsw and p.fsw_ip:
        with ScpiTcp(p.fsw_ip, p.port, timeout_s=p.timeout) as fsw:
            fsw_configure_spectrum(
                fsw,
                center_hz=p.center_hz,
                span_hz=p.span_hz,
                ref_level_dbm=p.ref_dbm,
                channel=p.fsw_ch,
                rbw_hz=p.rbw_hz,
                vbw_hz=p.vbw_hz,
                rbw_auto=p.rbw_auto,
                vbw_auto=p.vbw_auto,
            )
            fsw_errs = collect_scpi_errors(fsw)
    return smw_errs, fsw_errs


def query_idn(dev: ScpiTcp) -> str:
    return dev.query("*IDN?")


def run_idn(dev: ScpiTcp, label: str) -> str:
    r = query_idn(dev)
    print(f"{label} (*IDN?): {r}")
    return r


def collect_scpi_errors(dev: ScpiTcp, max_items: int = 30) -> list[str]:
    """Read SYSTem:ERRor? queue until empty (or max_items)."""
    lines: list[str] = []
    for _ in range(max_items):
        err = dev.query("SYSTem:ERRor?")
        lines.append(err)
        if err.startswith("0,") or err.startswith("+0,"):
            break
    return lines


@dataclass(frozen=True)
class BandPreset:
    """Reference frequency range and operator notes."""

    id: str
    title: str
    notes: str
    f_min_hz: float
    f_max_hz: float

    @property
    def center_hz(self) -> float:
        return 0.5 * (self.f_min_hz + self.f_max_hz)

    @property
    def span_hz(self) -> float:
        return max(self.f_max_hz - self.f_min_hz, 1.0)


# Band presets (test CW at band center unless overridden).
BAND_PRESETS: tuple[BandPreset, ...] = (
    BandPreset(
        "vhf-atc",
        "VHF 108-136 MHz",
        "Civil and military ATC voice.",
        108e6,
        136e6,
    ),
    BandPreset(
        "vhf-maritime-lmr",
        "VHF 136-174 MHz",
        "Maritime (AIS, DSC ch 70/16), LMR, P25, DMR II, NXDN 6.25 kHz, NOAA WX.",
        136e6,
        174e6,
    ),
    BandPreset(
        "uhf-mil-satcom",
        "UHF 225-400 MHz",
        "DoD UHF SATCOM uplinks/downlinks.",
        225e6,
        400e6,
    ),
    BandPreset(
        "uhf-lmr-public",
        "UHF 400-480 MHz",
        "Federal/commercial LMR, DMR/P25, TETRA near 390-395 MHz, EPIRB 406 MHz, GMRS/FRS.",
        400e6,
        480e6,
    ),
    BandPreset(
        "lband-mixed",
        "L-band 960 MHz - 1.85 GHz",
        "GNSS, Inmarsat BGAN, ~1.6 GHz satcom, ADS-B 1090 MHz, Link-16, US DoD L-band 1755-1850 MHz.",
        960e6,
        1.85e9,
    ),
    BandPreset(
        "s-ttc-ism",
        "S-band 2.0-2.4 GHz",
        "TT&C uplink 2.0-2.1 GHz, ISM telemetry ~2.4 GHz.",
        2.0e9,
        2.4e9,
    ),
    BandPreset(
        "s-maritime-radar",
        "S-band 2.9-3.1 GHz",
        "Maritime radar.",
        2.9e9,
        3.1e9,
    ),
    BandPreset(
        "c-airborne-satcom",
        "C-band 4.0-8.0 GHz (nominal)",
        "Airborne radar ~5.4 GHz, satcom uplink ~5.8 GHz, data links 4.4-5.0 GHz.",
        4.0e9,
        8.0e9,
    ),
    BandPreset(
        "x-maritime-airborne",
        "X-band 7.9-10.6 GHz",
        "Maritime/airborne radar 9.3-9.6 GHz, X-band satcom 7.9-8.4 GHz, payload downlinks.",
        7.9e9,
        10.6e9,
    ),
    BandPreset(
        "ku-ka-wide",
        "Ku/Ka wide 13.7-31 GHz",
        "SATCOM downlinks; very wide span — consider manual FSW span and RBW/sweep time.",
        13.7e9,
        31.0e9,
    ),
    BandPreset(
        "ku-downlink",
        "Ku downlink 10.7-12.75 GHz",
        "Typical Ku downlink.",
        10.7e9,
        12.75e9,
    ),
    BandPreset(
        "ku-uplink",
        "Ku uplink 13.75-14.5 GHz",
        "Typical Ku uplink.",
        13.75e9,
        14.5e9,
    ),
    BandPreset(
        "ka-downlink",
        "Ka downlink 17.7-21.2 GHz",
        "Ka downlink.",
        17.7e9,
        21.2e9,
    ),
    BandPreset(
        "ka-uplink",
        "Ka uplink 27.5-31.0 GHz",
        "Ka uplink.",
        27.5e9,
        31.0e9,
    ),
)

_PRESET_BY_ID = {b.id: b for b in BAND_PRESETS}


def _fmt_freq_range_hz(lo: float, hi: float) -> str:
    """Human-readable MHz/GHz range string."""
    if hi <= 3e9:
        return f"{lo / 1e6:.3f} - {hi / 1e6:.3f} MHz"
    if lo >= 1e9:
        return f"{lo / 1e9:.4f} - {hi / 1e9:.4f} GHz"
    return f"{lo / 1e6:.3f} MHz - {hi / 1e9:.4f} GHz"


def list_bands() -> None:
    for b in BAND_PRESETS:
        rng = _fmt_freq_range_hz(b.f_min_hz, b.f_max_hz)
        print(f"  {b.id:18}  {rng:28}  {b.title}")
        if b.notes:
            print(f"                      {b.notes}")
        print()


def band_span_with_margin(b: BandPreset, margin: float) -> float:
    """Expand span by margin fraction (e.g. 0.05 adds 5% on each side conceptually)."""
    raw = b.span_hz
    return raw * (1.0 + 2.0 * max(margin, 0.0))


def resolve_fsw_span_hz(
    band: BandPreset,
    span_margin: float,
    manual_span_hz: Optional[float],
) -> float:
    """FSW span: manual value if set, else preset range × margin."""
    if manual_span_hz is not None:
        return manual_span_hz
    return band_span_with_margin(band, span_margin)


def _import_baseband_module():
    """Import rs_smw200_baseband.py from the same directory as this script."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import rs_smw200_baseband as bb  # noqa: WPS433 — local dynamic import

    return bb


def _import_arb_signals_module():
    """Import smw200a_arb_signals.py from the same directory as this script."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import smw200a_arb_signals as arb  # noqa: WPS433 — local dynamic import

    return arb


def arb_signals_script_path() -> Path:
    """Path to smw200a_arb_signals.py next to this script."""
    return Path(__file__).resolve().parent / "smw200a_arb_signals.py"


def _python_exe_for_new_console() -> str:
    """
    On Windows, if this app was started with pythonw.exe, child processes would
    inherit pythonw and show no console; use python.exe for smw200a_arb_signals.
    """
    exe = sys.executable
    if sys.platform == "win32":
        el = exe.lower()
        if el.endswith("pythonw.exe"):
            return exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def launch_arb_signals_console(
    smw_ip: str = "",
    fsw_ip: str = "",
    *,
    wait_on_non_windows_cli: bool = True,
) -> int:
    """
    Start smw200a_arb_signals.py: new console on Windows; on other platforms,
    if ``wait_on_non_windows_cli`` is True, run in the foreground in this process's
    terminal (blocking). If False, start detached (typical from GUI on Linux/macOS).
    """
    script = arb_signals_script_path()
    if not script.is_file():
        print(f"smw200a_arb_signals.py not found (expected next to this file):\n  {script}")
        return 1
    workdir = str(script.resolve().parent)
    cmd = [_python_exe_for_new_console(), str(script)]
    if smw_ip.strip():
        cmd += ["--smw-ip", smw_ip.strip()]
    if fsw_ip.strip():
        cmd += ["--fsw-ip", fsw_ip.strip()]
    popen_kw: dict = {"cwd": workdir}
    try:
        if sys.platform == "win32":
            popen_kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(cmd, **popen_kw)
            print("Started smw200a_arb_signals.py in a new console window.")
            print("Example: python smw200a_arb_signals.py --list --smw-ip <IP>")
            return 0
        if wait_on_non_windows_cli:
            return subprocess.call(cmd, cwd=workdir)
        subprocess.Popen(cmd, start_new_session=True, cwd=workdir)
        print(f"Started in background: {' '.join(cmd)}")
        return 0
    except OSError as e:
        print(f"Failed to start smw200a_arb_signals.py: {e}")
        return 1


def interactive_pick_band() -> BandPreset:
    items = list(BAND_PRESETS)
    print("\nAvailable presets:\n")
    for i, b in enumerate(items, start=1):
        print(f"  [{i:2}] {b.id}")
        print(f"       {b.title}  ({b.f_min_hz/1e6:.3f} - {b.f_max_hz/1e9:.4f} GHz)")
        print()
    while True:
        s = input("Band number (1-{}), or id (e.g. vhf-atc): ".format(len(items))).strip()
        if not s:
            continue
        if s.isdigit():
            n = int(s)
            if 1 <= n <= len(items):
                return items[n - 1]
            print("Out of range.")
            continue
        b = _PRESET_BY_ID.get(s.lower())
        if b is not None:
            return b
        print("Unknown id. Use --list-bands to see the list.")


def _hide_windows_console_if_allocated() -> None:
    """On Windows, hide the console window if one exists (e.g. python.exe)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            SW_HIDE = 0
            ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass


def run_gui(cli_args: argparse.Namespace) -> int:
    """Tkinter window to pick a band and apply SMW + FSW."""
    if getattr(cli_args, "gui_hide_console", False):
        _hide_windows_console_if_allocated()
    root = tk.Tk()
    root.title("R&S SMW200 + FSW43 — bands / SCPI (TCP)")
    root.minsize(780, 820)
    SmwFswGuiApp(root, cli_args)
    root.mainloop()
    return 0


class SmwFswGuiApp:
    def __init__(self, root: tk.Tk, cli_args: argparse.Namespace) -> None:
        self.root = root
        self._busy = False

        main = ttk.Frame(root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(6, weight=2)

        conn = ttk.LabelFrame(main, text="Connection (SCPI socket, port 5025)", padding=6)
        conn.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        conn.columnconfigure(1, weight=1)
        conn.columnconfigure(3, weight=1)

        ttk.Label(conn, text="IP SMW:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.ent_smw_ip = ttk.Entry(conn, width=22)
        self.ent_smw_ip.insert(0, getattr(cli_args, "smw_ip", "") or "")
        self.ent_smw_ip.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(conn, text="IP FSW43:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.ent_fsw_ip = ttk.Entry(conn, width=22)
        self.ent_fsw_ip.insert(0, getattr(cli_args, "fsw_ip", "") or "")
        self.ent_fsw_ip.grid(row=0, column=3, sticky="ew")

        ttk.Label(conn, text="Port:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_port = ttk.Entry(conn, width=8)
        self.ent_port.insert(0, str(cli_args.port or 5025))
        self.ent_port.grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(conn, text="Timeout (s):").grid(row=1, column=2, sticky="w", pady=(6, 0), padx=(8, 4))
        self.ent_timeout = ttk.Entry(conn, width=8)
        self.ent_timeout.insert(0, "15")
        self.ent_timeout.grid(row=1, column=3, sticky="w", pady=(6, 0))

        bf = ttk.Frame(conn)
        bf.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(bf, text="*IDN? SMW", command=self._idn_smw).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bf, text="*IDN? FSW", command=self._idn_fsw).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bf, text="SMW errors", command=lambda: self._errors_ip("smw")).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(bf, text="FSW errors", command=lambda: self._errors_ip("fsw")).pack(side=tk.LEFT)
        ttk.Button(
            bf,
            text="SMW baseband modulation...",
            command=self._open_baseband_gui,
        ).pack(side=tk.LEFT, padx=(12, 0))

        arb_row = ttk.Frame(conn)
        arb_row.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(arb_row, text="ARB waveforms:").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            arb_row,
            text="Open ARB window (this app)",
            command=self._open_arb_signals_gui,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            arb_row,
            text="Open ARB in new CMD",
            command=self._open_arb_signals_console,
        ).pack(side=tk.LEFT, padx=(0, 0))

        self._bb_window: Optional[tk.Toplevel] = None
        self._arb_window: Optional[tk.Toplevel] = None

        bands = ttk.LabelFrame(main, text="Signals / bands to analyze (preset)", padding=6)
        bands.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        bands.rowconfigure(0, weight=1)
        bands.columnconfigure(0, weight=1)

        lb_frame = ttk.Frame(bands)
        lb_frame.grid(row=0, column=0, sticky="nsew")
        lb_frame.rowconfigure(0, weight=1)
        lb_frame.columnconfigure(0, weight=1)

        scroll = ttk.Scrollbar(lb_frame)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list_bands = tk.Listbox(
            lb_frame,
            height=14,
            exportselection=False,
            yscrollcommand=scroll.set,
        )
        self.list_bands.grid(row=0, column=0, sticky="nsew")
        scroll.config(command=self.list_bands.yview)

        for b in BAND_PRESETS:
            rng = _fmt_freq_range_hz(b.f_min_hz, b.f_max_hz)
            self.list_bands.insert(tk.END, f"{b.title}  |  {rng}")
        self.list_bands.select_set(0)

        ttk.Label(bands, text="Preset description:").grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.txt_notes = scrolledtext.ScrolledText(bands, height=5, wrap=tk.WORD, state=tk.DISABLED)
        self.txt_notes.grid(row=2, column=0, sticky="ew")

        opts = ttk.LabelFrame(main, text="Parameters when applying band", padding=6)
        opts.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        ttk.Label(opts, text="SMW power (dBm):").grid(row=0, column=0, sticky="w")
        self.ent_pow = ttk.Entry(opts, width=10)
        self.ent_pow.insert(0, "-10")
        self.ent_pow.grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(opts, text="FSW span margin (0-1):").grid(row=0, column=2, sticky="w", padx=(12, 4))
        self.ent_span_m = ttk.Entry(opts, width=8)
        self.ent_span_m.insert(0, "0.05")
        self.ent_span_m.grid(row=0, column=3, sticky="w")

        ttk.Label(opts, text="FSW ref. level (dBm):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_ref = ttk.Entry(opts, width=10)
        self.ent_ref.insert(0, "10")
        self.ent_ref.grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))

        ttk.Label(opts, text="Path SMW:").grid(row=1, column=2, sticky="w", padx=(12, 4), pady=(6, 0))
        self.ent_path = ttk.Entry(opts, width=4)
        self.ent_path.insert(0, "1")
        self.ent_path.grid(row=1, column=3, sticky="w", pady=(6, 0))

        ttk.Label(opts, text="FSW ch:").grid(row=1, column=4, sticky="w", padx=(12, 4), pady=(6, 0))
        self.ent_fsw_ch = ttk.Entry(opts, width=4)
        self.ent_fsw_ch.insert(0, "1")
        self.ent_fsw_ch.grid(row=1, column=5, sticky="w", pady=(6, 0))

        ttk.Label(opts, text="SMW CW freq (Hz, empty=preset center):").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        self.ent_gen_hz = ttk.Entry(opts, width=20)
        self.ent_gen_hz.grid(row=2, column=2, sticky="w", padx=4, pady=(6, 0))

        ttk.Label(opts, text="FSW center (Hz, empty=preset):").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        self.ent_fsw_cent = ttk.Entry(opts, width=22)
        self.ent_fsw_cent.grid(row=3, column=2, columnspan=2, sticky="w", padx=4, pady=(6, 0))

        ttk.Label(opts, text="FSW span (Hz, empty=preset+margin):").grid(
            row=3, column=4, sticky="e", padx=(8, 4), pady=(6, 0)
        )
        self.ent_fsw_span = ttk.Entry(opts, width=22)
        self.ent_fsw_span.grid(row=3, column=5, sticky="w", pady=(6, 0))

        ttk.Label(opts, text="RBW (Hz):").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.ent_rbw = ttk.Entry(opts, width=12)
        self.ent_rbw.insert(0, "10000")
        self.ent_rbw.grid(row=4, column=1, sticky="w", padx=4, pady=(6, 0))
        self.var_rbw_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="RBW AUTO", variable=self.var_rbw_auto).grid(
            row=4, column=2, sticky="w", padx=(4, 12), pady=(6, 0)
        )

        ttk.Label(opts, text="VBW (Hz):").grid(row=4, column=3, sticky="w", pady=(6, 0))
        self.ent_vbw = ttk.Entry(opts, width=12)
        self.ent_vbw.insert(0, "10000")
        self.ent_vbw.grid(row=4, column=4, sticky="w", padx=4, pady=(6, 0))
        self.var_vbw_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="VBW AUTO", variable=self.var_vbw_auto).grid(
            row=4, column=5, sticky="w", pady=(6, 0)
        )

        cf = ttk.Frame(opts)
        cf.grid(row=5, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.var_cfg_smw = tk.BooleanVar(value=True)
        self.var_cfg_fsw = tk.BooleanVar(value=True)
        self.var_rf_on = tk.BooleanVar(value=True)
        self.var_auto_on_select = tk.BooleanVar(value=True)
        ttk.Checkbutton(cf, text="Configure SMW (CW)", variable=self.var_cfg_smw).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Checkbutton(cf, text="Configure FSW (spectrum)", variable=self.var_cfg_fsw).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Checkbutton(cf, text="SMW RF on when applying", variable=self.var_rf_on).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Checkbutton(
            cf,
            text="Configure instruments on band selection",
            variable=self.var_auto_on_select,
        ).pack(side=tk.LEFT)

        self._select_apply_job: Optional[str] = None

        af = ttk.Frame(main)
        af.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(af, text="Apply / re-apply band", command=self._apply_band).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(af, text="SMW RF ON", command=lambda: self._rf_toggle(True)).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(af, text="SMW RF OFF", command=lambda: self._rf_toggle(False)).pack(side=tk.LEFT)

        manual = ttk.LabelFrame(main, text="Manual CW (SMW only)", padding=6)
        manual.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(manual, text="Frequency (Hz):").pack(side=tk.LEFT)
        self.ent_man_f = ttk.Entry(manual, width=22)
        self.ent_man_f.insert(0, "1e9")
        self.ent_man_f.pack(side=tk.LEFT, padx=6)
        ttk.Label(manual, text="dBm:").pack(side=tk.LEFT)
        self.ent_man_p = ttk.Entry(manual, width=8)
        self.ent_man_p.insert(0, "-10")
        self.ent_man_p.pack(side=tk.LEFT, padx=4)
        ttk.Button(manual, text="Apply manual CW", command=self._apply_manual_cw).pack(
            side=tk.LEFT, padx=(12, 0)
        )

        ttk.Label(main, text="Log:").grid(row=5, column=0, sticky="w")
        self.log = scrolledtext.ScrolledText(main, height=12, state=tk.NORMAL, wrap=tk.WORD)
        self.log.grid(row=6, column=0, sticky="nsew", pady=(2, 0))

        self.list_bands.bind("<<ListboxSelect>>", self._on_band_select)
        self._on_band_select(apply_instruments=False)
        self._log(
            "Ready. Enter IPs; selecting a band configures SMW and FSW when "
            "'Configure instruments on band selection' is enabled."
        )

    def _log(self, msg: str) -> None:
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _port_timeout(self) -> tuple[int, float]:
        port = int(self.ent_port.get().strip())
        timeout = float(self.ent_timeout.get().strip())
        return port, timeout

    def _selected_preset(self) -> Optional[BandPreset]:
        sel = self.list_bands.curselection()
        if not sel:
            return None
        i = int(sel[0])
        if 0 <= i < len(BAND_PRESETS):
            return BAND_PRESETS[i]
        return None

    def _open_baseband_gui(self) -> None:
        """Open rs_smw200_baseband in a child window with SMW settings from this GUI."""
        smw_ip = self.ent_smw_ip.get().strip()
        if not smw_ip:
            messagebox.showwarning(
                "SMW",
                "Enter the SMW IP before opening baseband modulation.",
            )
            return
        if self._bb_window is not None:
            try:
                if self._bb_window.winfo_exists():
                    self._bb_window.lift()
                    self._bb_window.focus_force()
                    return
            except tk.TclError:
                self._bb_window = None
        try:
            port = int(self.ent_port.get().strip())
            path = int(self.ent_path.get().strip())
            power = float(self.ent_pow.get().strip())
        except ValueError as e:
            messagebox.showerror("Parameters", f"Invalid port, path, or power: {e}")
            return
        freq: Optional[float] = None
        gen_s = self.ent_gen_hz.get().strip()
        if gen_s:
            try:
                freq = float(gen_s)
            except ValueError:
                messagebox.showerror("Parameters", "Invalid SMW CW frequency (Hz).")
                return
        if freq is None:
            band = self._selected_preset()
            if band is not None:
                freq = band.center_hz
        try:
            bb = _import_baseband_module()
            self._bb_window = bb.open_baseband_gui(
                self.root,
                ip=smw_ip,
                port=port,
                path=path,
                freq_hz=freq,
                power_dbm=power,
                rf_on=bool(self.var_rf_on.get()),
            )
        except ImportError as e:
            messagebox.showerror(
                "Baseband",
                "Could not load rs_smw200_baseband.py\n"
                f"(must be next to rs_smw_fsw_tcp.py).\n\n{e}",
            )
            return
        except Exception as e:
            messagebox.showerror("Baseband", str(e))
            return
        self._log(
            f"Baseband window opened (SMW {smw_ip}, path {path}, "
            f"{'RF ON' if self.var_rf_on.get() else 'RF OFF'})."
        )

    def _open_arb_signals_gui(self) -> None:
        """Open smw200a_arb_signals Tkinter window (child of this GUI)."""
        if self._arb_window is not None:
            try:
                if self._arb_window.winfo_exists():
                    self._arb_window.lift()
                    self._arb_window.focus_force()
                    self._log("ARB window brought to front (already open).")
                    return
            except tk.TclError:
                self._arb_window = None
        script_path = arb_signals_script_path()
        if not script_path.is_file():
            messagebox.showerror(
                "ARB catalog",
                "smw200a_arb_signals.py was not found next to rs_smw_fsw_tcp.py:\n"
                f"{script_path}",
            )
            return
        try:
            port = int(self.ent_port.get().strip())
            timeout = float(self.ent_timeout.get().strip())
        except ValueError as e:
            messagebox.showerror("Parameters", f"Invalid port or timeout: {e}")
            return
        try:
            arb = _import_arb_signals_module()
            self._arb_window = arb.open_arb_signals_gui(
                self.root,
                smw_ip=self.ent_smw_ip.get().strip(),
                fsw_ip=self.ent_fsw_ip.get().strip(),
                fsw_port=port,
                fsw_timeout=timeout,
            )

            def _on_arb_destroy(evt: object) -> None:
                w = getattr(evt, "widget", None)
                if w is self._arb_window:
                    self._arb_window = None

            self._arb_window.bind("<Destroy>", _on_arb_destroy)
        except ImportError as e:
            messagebox.showerror(
                "ARB catalog",
                "Could not import smw200a_arb_signals (missing file, numpy, tkinter, or rs_scpi_tcp).\n"
                f"Expected: {script_path}\n\n{e}",
            )
            return
        except Exception as e:
            messagebox.showerror("ARB catalog", str(e))
            return
        self._log(
            "ARB catalog GUI opened in this app (child window). "
            "If you do not see it, check behind other windows or the taskbar."
        )

    def _open_arb_signals_console(self) -> None:
        """Open smw200a_arb_signals.py in a new console (Windows) or background process."""
        smw_ip = self.ent_smw_ip.get().strip()
        fsw_ip = self.ent_fsw_ip.get().strip()
        if not smw_ip:
            if not messagebox.askokcancel(
                "SMW IP",
                "SMW IP is empty: the new console will open without --smw-ip.\n"
                "You can pass the address there (e.g. --smw-ip 192.168.1.10) or fill "
                "IP SMW here and click again to pre-fill.\n\nOpen console anyway?",
            ):
                return
        code = launch_arb_signals_console(
            smw_ip,
            fsw_ip,
            wait_on_non_windows_cli=False,
        )
        if code != 0:
            messagebox.showerror(
                "ARB catalog",
                f"Could not start smw200a_arb_signals.py (exit {code}).\n"
                f"Expected file:\n{arb_signals_script_path()}",
            )
            return
        extra_smw = f"SMW IP {smw_ip}" if smw_ip else "SMW IP (not passed; set in console)"
        extra_fsw = f", FSW IP {fsw_ip}" if fsw_ip else ""
        self._log(
            f"ARB external CMD started ({extra_smw}{extra_fsw}). "
            "For an in-app window use 'Open ARB window (this app)'."
        )

    def _on_band_select(self, _event: object = None, apply_instruments: bool = True) -> None:
        b = self._selected_preset()
        self.txt_notes.config(state=tk.NORMAL)
        self.txt_notes.delete("1.0", tk.END)
        if b:
            self.txt_notes.insert(tk.END, f"ID: {b.id}\n\n{b.notes}")
        self.txt_notes.config(state=tk.DISABLED)
        if apply_instruments and self.var_auto_on_select.get() and b is not None:
            self._schedule_apply_on_select()

    def _schedule_apply_on_select(self) -> None:
        job = self._select_apply_job
        if job is not None:
            self.root.after_cancel(job)
        self._select_apply_job = self.root.after(400, self._apply_band_on_select)

    def _apply_band_on_select(self) -> None:
        self._select_apply_job = None
        self._apply_band(from_selection=True)

    def _collect_band_params(
        self, band: BandPreset, from_selection: bool
    ) -> Optional[BandApplyParams]:
        smw_ip = self.ent_smw_ip.get().strip()
        fsw_ip = self.ent_fsw_ip.get().strip()
        do_smw = bool(self.var_cfg_smw.get())
        do_fsw = bool(self.var_cfg_fsw.get())
        if do_smw and not smw_ip:
            if not from_selection:
                messagebox.showwarning("SMW", "Enter SMW IP or uncheck Configure SMW.")
            return None
        if do_fsw and not fsw_ip:
            if not from_selection:
                messagebox.showwarning("FSW", "Enter FSW IP or uncheck Configure FSW.")
            return None
        if not do_smw and not do_fsw:
            if not from_selection:
                messagebox.showwarning("Band", "Enable at least Configure SMW or Configure FSW.")
            return None
        try:
            port, timeout = self._port_timeout()
            power = float(self.ent_pow.get().strip())
            span_m = float(self.ent_span_m.get().strip())
            ref = float(self.ent_ref.get().strip())
            path = int(self.ent_path.get().strip())
            fsw_ch = int(self.ent_fsw_ch.get().strip())
            gen_s = self.ent_gen_hz.get().strip()
            gen_hz = float(gen_s) if gen_s else band.center_hz
            fc_s = self.ent_fsw_cent.get().strip()
            center = float(fc_s) if fc_s else band.center_hz
            fsp_s = self.ent_fsw_span.get().strip()
            manual_span = float(fsp_s) if fsp_s else None
            rbw_auto = bool(self.var_rbw_auto.get())
            vbw_auto = bool(self.var_vbw_auto.get())
            rbw_hz = float(self.ent_rbw.get().strip()) if not rbw_auto else None
            vbw_hz = float(self.ent_vbw.get().strip()) if not vbw_auto else None
        except ValueError as e:
            if not from_selection:
                messagebox.showerror("Parameters", f"Check numeric fields (port, power, RBW, span, etc.): {e}")
            return None

        return BandApplyParams(
            band_id=band.id,
            band_title=band.title,
            smw_ip=smw_ip,
            fsw_ip=fsw_ip,
            port=port,
            timeout=timeout,
            do_smw=do_smw,
            do_fsw=do_fsw,
            rf_on=bool(self.var_rf_on.get()),
            power_dbm=power,
            path=path,
            gen_hz=gen_hz,
            center_hz=center,
            span_hz=resolve_fsw_span_hz(band, span_m, manual_span),
            ref_dbm=ref,
            fsw_ch=fsw_ch,
            rbw_auto=rbw_auto,
            vbw_auto=vbw_auto,
            rbw_hz=rbw_hz,
            vbw_hz=vbw_hz,
        )

    def _run_async(self, title: str, fn) -> None:
        """
        Run network work on a background thread. Tkinter must only be touched
        from the main thread; ``fn`` must use captured data, not widgets.
        """
        if self._busy:
            messagebox.showinfo("Busy", "A network operation is in progress; please wait.")
            return

        def target() -> None:
            try:
                fn()
            except OSError as e:
                err = str(e)
                self.root.after(0, lambda m=err: messagebox.showerror(title, f"Network / socket: {m}"))
                self.root.after(0, lambda m=err: self._log(f"[{title}] NETWORK ERROR: {m}"))
            except ValueError as e:
                err = str(e)
                self.root.after(0, lambda m=err: messagebox.showerror(title, m))
                self.root.after(0, lambda m=err: self._log(f"[{title}] ERROR: {m}"))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda m=err: messagebox.showerror(title, m))
                self.root.after(0, lambda m=err: self._log(f"[{title}] ERROR: {m}"))
            finally:
                self.root.after(0, lambda: setattr(self, "_busy", False))

        self._busy = True
        threading.Thread(target=target, daemon=True).start()

    def _idn_smw(self) -> None:
        ip = self.ent_smw_ip.get().strip()
        if not ip:
            messagebox.showwarning("SMW", "Enter the SMW IP.")
            return
        try:
            port, timeout = self._port_timeout()
        except ValueError as e:
            messagebox.showerror("Parameters", str(e))
            return

        def work() -> None:
            with ScpiTcp(ip, port, timeout_s=timeout) as dev:
                r = query_idn(dev)
            self.root.after(0, lambda res=r: self._log(f"SMW *IDN?: {res}"))

        self._run_async("IDN SMW", work)

    def _idn_fsw(self) -> None:
        ip = self.ent_fsw_ip.get().strip()
        if not ip:
            messagebox.showwarning("FSW", "Enter the FSW IP.")
            return
        try:
            port, timeout = self._port_timeout()
        except ValueError as e:
            messagebox.showerror("Parameters", str(e))
            return

        def work() -> None:
            with ScpiTcp(ip, port, timeout_s=timeout) as dev:
                r = query_idn(dev)
            self.root.after(0, lambda res=r: self._log(f"FSW *IDN?: {res}"))

        self._run_async("IDN FSW", work)

    def _errors_ip(self, which: str) -> None:
        if which == "smw":
            ip = self.ent_smw_ip.get().strip()
            label = "SMW"
        else:
            ip = self.ent_fsw_ip.get().strip()
            label = "FSW"
        if not ip:
            messagebox.showwarning(label, f"Enter the {label} IP.")
            return
        try:
            port, timeout = self._port_timeout()
        except ValueError as e:
            messagebox.showerror("Parameters", str(e))
            return

        def work() -> None:
            with ScpiTcp(ip, port, timeout_s=timeout) as dev:
                lines = collect_scpi_errors(dev)

            def show(ls: list[str] = lines, lab: str = label) -> None:
                self._log(f"{lab} error queue:")
                for ln in ls:
                    self._log(f"  {ln}")

            self.root.after(0, show)

        self._run_async(f"{label} errors", work)

    def _apply_band(self, from_selection: bool = False) -> None:
        band = self._selected_preset()
        if band is None:
            if not from_selection:
                messagebox.showwarning("Band", "Select a band from the list.")
            return
        params = self._collect_band_params(band, from_selection=from_selection)
        if params is None:
            if from_selection:
                return
            return

        def work() -> None:
            smw_errs, fsw_errs = apply_band_to_instruments(params)
            if params.do_smw:
                msg_smw = (
                    f"SMW: CW {params.gen_hz:.12g} Hz, {params.power_dbm} dBm, "
                    f"OUT={'ON' if params.rf_on else 'OFF'}, path={params.path}"
                )
                self.root.after(0, lambda m=msg_smw: self._log(m))
                for line in smw_errs:
                    self.root.after(0, lambda ln=line: self._log(f"  SMW SYST:ERR? {ln}"))
            if params.do_fsw:
                bw = fsw_bw_summary(
                    params.rbw_auto, params.vbw_auto, params.rbw_hz, params.vbw_hz
                )
                msg_fsw = (
                    f"FSW: CENT {params.center_hz:.12g} Hz, SPAN {params.span_hz:.12g} Hz, "
                    f"RLEV {params.ref_dbm} dBm, ch={params.fsw_ch}, {bw}"
                )
                self.root.after(0, lambda m=msg_fsw: self._log(m))
                for line in fsw_errs:
                    self.root.after(0, lambda ln=line: self._log(f"  FSW SYST:ERR? {ln}"))
            prefix = "Band selected" if from_selection else "Preset applied"
            done = f"{prefix}: {params.band_id} — {params.band_title}"
            self.root.after(0, lambda m=done: self._log(m))

        title = "Configure band" if from_selection else "Apply band"
        self._run_async(title, work)

    def _rf_toggle(self, on: bool) -> None:
        ip = self.ent_smw_ip.get().strip()
        if not ip:
            messagebox.showwarning("SMW", "Enter the SMW IP.")
            return
        try:
            port, timeout = self._port_timeout()
            path = int(self.ent_path.get().strip())
        except ValueError as e:
            messagebox.showerror("Parameters", str(e))
            return
        state = "ON" if on else "OFF"

        def work() -> None:
            with ScpiTcp(ip, port, timeout_s=timeout) as smw:
                smw.write(f"OUTPut{path}:STATe {'ON' if on else 'OFF'}")
                opc_wait(smw)
                collect_scpi_errors(smw)
            msg = f"SMW: OUTPut{path} -> {state}"
            self.root.after(0, lambda m=msg: self._log(m))

        self._run_async("RF SMW", work)

    def _apply_manual_cw(self) -> None:
        ip = self.ent_smw_ip.get().strip()
        if not ip:
            messagebox.showwarning("SMW", "Enter the SMW IP.")
            return
        try:
            port, timeout = self._port_timeout()
            freq = float(self.ent_man_f.get().strip())
            power = float(self.ent_man_p.get().strip())
            path = int(self.ent_path.get().strip())
        except ValueError as e:
            messagebox.showerror("Parameters", str(e))
            return

        def work() -> None:
            with ScpiTcp(ip, port, timeout_s=timeout) as smw:
                smw_set_cw(smw, freq, power, rf_on=True, path=path)
                collect_scpi_errors(smw)
            msg = f"SMW manual CW: {freq} Hz, {power} dBm, path={path}, RF ON"
            self.root.after(0, lambda m=msg: self._log(m))

        self._run_async("Manual CW", work)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SCPI over TCP (5025) for SMW and FSW — CW per band and FSW spectrum tuning."
    )
    parser.add_argument("--smw-ip", default="", help="SMW generator IP")
    parser.add_argument("--fsw-ip", default="", help="FSW analyzer IP (optional)")
    parser.add_argument("--port", type=int, default=5025, help="SCPI TCP port (default 5025)")
    parser.add_argument("--freq-hz", type=float, default=None, help="Manual CW frequency in Hz (without --band)")
    parser.add_argument("--power-dbm", type=float, default=None, help="SMW level in dBm")
    parser.add_argument("--rf-on", action="store_true", help="Turn SMW RF output on")
    parser.add_argument("--rf-off", action="store_true", help="Turn SMW RF output off")
    parser.add_argument("--path", type=int, default=1, help="SMW RF path index (1 or 2, etc.)")
    parser.add_argument("--fsw-ch", type=int, default=1, help="FSW measurement channel (default 1)")
    parser.add_argument("--idn-only", action="store_true", help="Query *IDN? only and exit")
    parser.add_argument(
        "--list-bands",
        action="store_true",
        help="List band presets and exit",
    )
    parser.add_argument(
        "--band",
        default="",
        metavar="ID",
        help="Band preset (see --list-bands). Sets SMW center and FSW center+span.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pick band from interactive menu (requires stdin; use with IPs).",
    )
    parser.add_argument(
        "--span-margin",
        type=float,
        default=0.05,
        help="Extra FSW span fraction vs preset range (default 0.05 = 5%%). Ignored if --fsw-span-hz is set.",
    )
    parser.add_argument(
        "--fsw-span-hz",
        type=float,
        default=None,
        help="Manual FSW span in Hz (overrides preset range and --span-margin).",
    )
    parser.add_argument(
        "--gen-freq-hz",
        type=float,
        default=None,
        help="Override SMW frequency (Hz) with --band; default = preset center.",
    )
    parser.add_argument(
        "--fsw-center-hz",
        type=float,
        default=None,
        help="Override FSW center (Hz) with --band.",
    )
    parser.add_argument(
        "--fsw-ref-db",
        type=float,
        default=10.0,
        help="FSW display reference level in dBm (default 10).",
    )
    parser.add_argument(
        "--rbw-hz",
        type=float,
        default=None,
        help="FSW RBW in Hz (if set, disables RBW AUTO).",
    )
    parser.add_argument(
        "--vbw-hz",
        type=float,
        default=None,
        help="FSW VBW in Hz (if set, disables VBW AUTO).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open tkinter GUI for IPs, bands, and SMW/FSW apply.",
    )
    parser.add_argument(
        "--gui-hide-console",
        action="store_true",
        help="With --gui, hide the console window on Windows (console visible by default).",
    )
    parser.add_argument(
        "--arb-signals-console",
        action="store_true",
        help=(
            "Open smw200a_arb_signals.py (ARB waveform catalog via RsSmw). "
            "New console on Windows; passes --smw-ip / --fsw-ip if set. Does not require both IPs."
        ),
    )
    args = parser.parse_args()

    if args.arb_signals_console:
        return launch_arb_signals_console(
            args.smw_ip or "",
            args.fsw_ip or "",
            wait_on_non_windows_cli=sys.platform != "win32",
        )

    if args.gui:
        return run_gui(args)

    if args.list_bands:
        list_bands()
        return 0

    if not args.smw_ip and not args.fsw_ip:
        print("Specify at least --smw-ip or --fsw-ip, or use --list-bands.")
        return 1

    if args.interactive and not args.idn_only:
        if not args.smw_ip or not args.fsw_ip:
            print("--interactive requires --smw-ip and --fsw-ip.")
            return 1
        if not sys.stdin.isatty():
            print("--interactive requires an interactive console.")
            return 1

    band: Optional[BandPreset] = None
    if args.interactive and not args.idn_only:
        band = interactive_pick_band()
        print(f"Selected: {band.id} — {band.title}\n")
    elif args.band:
        band = _PRESET_BY_ID.get(args.band.lower())
        if band is None:
            print(f"Unknown preset: {args.band!r}. Use --list-bands.")
            return 1

    if band is not None:
        if not args.smw_ip or not args.fsw_ip:
            print("--band or --interactive requires --smw-ip and --fsw-ip.")
            return 1
        if args.power_dbm is None:
            print("With --band / --interactive specify --power-dbm (SMW CW level).")
            return 1

    if args.smw_ip:
        try:
            with ScpiTcp(args.smw_ip, args.port) as smw:
                run_idn(smw, "SMW")
                if args.idn_only:
                    pass
                elif band is not None:
                    gen_f = args.gen_freq_hz if args.gen_freq_hz is not None else band.center_hz
                    rf = False if args.rf_off else True
                    smw_set_cw(smw, gen_f, args.power_dbm, rf_on=rf, path=args.path)
                    print(
                        f"SMW: CW {gen_f:.12g} Hz ({gen_f/1e9:.6f} GHz), {args.power_dbm:.3g} dBm, "
                        f"OUTPut={'ON' if rf else 'OFF'}, path={args.path}"
                    )
                else:
                    if args.freq_hz is not None or args.power_dbm is not None:
                        if args.freq_hz is None or args.power_dbm is None:
                            print("Manual CW requires --freq-hz and --power-dbm together.")
                            return 1
                        if args.rf_off:
                            rf = False
                        elif args.rf_on:
                            rf = True
                        else:
                            rf = True
                        smw_set_cw(smw, args.freq_hz, args.power_dbm, rf_on=rf, path=args.path)
                        print(
                            f"SMW: CW {args.freq_hz:.6g} Hz, {args.power_dbm:.3g} dBm, "
                            f"OUTPut={'ON' if rf else 'OFF'}, path={args.path}"
                        )
        except OSError as e:
            print(f"Network error connecting to SMW ({args.smw_ip}:{args.port}): {e}")
            return 1

    if args.fsw_ip:
        try:
            with ScpiTcp(args.fsw_ip, args.port) as fsw:
                run_idn(fsw, "FSW")
                if args.idn_only:
                    pass
                elif band is not None:
                    center = args.fsw_center_hz if args.fsw_center_hz is not None else band.center_hz
                    span = resolve_fsw_span_hz(band, args.span_margin, args.fsw_span_hz)
                    rbw_auto = args.rbw_hz is None
                    vbw_auto = args.vbw_hz is None
                    fsw_configure_spectrum(
                        fsw,
                        center_hz=center,
                        span_hz=span,
                        ref_level_dbm=args.fsw_ref_db,
                        channel=args.fsw_ch,
                        rbw_hz=args.rbw_hz,
                        vbw_hz=args.vbw_hz,
                        rbw_auto=rbw_auto,
                        vbw_auto=vbw_auto,
                    )
                    bw = fsw_bw_summary(rbw_auto, vbw_auto, args.rbw_hz, args.vbw_hz)
                    print(
                        f"FSW: CENTer {center:.12g} Hz ({center/1e9:.6f} GHz), "
                        f"SPAN {span:.12g} Hz ({span/1e6:.3f} MHz), "
                        f"RLEV {args.fsw_ref_db:.3g} dBm, ch={args.fsw_ch}, {bw}"
                    )
                else:
                    print(
                        "FSW: no --band, *IDN? only. "
                        "Use --band ID (with --smw-ip and --fsw-ip) to set center/span."
                    )
        except OSError as e:
            print(f"Network error connecting to FSW ({args.fsw_ip}:{args.port}): {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
