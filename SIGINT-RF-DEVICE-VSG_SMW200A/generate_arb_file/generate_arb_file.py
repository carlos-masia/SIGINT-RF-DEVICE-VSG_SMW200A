"""
generate_arb_file — Standalone R&S SMW200A ARB .wv file generator.

Reads the signal catalog directly from ``vsg_smw200a.CATALOG`` and writes
``.wv`` binary waveform files.  No instrument connection required.

Programmatic usage::

    from generate_arb_file import generate, generate_all

    path = generate("atc_am")            # → output/arb_signals.wv
    results = generate_all()             # all signals → output/<name>.wv

CLI usage::

    python generate_arb_file.py          # generate DEFAULT_SIGNAL
    python generate_arb_file.py atc_am
    python generate_arb_file.py --all    # all signals → output/
    python generate_arb_file.py --list   # list catalog entries

.wv file format (R&S SMW-ARB-V2):
    ASCII header lines enclosed in ``{...}``, terminated with ``\\r\\n``.
    Binary data block follows: 16-bit big-endian signed integers, interleaved I/Q.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths + catalog bootstrap
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent
_PACKAGE_ROOT = _HERE.parent          # …/SIGINT-RF-DEVICE-VSG_SMW200A/

# Output folder is always next to this script (not the working directory)
_OUTPUT_DIR_DEFAULT = _HERE / "output"
_BASE_STEM = "arb_signals"

# -----------------------------------------------------------------------
# DEFAULT SIGNAL — edit this to change what gets generated when the
# script is run without arguments.
# -----------------------------------------------------------------------
DEFAULT_SIGNAL = "atc_am"


def timestamped_path(
    stem: str = _BASE_STEM,
    output_dir: str | pathlib.Path = _OUTPUT_DIR_DEFAULT,
    suffix: str = ".wv",
) -> pathlib.Path:
    """Return ``<output_dir>/<stem><suffix>``."""
    return pathlib.Path(output_dir) / f"{stem}{suffix}"


# Import CATALOG from the device package (vsg_smw200a must be on sys.path)
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from vsg_smw200a import CATALOG  # noqa: E402


# ---------------------------------------------------------------------------
# .wv file writer  (no instrument needed)
# ---------------------------------------------------------------------------

def write_wv(
    iq: np.ndarray,
    fs: float,
    path: str | pathlib.Path,
    *,
    comment: str = "",
) -> None:
    """Write an R&S SMW-ARB-V2 ``.wv`` file from a complex I/Q array.

    Parameters
    ----------
    iq:
        Complex baseband samples (any dtype; converted internally).
    fs:
        Sample rate in Hz.
    path:
        Destination file path.  Parent directories are created if needed.
    comment:
        Optional free-text comment embedded in the file header.

    File structure
    --------------
    ASCII header block (``\\r\\n``-terminated lines, each field ``{TAG: value}``),
    followed immediately by interleaved 16-bit big-endian I/Q integers.

    Level offsets are both set to ``0.0`` dB because we normalise the array to
    full-scale before quantisation; configure actual RF power on the instrument
    itself or via ``vsg_smw200a.play()``.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    iq = np.asarray(iq, dtype=np.complex128)
    n = len(iq)

    # Full-scale normalise → quantise to signed 16-bit
    peak = np.max(np.abs(iq))
    if peak > 0.0:
        iq = iq / peak

    i16 = np.clip(np.real(iq) * 32767.0, -32768, 32767).astype(np.int16)
    q16 = np.clip(np.imag(iq) * 32767.0, -32768, 32767).astype(np.int16)

    # RMS offset (PAPR) so the instrument knows the crest factor
    rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
    papr_db = round(-20.0 * np.log10(rms) if rms > 0.0 else 0.0, 4)

    now = datetime.datetime.now()
    header = "\r\n".join([
        "{TYPE: SMW-ARB-V2}",
        f"{{COMMENT: {comment}}}",
        f"{{DATE: {now.strftime('%Y-%m-%d;%H:%M:%S')}}}",
        f"{{CLOCK: {fs:.6f}}}",
        f"{{SAMPLES: {n}}}",
        f"{{LEVEL OFFS: 0.0000,{papr_db:.4f}}}",
        "{DATA LIST}",
        "",   # blank line → trailing \r\n before binary block
    ])

    # Interleave I/Q as big-endian int16
    iq_interleaved = np.empty(2 * n, dtype=">i2")
    iq_interleaved[0::2] = i16.view(np.int16).astype(">i2")
    iq_interleaved[1::2] = q16.view(np.int16).astype(">i2")

    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(iq_interleaved.tobytes())


# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------

def _build_generator(entry: Any):
    """Return a zero-argument callable that produces ``(iq, fs)`` for *entry*.

    ``entry.gen`` is already the generator function (or a ``functools.partial``
    with fixed kwargs), so we just wrap it in a no-arg lambda.
    """
    return entry.gen


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    signal_name: str,
    output_path: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Generate a ``.wv`` file for *signal_name* from the ARB catalog.

    Parameters
    ----------
    signal_name:
        Key in ``vsg_smw200a.CATALOG``.
    output_path:
        Destination ``.wv`` file.  Defaults to ``output/arb_signals.wv``
        next to this script.

    Returns
    -------
    pathlib.Path
        Path to the written file.

    Raises
    ------
    KeyError
        If *signal_name* is not in the catalog.
    """
    if signal_name not in CATALOG:
        available = ", ".join(sorted(CATALOG))
        raise KeyError(
            f"Signal {signal_name!r} not in catalog.  Available: {available}"
        )

    entry = CATALOG[signal_name]
    iq, fs = entry.gen()

    out = pathlib.Path(output_path) if output_path else timestamped_path()
    comment = f"{signal_name}: {entry.description}"
    write_wv(iq, fs, out, comment=comment)
    return out


def generate_all(
    output_dir: str | pathlib.Path = _OUTPUT_DIR_DEFAULT,
) -> list[tuple[str, pathlib.Path | Exception]]:
    """Generate ``.wv`` files for **all** catalog entries into *output_dir*.

    Returns a list of ``(signal_name, Path | Exception)`` tuples.
    """
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, pathlib.Path | Exception]] = []
    for name in CATALOG:
        try:
            path = generate(name, out_dir / f"{name}.wv")
            results.append((name, path))
        except Exception as exc:  # noqa: BLE001
            results.append((name, exc))
    return results


# ---------------------------------------------------------------------------
# Spectrum plotting
# ---------------------------------------------------------------------------

def plot_spectrum(
    iq: np.ndarray,
    fs: float,
    entry: dict,
    *,
    signal_name: str = "",
    nfft: int = 4096,
) -> tuple:
    """Compute normalised power spectrum and return a matplotlib Figure plus raw data.

    Returns
    -------
    ``(fig, freqs, power_db, xlabel, title)``
        *fig* is a ``matplotlib.figure.Figure`` ready for PNG export or GUI embedding.
        *freqs* and *power_db* are numpy arrays.

    Raises
    ------
    ImportError
        If matplotlib is not installed.
    """
    try:
        import matplotlib.figure as mpl_fig
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for spectrum plots: pip install matplotlib"
        ) from exc

    n = min(len(iq), nfft)
    iq_slice = np.asarray(iq[:n], dtype=np.complex128)
    window = np.blackman(n)
    spectrum = np.fft.fftshift(np.fft.fft(iq_slice * window))
    peak = float(np.max(np.abs(spectrum)))
    power_db = 20.0 * np.log10(np.abs(spectrum) / (peak + 1e-30) + 1e-30)
    freqs_hz = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / fs))

    if fs >= 10e6:
        freqs = freqs_hz / 1e6
        xlabel = "Frequency offset (MHz)"
    elif fs >= 10e3:
        freqs = freqs_hz / 1e3
        xlabel = "Frequency offset (kHz)"
    else:
        freqs = freqs_hz
        xlabel = "Frequency offset (Hz)"

    title = (
        f"{signal_name}  –  {entry.description}\n"
        f"Carrier {entry.carrier_hz / 1e6:.3f} MHz  ·  "
        f"{entry.power_dbm} dBm  ·  "
        f"fs {fs / 1e6:.3f} MS/s  ·  N={n}"
    )

    fig = mpl_fig.Figure(figsize=(9, 3.2), dpi=100, tight_layout=True)
    ax = fig.add_subplot(111)
    ax.plot(freqs, power_db, linewidth=0.8, color="#1f77b4")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Power (dBr)")
    ax.set_title(title, fontsize=9)
    ax.set_ylim(-80, 5)
    ax.grid(True, alpha=0.3)

    return fig, freqs, power_db, xlabel, title


# ---------------------------------------------------------------------------
# ArbFileGenerator class
# ---------------------------------------------------------------------------

class ArbFileGenerator:
    """Generate an ARB ``.wv`` waveform file and spectrum PNG from the catalog.

    Output is always saved to ``<this_module>/output/`` regardless of the
    working directory.

    Workflow::

        gen = ArbFileGenerator("atc_am")
        wv_path, png_path = gen.generate()
        # gen.spectrum_figure  → matplotlib Figure (embed in Tkinter GUI)
        # gen.spectrum_data    → (freqs, power_db, xlabel, title)

        # Optionally call vsg_smw200a.play() to send to the instrument.

    For live signal browsing without writing files use :meth:`preview`.
    """

    def __init__(
        self,
        signal_name: str,
        *,
        output_dir: str | pathlib.Path = _OUTPUT_DIR_DEFAULT,
    ) -> None:
        self.signal_name = signal_name
        self.output_dir = pathlib.Path(output_dir)
        if signal_name not in CATALOG:
            available = ", ".join(sorted(CATALOG))
            raise KeyError(
                f"Signal {signal_name!r} not in catalog.  Available: {available}"
            )
        self.entry = CATALOG[signal_name]
        self._wv_path: pathlib.Path | None = None
        self._png_path: pathlib.Path | None = None
        self._spectrum_figure = None
        self._spectrum_data: tuple | None = None  # (freqs, power_db, xlabel, title)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def generate(self) -> tuple[pathlib.Path, pathlib.Path | None]:
        """Generate ``.wv`` and spectrum PNG, return ``(wv_path, png_path)``.

        Both files share the same timestamp stem:
        ``YYYYMMDDHHMM_arb_signals.{wv,png}``.

        *png_path* is ``None`` if matplotlib is not installed.
        """
        gen_fn = _build_generator(self.entry)
        iq, fs = gen_fn()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = _BASE_STEM

        wv_path = self.output_dir / f"{stem}.wv"
        comment = f"{self.signal_name}: {self.entry.description}"
        write_wv(iq, fs, wv_path, comment=comment)
        self._wv_path = wv_path

        png_path: pathlib.Path | None = None
        try:
            fig, freqs, power_db, xlabel, title = plot_spectrum(
                iq, fs, self.entry, signal_name=self.signal_name
            )
            png_path = self.output_dir / f"{stem}.png"
            fig.savefig(png_path, dpi=120, bbox_inches="tight")
            self._spectrum_figure = fig
            self._spectrum_data = (freqs, power_db, xlabel, title)
            self._png_path = png_path
        except ImportError:
            pass

        return wv_path, png_path

    def preview(self) -> tuple | None:
        """Compute spectrum without writing any files.

        Returns ``(freqs, power_db, xlabel, title)`` and populates
        :attr:`spectrum_figure` / :attr:`spectrum_data`.
        Returns ``None`` if matplotlib is not installed.
        """
        gen_fn = _build_generator(self.entry)
        iq, fs = gen_fn()
        try:
            fig, freqs, power_db, xlabel, title = plot_spectrum(
                iq, fs, self.entry, signal_name=self.signal_name
            )
            self._spectrum_figure = fig
            self._spectrum_data = (freqs, power_db, xlabel, title)
            return freqs, power_db, xlabel, title
        except ImportError:
            return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def wv_path(self) -> pathlib.Path | None:
        """Path to the last generated ``.wv``, or ``None``."""
        return self._wv_path

    @property
    def png_path(self) -> pathlib.Path | None:
        """Path to the last generated PNG, or ``None``."""
        return self._png_path

    @property
    def spectrum_figure(self):
        """Last matplotlib Figure, or ``None`` if not yet generated."""
        return self._spectrum_figure

    @property
    def spectrum_data(self) -> tuple | None:
        """``(freqs, power_db, xlabel, title)`` for the last preview/generate."""
        return self._spectrum_data

if __name__ == "__main__":
    import sys as _sys

    # Re-use the CLI defined in __main__.py but import it inline to avoid
    # relative-import issues when the file is run as a plain script.
    _here = pathlib.Path(__file__).parent
    _pkg_root = _here.parent
    if str(_pkg_root) not in _sys.path:
        _sys.path.insert(0, str(_pkg_root))

    # Minimal inline CLI so ``python generate_arb_file.py atc_am`` works.
    import argparse as _ap

    _p = _ap.ArgumentParser(
        description="Generate R&S SMW200A ARB .wv files from arb_catalog.yaml.",
    )
    _p.add_argument("signal", nargs="?", help="Signal name (omit → uses DEFAULT_SIGNAL)")
    _p.add_argument("-o", "--output", help="Output .wv path")
    _p.add_argument("--all", action="store_true", help="Generate all catalog signals")
    _p.add_argument("--list", action="store_true", help="List catalog signals and exit")
    _a = _p.parse_args()

    if _a.list:
        _col = max(len(k) for k in CATALOG)
        print(f"{'SIGNAL':<{_col}}  CARRIER MHz  POWER dBm  DESCRIPTION")
        print("-" * (_col + 48))
        for _n, _e in CATALOG.items():
            print(
                f"{_n:<{_col}}  "
                f"{_e.carrier_hz / 1e6:>11.3f}  "
                f"{_e.power_dbm:>9}  "
                f"{_e.description}"
            )
        _sys.exit(0)

    if _a.all:
        _results = generate_all(_a.output or str(_OUTPUT_DIR_DEFAULT))
        _ok = sum(1 for _, r in _results if not isinstance(r, Exception))
        print(f"\nGenerated {_ok}/{len(_results)} signals")
        for _n, _r in _results:
            print(f"  {'OK  ' if not isinstance(_r, Exception) else 'FAIL'} {_n} → {_r}")
        _sys.exit(0 if _ok == len(_results) else 1)

    # Single signal: use explicit argument or fall back to DEFAULT_SIGNAL
    _sig = _a.signal or DEFAULT_SIGNAL
    _out = _a.output or timestamped_path()
    _path = generate(_sig, _out)
    print(f"OK  {_sig} → {_path}")
    _sys.exit(0)
