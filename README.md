# SIGINT-RF-DEVICE-VSG_SMW200A

Python tools for the **Rohde & Schwarz SMW200A** vector signal generator (VSG) and related **SMW / FSW** SCPI-over-TCP workflows.

## Repository layout

```text
SIGINT-RF-DEVICE-VSG_SMW200A/          ← repo root (pyproject.toml, .venv)
  SIGINT-RF-DEVICE-VSG_SMW200A/        ← installable Python sources
    vsg_smw200a.py                     ← VsgSmw200a: RsSmw wrapper (VISA connection)
    vsg_config.yaml                    ← instrument defaults (IP, transport, port)
    rs_scpi_tcp/                       ← SCPI-over-TCP package (stdlib only)
      __init__.py                      ← re-exports ScpiTcp, smw_query_idn, …
      rs_scpi_tcp.py                   ← implementation
    arb_catalog.yaml                   ← ARB signal catalog (carrier, power, gen, …)
  code_snippets/
    smw200a_tkinter_gui.py             ← legacy Tk ARB catalog GUI (archive / reference)
```

## What each file does

| File | Role |
|------|------|
| `vsg_smw200a.py` | **High-level driver.** Thin `RsSmw` wrapper (`VsgSmw200a`). Reads `vsg_config.yaml` / env vars for VISA address. Use this for ARB upload, RF on/off, full instrument API. |
| `vsg_config.yaml` | **Instrument defaults:** `smw_ip`, `smw_transport` (`hislip` / `socket`), `smw_socket_port`, optional `smw_visa`, optional `fsw_ip` / `fsw43_ip`. Override with `SMW_*` env vars or `VSG_CONFIG_PATH`. |
| `rs_scpi_tcp/` | **Low-level raw TCP SCPI** (stdlib only, no VISA). `ScpiTcp` class + helpers: `smw_query_idn` (quick `*IDN?`), `smw_set_cw`, `fsw_configure_spectrum`, `fsw_bw_summary`. Used by the Shiny GUI **Test SMW** button and any FSW tuning. |
| `arb_catalog.yaml` | **Signal catalog config.** 23 entries (VHF → Ka), each with `gen`, `carrier_mhz`, `power_dbm`, `description`, `trial_native`. Edit here to add / change signals without touching Python code. |
| `code_snippets/smw200a_tkinter_gui.py` | Legacy Tk ARB catalog GUI (original all-in-one script). Kept as reference — **not the primary entry point**. Has a `sys.path` bootstrap to find `rs_scpi_tcp` and `vsg_smw200a` from the inner folder. |

## `vsg_smw200a` vs `rs_scpi_tcp` — when to use each

| Task | Use |
|------|-----|
| Upload `.wv` waveform, set RF freq / power, control ARB | `vsg_smw200a` (RsSmw, requires VISA) |
| Quick `*IDN?` to verify the instrument is reachable | `rs_scpi_tcp.smw_query_idn` |
| Configure FSW spectrum (center, span, RBW) | `rs_scpi_tcp.fsw_configure_spectrum` |
| Set a CW without the full driver | `rs_scpi_tcp.smw_set_cw` |
| No VISA stack on the PC | `rs_scpi_tcp` only |

## Requirements

- **Python** ≥ 3.14.5 (see `pyproject.toml`).
- **RsSmw** + a **VISA** runtime (R&S VISA or NI-VISA) — only for `vsg_smw200a`.
- **NumPy** and **PyYAML** — for the ARB scripts (`smw200a_arb_signals.py` in the Shiny GUI repo).

Install from the repository root:

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A
pip install -e .
```

After install, `import rs_scpi_tcp` and `import vsg_smw200a` work in any script running in that venv.

## Quick usage

### Connection test (`vsg_smw200a.py`)

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

### Shiny ARB catalog GUI

Lives in the sibling repo **`SIGINT-RF-GUI`**. `vsg_gui.py` loads `smw200a_arb_signals.py` from that repo and uses `rs_scpi_tcp` and `vsg_smw200a` from here.

```powershell
cd ..\SIGINT-RF-GUI
pip install -e .           # also installs this device package as a dependency
sigint-rf-gui-shiny
```

### Legacy Tk GUI (code_snippets)

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A        # repo root
python .\code_snippets\smw200a_tkinter_gui.py --gui
```

## Packaging notes

`pyproject.toml` maps setuptools `package-dir` to `SIGINT-RF-DEVICE-VSG_SMW200A/` and exposes:
- **package** `rs_scpi_tcp` (folder with `__init__.py`)
- **flat modules** `vsg_smw200a`, `rs_smw_fsw_tcp`, `smw200a_arb_signals`, `vsg_tk_smoke`

## Development extras

```powershell
pip install -e ".[dev]"
```

## License

MIT (see `pyproject.toml`).
