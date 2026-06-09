# SIGINT-RF-DEVICE-VSG_SMW200A

Python tools for the **Rohde & Schwarz SMW200A** vector signal generator (VSG) and related **SMW / FSW** SCPI-over-TCP workflows.

## Contents

| Location | Role |
|----------|------|
| `SIGINT-RF-DEVICE-VSG_SMW200A/SIGINT-RF-DEVICE-VSG_SMW200A/vsg_config.yaml` | **Defaults** for `VsgSmw200a` (IP, transport, port, optional `smw_visa`). Overridden by `SMW_*` env vars. Alternate path: `VSG_CONFIG_PATH`. |
| `SIGINT-RF-DEVICE-VSG_SMW200A/SIGINT-RF-DEVICE-VSG_SMW200A/vsg_smw200a.py` | **Instrument layer:** thin `RsSmw` wrapper (`VsgSmw200a`), HiSLIP or SCPI socket, `open` / `close`, access to `.smw` for full driver API. |
| `SIGINT-RF-DEVICE-VSG_SMW200A/SIGINT-RF-DEVICE-VSG_SMW200A/smw200a_arb_signals.py` | **ARB catalog:** builds I/Q, writes `.wv`, uploads to the SMW ARB path, optional FSW spectrum alignment; includes a Tk catalog GUI (`--gui`). |
| `rs_smw_fsw_tcp.py` (repository root, next to `pyproject.toml`) | **SCPI/TCP GUI:** band presets, CW setup, FSW spectrum; Tk UI. Uses the same SCPI helpers as the ARB script. |

The **GUI** project (`SIGINT-RF-GUI`) imports `vsg_smw200a.py` indirectly via `vsg_bridge.py` so this folder remains the single source of truth for hardware control.

## Requirements

- **Python** ≥ 3.10 (`requires-python` in `pyproject.toml`).
- **RsSmw** (R&S Python driver) and a **VISA** runtime (R&S VISA or NI-VISA) for HiSLIP / SOCKET.
- **NumPy** (ARB generation and samples).
- **PyYAML** (read `vsg_config.yaml` for `VsgSmw200a` defaults).

Install from the repository root:

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A
pip install -e .
```

Or install dependencies only:

```powershell
pip install -r SIGINT-RF-DEVICE-VSG_SMW200A\requirements.txt
```

### `rs_scpi_tcp` module

`smw200a_arb_signals.py` and `rs_smw_fsw_tcp.py` import **`rs_scpi_tcp`** (SCPI over TCP helpers). That file must be available on **PYTHONPATH** next to those scripts (same layout you use today). It is not published on PyPI in this snapshot—add or vendor `rs_scpi_tcp.py` in your checkout if it is missing.

## Quick usage

### VSG session (`vsg_smw200a.py`)

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A\SIGINT-RF-DEVICE-VSG_SMW200A
# No CLI args: reads vsg_config.yaml (next to this script), then SMW_* env overrides
python vsg_smw200a.py
```

Configuration resolution for each field (highest priority first): **explicit `VsgSmw200a(...)` kwargs** → **`SMW_*` environment variables** → **`vsg_config.yaml`** → **built-in constants** in `vsg_smw200a.py`.

YAML keys (see `vsg_config.yaml`): `smw_ip`, `smw_transport` (`hislip` or `socket`), `smw_socket_port`, optional `smw_visa` (full VISA resource). Set **`VSG_CONFIG_PATH`** to point at an alternate YAML file.

### ARB catalog (`smw200a_arb_signals.py`)

```powershell
python smw200a_arb_signals.py --list
python smw200a_arb_signals.py --gui
python smw200a_arb_signals.py --signal ais --smw-ip 192.168.1.10
```

See the module docstring for licensing notes (permanent ARB path vs trials) and **RF safety** (shielded bench only).

### SMW / FSW TCP tool (`rs_smw_fsw_tcp.py`)

```powershell
cd SIGINT-RF-DEVICE-VSG_SMW200A
python rs_smw_fsw_tcp.py --smw-ip 192.168.1.10 --fsw-ip 192.168.1.11 --idn-only
python rs_smw_fsw_tcp.py --gui
```

## Packaging notes

`pyproject.toml` declares runtime dependencies and, for setuptools, exposes `rs_smw_fsw_tcp` as a root-level module when the project is installed. Inner-folder scripts are typically run as files or with `PYTHONPATH` pointing at `SIGINT-RF-DEVICE-VSG_SMW200A/SIGINT-RF-DEVICE-VSG_SMW200A/`.

## Development extras

```powershell
pip install -e ".[dev]"
```

## License

MIT (see `pyproject.toml`).
