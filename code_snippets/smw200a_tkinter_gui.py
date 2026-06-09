#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smw200a_tkinter_gui.py
======================
Tkinter GUI for the R&S SMW200A ARB catalog.

All catalog logic, I/Q generators, DSP helpers and ``play()`` now live in
``vsg_smw200a`` (device package). This file is only the Tkinter front-end.

Quick usage:
    python smw200a_tkinter_gui.py --gui
    python smw200a_tkinter_gui.py --list
    python smw200a_tkinter_gui.py --signal ais
    python smw200a_tkinter_gui.py --signal radar_x --dry-run

SECURITY NOTICE
---------------
Several entries use distress/safety service frequencies (EPIRB 406 MHz,
ELT 121.5 MHz, maritime Ch16 and DSC Ch70, Mode S/ADS-B with valid ICAO).
Generate these **only** in a shielded bench (cable + attenuator) or screened
chamber — **never** radiate outdoors.
"""

from __future__ import annotations

import argparse
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

# ---------------------------------------------------------------------------
# sys.path bootstrap — this file lives in code_snippets/; the device package
# is in the sibling SIGINT-RF-DEVICE-VSG_SMW200A/ inner folder.
# ---------------------------------------------------------------------------
_device_inner = Path(__file__).resolve().parent.parent / "SIGINT-RF-DEVICE-VSG_SMW200A"
if str(_device_inner) not in sys.path:
    sys.path.insert(0, str(_device_inner))

import os
_VSG_YAML = _device_inner / "vsg_config.yaml"
if _VSG_YAML.is_file() and not os.environ.get("VSG_CONFIG_PATH", "").strip():
    os.environ["VSG_CONFIG_PATH"] = str(_VSG_YAML)

# ---------------------------------------------------------------------------
# All catalog / ARB logic imported from the device package
# ---------------------------------------------------------------------------
from vsg_smw200a import (
    CATALOG,
    GenParamSpec,
    VsgSmw200a,
    _fsw_ip_from_vsg_yaml,
    build_visa_resource,
    config_file_path,
    default_smw_ip,
    default_socket_port,
    default_transport,
    default_visa_from_config,
    default_visa_from_env,
    format_gen_default_for_entry,
    is_rs_smw_installed,
    list_generator_param_specs,
    parse_gen_param_value,
    play,
    resolve_instr_addr,
)


# ============================================================================
# Tkinter GUI
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
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                self.root.after(0, lambda: messagebox.showerror(title, err))
                self.root.after(0, lambda: self._append_log(f"[{title}] ERROR: {err}"))
            finally:
                self._busy = False
                self.root.after(0, lambda: self.btn_play.config(state=tk.NORMAL))

        threading.Thread(target=target, daemon=True).start()

    def _on_list(self) -> None:
        self._append_log("Available signals — ARB I/Q [P] (B9+K515+K527):")
        for k, e in CATALOG.items():
            self._append_log(f"  {k:14s} {e.carrier_hz / 1e6:10.3f} MHz  {e.power_dbm:>4} dBm  {e.description}")
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
        except ValueError as exc:
            messagebox.showerror("Waveform parameters", str(exc))
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
    """Open ARB catalog UI as a child window (same process as rs_smw_fsw_tcp GUI)."""
    try:
        parent.update_idletasks()
    except tk.TclError:
        pass
    win = tk.Toplevel(parent)
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
        description="ARB waveform generator for R&S SMW200A (vsg_smw200a; vsg_config.yaml + env)."
    )
    ap.add_argument("--signal", help="Catalog entry name (see --list).")
    ap.add_argument("--list", action="store_true", help="List catalog entries and exit.")
    ap.add_argument("--license-detail", action="store_true", help="With --list, show permanent ARB path and trial-native notes.")
    ap.add_argument("--gui", action="store_true", help="Open the Tkinter catalog window.")
    ap.add_argument("--dry-run", action="store_true", help="Only create the .wv file; do not upload or change the instrument.")
    ap.add_argument("--instr-addr", default=None, metavar="VISA", help="Full VISA resource string.")
    ap.add_argument("--smw-ip", default=None, metavar="IP", help="SMW IP (transport/port from vsg_config.yaml or env).")
    ap.add_argument("--fsw-ip", default=None, metavar="IP", help="FSW43 IP (SCPI TCP).")
    ap.add_argument("--fsw-port", type=int, default=5025, help="FSW SCPI TCP port (default 5025).")
    ap.add_argument("--fsw-timeout", type=float, default=15.0, help="FSW socket timeout in seconds.")
    ap.add_argument("--fsw-span-hz", type=float, default=None, help="Override spectrum span (Hz).")
    ap.add_argument("--fsw-ref-db", type=float, default=None, help="FSW reference level (dBm).")
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
        print("Available signals (permanent ARB I/Q [P], B9+K515+K527):")
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
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
