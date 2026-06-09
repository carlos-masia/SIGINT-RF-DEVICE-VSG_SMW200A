# SIGINT-RF-DEVICE-VSG_SMW200A

Python tools for the **Rohde & Schwarz SMW200A** vector signal generator (VSG) and related **SMW / FSW** SCPI-over-TCP workflows.

## Repository layout

```text
SIGINT-RF-DEVICE-VSG_SMW200A/          ← repo root (pyproject.toml, .venv)
  SIGINT-RF-DEVICE-VSG_SMW200A/        ← installable Python sources
    vsg_smw200a.py                     ← full application layer: VsgSmw200a, CATALOG, play(), generators, …
    vsg_config.yaml                    ← instrument defaults (IP, transport, port, FSW IP)
    arb_catalog.yaml                   ← signal catalog documentation (reference only)
    rs_scpi_tcp/                       ← SCPI-over-TCP package (stdlib only)
      __init__.py                      ← re-exports ScpiTcp, smw_query_idn, …
      rs_scpi_tcp.py                   ← implementation
    dsptools/                          ← DSP helpers and I/Q signal generators
      __init__.py                      ← re-exports everything
      filters.py                       ← normalize, rrc_filter, gaussian_pulse, random_bits, shape_symbols
      signal_generator.py              ← gen_am_dsb, gen_fm, gen_gmsk, gen_fsk, gen_4fsk,
                                          gen_pi4_dqpsk, gen_psk, gen_qam, gen_apsk16,
                                          gen_lfm_pulse, gen_barker_pulse, gen_ppm_adsb,
                                          gen_multitone, gen_awgn
  code_snippets/
    smw200a_tkinter_gui.py             ← Tkinter ARB catalog GUI (imports everything from vsg_smw200a)
```

## What each file does

| File | Role |
|------|------|
| `vsg_smw200a.py` | **Application layer.** `VsgSmw200a` (VISA connection), ARB `CATALOG` (23 signals), `play()` (generate → upload → RF on), `resolve_instr_addr`, FSW helpers. Imports generators from `dsptools`. |
| `vsg_config.yaml` | **Instrument defaults:** `smw_ip`, `smw_transport` (`hislip` / `socket`), `smw_socket_port`, optional `smw_visa`, optional `fsw_ip` / `fsw43_ip`. Override with `SMW_*` env vars or `VSG_CONFIG_PATH`. |
| `rs_scpi_tcp/` | **Low-level raw TCP SCPI** (stdlib only, no VISA). `ScpiTcp` class + helpers: `smw_query_idn` (quick `*IDN?`), `smw_set_cw`, `fsw_configure_spectrum`, `fsw_bw_summary`. |
| `dsptools/filters.py` | **DSP primitives:** `normalize`, `rrc_filter`, `gaussian_pulse`, `random_bits`, `shape_symbols`. |
| `dsptools/signal_generator.py` | **I/Q generators.** Each returns `(complex_array, sample_rate_hz)`. 14 modulations: AM-DSB, FM, GMSK, 2-FSK, 4-FSK, pi/4-DQPSK, PSK, QAM, 16-APSK, LFM pulse, Barker pulse, ADS-B PPM, multitone, AWGN. |
| `arb_catalog.yaml` | **Signal catalog reference.** 23 entries (VHF → Ka). Documents `gen`, `carrier_mhz`, `power_dbm`, `description`, `trial_native`. The live catalog is built in `vsg_smw200a.CATALOG`. |
| `code_snippets/smw200a_tkinter_gui.py` | Tkinter front-end for the ARB catalog. Thin layer — all logic (`CATALOG`, `play()`, generators) imported from `vsg_smw200a`. |

## Module dependency graph

```
smw200a_tkinter_gui.py  ──►  vsg_smw200a.py  ──►  dsptools/filters.py
                                             ──►  dsptools/signal_generator.py
                                             ──►  rs_scpi_tcp/

vsg_gui.py (Shiny)      ──►  vsg_smw200a.py  (same path)
                        ──►  rs_scpi_tcp/    (Test SMW button)
```

## `vsg_smw200a` vs `rs_scpi_tcp` — when to use each

| Task | Use |
|------|-----|
| Generate ARB waveform, upload `.wv`, set RF freq / power | `vsg_smw200a.play()` |
| Full instrument API (RsSmw) | `vsg_smw200a.VsgSmw200a` |
| Quick `*IDN?` to verify the instrument is reachable | `rs_scpi_tcp.smw_query_idn` |
| Configure FSW spectrum (center, span, RBW) | `rs_scpi_tcp.fsw_configure_spectrum` |
| Set a CW without the full driver | `rs_scpi_tcp.smw_set_cw` |
| No VISA stack on the PC | `rs_scpi_tcp` only |

## Requirements

- **Python** ≥ 3.14.5 (see `pyproject.toml`).
- **RsSmw** + a **VISA** runtime (R&S VISA or NI-VISA) — only for `VsgSmw200a` and `play()`.
- **NumPy** — for `dsptools` (signal generation). Already declared in `pyproject.toml`.
- **PyYAML** — for reading `vsg_config.yaml`. Already declared in `pyproject.toml`.

Install from the repository root:

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A
pip install -e .
```

After install, `import vsg_smw200a`, `import rs_scpi_tcp`, and `import dsptools` work in any script running in that venv.

## Quick usage

### ARB catalog — list signals

```powershell
python .\code_snippets\smw200a_tkinter_gui.py --list
python .\code_snippets\smw200a_tkinter_gui.py --list --license-detail
```

### ARB catalog — play a signal (CLI)

```powershell
python .\code_snippets\smw200a_tkinter_gui.py --signal ais
python .\code_snippets\smw200a_tkinter_gui.py --signal ais --smw-ip 192.168.1.10 --fsw-ip 192.168.1.11
python .\code_snippets\smw200a_tkinter_gui.py --signal radar_x --dry-run
```

### ARB catalog — Tkinter GUI

```powershell
python .\code_snippets\smw200a_tkinter_gui.py --gui
```

### Connection test (VISA)

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A\SIGINT-RF-DEVICE-VSG_SMW200A
python vsg_smw200a.py
```

Config resolution (highest priority first): explicit `VsgSmw200a(...)` kwargs → `SMW_*` env vars → `vsg_config.yaml` → built-in defaults.

### Raw TCP `*IDN?` (no VISA needed)

```python
from rs_scpi_tcp import smw_query_idn
print(smw_query_idn("192.168.1.10"))
```

### Use `play()` from Python

```python
from vsg_smw200a import play

play("ais", dry_run=True)                          # generate .wv only
play("atc_am", smw_ip="192.168.1.10")              # upload + RF on (requires RsSmw + VISA)
play("radar_x", fsw_ip="192.168.1.11", log=print)  # + tune FSW43
```

### Shiny ARB catalog GUI

Lives in the sibling repo **`SIGINT-RF-GUI`**. Imports `CATALOG`, `play()`, `vsg_smw200a`, `rs_scpi_tcp` from this package.

```powershell
cd ..\SIGINT-RF-GUI
pip install -e .      # also installs this device package as a dependency
sigint-rf-gui-shiny
```

## Packaging notes

`pyproject.toml` maps setuptools `package-dir` to `SIGINT-RF-DEVICE-VSG_SMW200A/` and exposes:

- **packages** `rs_scpi_tcp`, `dsptools` (subfolders with `__init__.py`)
- **flat modules** `vsg_smw200a`, `rs_smw_fsw_tcp`, `vsg_tk_smoke`

## Development extras

```powershell
pip install -e ".[dev]"
```

## License

MIT (see `pyproject.toml`).
