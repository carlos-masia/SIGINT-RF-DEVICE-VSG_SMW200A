"""
R&S SMW200A VSG control via RsSmw (VISA: HiSLIP or SOCKET).

This module lives in SIGINT-RF-DEVICE-VSG_SMW200A (instrument layer).
Import it directly: ``from vsg_smw200a import VsgSmw200a`` (after install or with this folder on ``PYTHONPATH``).

**Configuration** is read from ``vsg_config.yaml`` next to this file, then overridden by
environment variables (highest priority), then by explicit ``VsgSmw200a(...)`` keyword arguments.

| Source | Keys / variables |
|--------|------------------|
| YAML ``vsg_config.yaml`` | ``smw_ip``, ``smw_transport``, ``smw_socket_port``, ``smw_visa`` |
| Environment | ``SMW_VISA``, ``SMW_IP``, ``SMW_TRANSPORT``, ``SMW_SOCKET_PORT`` |
| Env ``VSG_CONFIG_PATH`` | Optional absolute path to an alternate YAML file |

Example (no arguments):

    from vsg_smw200a import VsgSmw200a

    with VsgSmw200a() as vsg:
        print(vsg.idn())

Requires: pip install RsSmw PyYAML (and a VISA stack on the PC for HiSLIP/SOCKET)
"""

from __future__ import annotations

import functools
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple, Optional

import numpy as np

Transport = Literal["hislip", "socket"]

VSG_CONFIG_FILENAME = "vsg_config.yaml"

DEFAULT_SMW_IP = "192.168.0.10"
DEFAULT_SOCKET_PORT = 5025
DEFAULT_TRANSPORT: Transport = "hislip"
_RS_SMW_MIN_VERSION = "5.0.44"

_config_cache: dict[str, Any] | None = None


def config_file_path() -> Path:
    """Path to the active ``vsg_config.yaml`` (next to this module unless ``VSG_CONFIG_PATH`` is set)."""
    override = os.environ.get("VSG_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / VSG_CONFIG_FILENAME


def clear_vsg_config_cache() -> None:
    """Reload YAML from disk on the next access (useful after editing the file at runtime)."""
    global _config_cache
    _config_cache = None


def _load_yaml_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    path = config_file_path()
    if not path.is_file():
        _config_cache = {}
        return _config_cache
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError(
            "Missing package 'PyYAML' (required to read vsg_config.yaml). Install with:\n"
            "  pip install PyYAML\n"
            f"Original error: {e}"
        ) from e
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise RuntimeError(f"Could not read VSG config file {path}: {e}") from e
    if raw is None:
        _config_cache = {}
    elif not isinstance(raw, dict):
        raise RuntimeError(f"VSG config {path} must be a YAML mapping (object), not {type(raw).__name__}.")
    else:
        _config_cache = raw
    return _config_cache


def _cfg_str(key: str, default: str = "") -> str:
    v = _load_yaml_config().get(key)
    if v is None:
        return default
    return str(v).strip()


def is_rs_smw_installed() -> bool:
    try:
        import RsSmw  # noqa: F401

        return True
    except ImportError:
        return False


def _import_rs_smw_class() -> Any:
    try:
        from RsSmw import RsSmw

        return RsSmw
    except ImportError as e:
        raise RuntimeError(
            "Missing Python package 'RsSmw'. Install it with the same interpreter you use for this app:\n"
            "  pip install RsSmw\n"
            "You also need VISA (R&S VISA or NI-VISA) for HiSLIP/SOCKET.\n"
            f"Original error: {e}"
        ) from e


def default_transport() -> Transport:
    """``SMW_TRANSPORT`` if set, else ``smw_transport`` from YAML, else ``hislip``."""
    raw = os.environ.get("SMW_TRANSPORT", "").strip().lower()
    if raw == "socket":
        return "socket"
    if raw in ("hislip", "hislip0"):
        return "hislip"
    if raw:
        return "hislip"
    yt = _cfg_str("smw_transport").lower()
    if yt == "socket":
        return "socket"
    return "hislip"


def default_socket_port() -> int:
    """``SMW_SOCKET_PORT`` if set, else ``smw_socket_port`` from YAML, else built-in default."""
    raw = os.environ.get("SMW_SOCKET_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    yv = _load_yaml_config().get("smw_socket_port")
    if yv is not None:
        try:
            return int(yv)
        except (TypeError, ValueError):
            pass
    return DEFAULT_SOCKET_PORT


def default_smw_ip(explicit_ip: str | None = None) -> str:
    """Explicit ``ip`` argument, else ``SMW_IP``, else YAML ``smw_ip``, else ``DEFAULT_SMW_IP``."""
    if explicit_ip and explicit_ip.strip():
        return explicit_ip.strip()
    env_ip = os.environ.get("SMW_IP", "").strip()
    if env_ip:
        return env_ip
    yip = _cfg_str("smw_ip")
    if yip:
        return yip
    return DEFAULT_SMW_IP


def build_visa_resource(
    *,
    ip: str | None,
    transport: Transport,
    socket_port: int,
) -> str:
    host = default_smw_ip(ip)
    if transport == "hislip":
        return f"TCPIP::{host}::HISLIP"
    return f"TCPIP::{host}::{int(socket_port)}::SOCKET"


def default_visa_from_env() -> str | None:
    """If ``SMW_VISA`` is set, return it (full VISA resource string)."""
    v = os.environ.get("SMW_VISA", "").strip()
    return v or None


def default_visa_from_config() -> str | None:
    """YAML ``smw_visa`` if non-empty (used when ``SMW_VISA`` is unset)."""
    v = _cfg_str("smw_visa")
    return v or None


class VsgSmw200a:
    """
    Thin wrapper around RsSmw to open one VSG session and reuse it.

    Omit keyword arguments to use ``vsg_config.yaml``, then ``SMW_*`` environment variables,
    then explicit kwargs (kwargs win for each field you pass).

    - HiSLIP (default): TCPIP::<ip>::HISLIP
    - SCPI socket: transport='socket' -> TCPIP::<ip>::<port>::SOCKET
      (RsSmw needs options='SelectVisa=SocketIo' in that case.)
    """

    def __init__(
        self,
        *,
        ip: str | None = None,
        visa_resource: str | None = None,
        transport: Transport | None = None,
        socket_port: int | None = None,
        options: str | None = None,
        id_query: bool = True,
        reset: bool = False,
    ) -> None:
        env_visa = default_visa_from_env()
        yaml_visa = default_visa_from_config()
        tr = default_transport() if transport is None else transport
        sp = default_socket_port() if socket_port is None else int(socket_port)

        if visa_resource and visa_resource.strip():
            self._resource = visa_resource.strip()
        elif env_visa:
            self._resource = env_visa
        elif yaml_visa:
            self._resource = yaml_visa
        else:
            self._resource = build_visa_resource(
                ip=ip, transport=tr, socket_port=sp
            )

        if options is not None:
            self._options = options
        elif "::SOCKET" in self._resource.upper():
            self._options = "SelectVisa=SocketIo"
        else:
            self._options = ""

        self._id_query = id_query
        self._reset = reset
        self._drv: Any | None = None

    @property
    def visa_resource(self) -> str:
        return self._resource

    def _make_driver(self) -> Any:
        RsSmw = _import_rs_smw_class()
        RsSmw.assert_minimum_version(_RS_SMW_MIN_VERSION)
        kw: dict[str, Any] = {"id_query": self._id_query, "reset": self._reset}
        if self._options:
            kw["options"] = self._options
        return RsSmw(self._resource, **kw)

    def open(self) -> VsgSmw200a:
        """Open the VISA session (connect to the instrument)."""
        if self._drv is None:
            self._drv = self._make_driver()
        return self

    def close(self) -> None:
        if self._drv is not None:
            self._drv.close()
            self._drv = None

    def __enter__(self) -> VsgSmw200a:
        return self.open()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def smw(self) -> Any:
        """Underlying RsSmw instance; call open() first."""
        if self._drv is None:
            raise RuntimeError("Session is closed: call open() or use 'with VsgSmw200a(...)'.")
        return self._drv

    def idn(self) -> str:
        self.open()
        return str(self._drv.utilities.idn_string)

    def query_opc(self) -> int:
        """Query *OPC? (1 when previous operations have completed)."""
        self.open()
        return int(float(self._drv.utilities.query_str("*OPC?").strip()))


# ============================================================================
# ARB waveform: file paths (PC temp + instrument destination)
# ============================================================================

PC_WV = r"./_tmp_signal.wv"
INSTR_WV = "/var/user/arb_signal.wv"


# ============================================================================
# Config helpers: FSW IP from vsg_config.yaml
# ============================================================================

def _fsw_ip_from_vsg_yaml() -> str | None:
    """Return ``fsw_ip`` / ``fsw43_ip`` from vsg_config.yaml, or None."""
    cfg = _load_yaml_config()
    for key in ("fsw_ip", "fsw43_ip"):
        v = cfg.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def resolve_instr_addr(instr_addr: str | None, smw_ip: str | None) -> str:
    """
    Resolve the VISA resource string for the SMW200A.

    Priority: explicit ``instr_addr`` > explicit ``smw_ip`` (with transport/port from
    YAML/env) > ``SMW_VISA`` env var > YAML ``smw_visa`` > YAML ``smw_ip`` + transport.
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
    return build_visa_resource(ip=None, transport=default_transport(), socket_port=default_socket_port())


# ============================================================================
# FSW helpers
# ============================================================================

def estimate_fsw_span_hz(fs: float) -> float:
    """Heuristic spectrum span (Hz) from ARB sample clock ``fs``."""
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
    from rs_scpi_tcp import ScpiTcp, fsw_bw_summary, fsw_configure_spectrum

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
# DSP helpers and signal generators (dsptools sub-package)
# ============================================================================

from dsptools.filters import gaussian_pulse, normalize, random_bits, rrc_filter, shape_symbols
from dsptools.signal_generator import (
    gen_am_dsb,
    gen_apsk16,
    gen_apsk32,
    gen_awgn,
    gen_barker_pulse,
    gen_fsk,
    gen_4fsk,
    gen_fm,
    gen_gmsk,
    gen_lfm_pulse,
    gen_multitone,
    gen_pi4_dqpsk,
    gen_ppm_adsb,
    gen_psk,
    gen_qam,
    gen_starlink_ku,
    gen_vdes,
)

# Keep underscore aliases so existing internal references (e.g. CATALOG) stay unchanged.
_normalize = normalize
_rrc_filter = rrc_filter
_gaussian_pulse = gaussian_pulse
_random_bits = random_bits
_shape_symbols = shape_symbols


# ============================================================================
# Catalog infrastructure
# ============================================================================

PERMANENT_ARB = "ARB I/Q (B9+K515+K527) [P]"


class CatalogEntry(NamedTuple):
    """Waveform row: generator, RF, level, description, and license routing hints."""

    gen: Callable[..., tuple[Any, float]]
    carrier_hz: float
    power_dbm: float
    description: str
    permanent_delivery: str
    trial_native: str


class GenParamSpec(NamedTuple):
    """One generator keyword argument exposed in the GUI."""

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
        return "float"
    if isinstance(val, int):
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
    """Parse a single GUI field into a Python value."""
    t = text.strip()
    if spec.kind == "tuple_float":
        if not t:
            return spec.default
        parts = [p.strip() for p in t.split(",") if p.strip()]
        return tuple(float(x) for x in parts)
    if spec.kind == "int":
        return int(float(t)) if t else spec.default
    return float(t) if t else spec.default


def format_gen_default_for_entry(spec: GenParamSpec) -> str:
    """String to pre-fill a GUI field from the catalog default."""
    v = spec.default
    if spec.kind == "tuple_float":
        return ",".join(str(float(x)) for x in v)
    if spec.kind == "int":
        return str(int(v))
    return str(v)


def build_gen_call_kwargs(entry: CatalogEntry, overrides: dict[str, Any]) -> dict[str, Any]:
    """Build kwargs for entry.gen() respecting functools.partial bindings."""
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


# ============================================================================
# Catalog
# ============================================================================

CATALOG: dict[str, CatalogEntry] = {
    "atc_am": CatalogEntry(gen_am_dsb, 118.000e6, -30, "ATC voice AM-DSB", PERMANENT_ARB, "AM nativo K720 [T] (no usado aqui)"),
    "maritime_fm": CatalogEntry(gen_fm, 156.800e6, -30, "Maritime voice FM (Ch16)", PERMANENT_ARB, "FM nativo K720 [T] (no usado aqui)"),
    "noaa_fm": CatalogEntry(gen_fm, 162.400e6, -30, "NOAA WX FM", PERMANENT_ARB, "FM nativo K720 [T] (no usado aqui)"),
    "gmrs_fm": CatalogEntry(gen_fm, 462.5625e6, -30, "GMRS FM", PERMANENT_ARB, "FM nativo K720 [T] (no usado aqui)"),
    "ais": CatalogEntry(gen_gmsk, 161.975e6, -40, "AIS GMSK", PERMANENT_ARB, "Custom GMSK / ARB [P] - bits aleatorios (trama AIS real -> encoder externo)"),
    "dsc": CatalogEntry(gen_fsk, 156.525e6, -40, "DSC Ch70 FSK", PERMANENT_ARB, "Custom 2FSK / ARB [P]"),
    "vdes": CatalogEntry(gen_vdes, 160.000e6, -40, "VDES-TER OFDM ~25 kHz (ITU-R M.2092)", PERMANENT_ARB, "Custom OFDM ARB [P] - trama ETSI EN 303 706 real -> encoder externo; BBWV:WAV \"vdes.wv\""),
    "dmr": CatalogEntry(gen_4fsk, 466.000e6, -40, "DMR 4FSK", PERMANENT_ARB, "Custom 4FSK / ARB [P] - vocoder real -> ARB externo"),
    "p25": CatalogEntry(gen_4fsk, 460.000e6, -40, "P25 C4FM", PERMANENT_ARB, "Custom 4FSK / ARB [P]"),
    "tetra": CatalogEntry(gen_pi4_dqpsk, 392.000e6, -40, "TETRA pi/4-DQPSK", PERMANENT_ARB, "Custom pi/4-DQPSK / ARB [P]"),
    "epirb": CatalogEntry(functools.partial(gen_psk, order=2, fs=20e3, rs=400, nsym=512, beta=0.5), 406.000e6, -40, "EPIRB COSPAS-SARSAT BPSK 400 bps", PERMANENT_ARB, "Custom BPSK ARB [P]; trama COSPAS-SARSAT real -> encoder externo; BBWV:WAV \"epirb_bpsk.wv\""),
    "nxdn": CatalogEntry(functools.partial(gen_4fsk, fs=50e3, rs=2400, dev=1800), 450.000e6, -40, "NXDN Land Mobile 4FSK 6.25 kHz", PERMANENT_ARB, "Custom 4FSK ARB [P]; NXDN protocol real -> encoder externo; BBWV:WAV \"nxdn_4fsk.wv\""),
    "uhf_satcom": CatalogEntry(gen_psk, 250.000e6, -50, "UHF SATCOM QPSK", PERMANENT_ARB, "Custom PSK / ARB [P]"),
    "lte_dl": CatalogEntry(functools.partial(gen_qam, order=16), 800.000e6, -40, "LTE downlink 16-QAM (band 20 placeholder)", PERMANENT_ARB, "Custom QAM ARB [P]; modulacion LTE real -> toolchain o RsSmw K540-K548 [T]"),
    "noise_jam": CatalogEntry(gen_awgn, 1000.000e6, -30, "Wideband AWGN noise / jamming placeholder", PERMANENT_ARB, "Custom AWGN ARB [P]; ajustar fs para BW de jamming deseado"),
    "adsb": CatalogEntry(gen_ppm_adsb, 1090.000e6, -50, "ADS-B 1090ES PPM (placeholder)", PERMANENT_ARB, "ARB [P] - trama Mode S real (p.ej. pyModeS + CRC)"),
    "radar_barker": CatalogEntry(gen_barker_pulse, 1300.000e6, -20, "L-band ASR Barker pulse compression", PERMANENT_ARB, "Pulse Sequencer K300/K301 (nativo) o ARB Barker [P]; ajustar chip/PRI segun radar objetivo"),
    "gnss_l1": CatalogEntry(functools.partial(gen_psk, order=2), 1575.420e6, -60, "GNSS L1 BPSK (1 SV placeholder)", PERMANENT_ARB, "Opciones GNSS K44/K66/K94/K107 [T]; ARB 1 SV limitado [P]"),
    "iridium": CatalogEntry(functools.partial(gen_pi4_dqpsk, fs=240e3, rs=25000, nsym=256), 1621.000e6, -40, "Iridium TDMA/FDMA pi/4-DQPSK", PERMANENT_ARB, "Custom pi/4-DQPSK ARB [P]; trama Iridium real -> encoder externo; BBWV:WAV \"iridium_dqpsk.wv\""),
    "inmarsat": CatalogEntry(functools.partial(gen_psk, order=4, fs=500e3, rs=48000, nsym=512), 1640.000e6, -40, "Inmarsat IsatPhone/BGAN QPSK (placeholder)", PERMANENT_ARB, "Custom QPSK ARB [P]; modulacion Inmarsat propietaria -> encoder externo; BBWV:WAV \"inmarsat.wv\""),
    "ttc_sband": CatalogEntry(functools.partial(gen_psk, order=2), 2025.000e6, -40, "TT&C S-band BPSK", PERMANENT_ARB, "Custom PCM/PSK / ARB [P]"),
    "radar_s": CatalogEntry(gen_lfm_pulse, 3050.000e6, -20, "Maritime S-band radar LFM", PERMANENT_ARB, "Pulse Sequencer K300/K301 [P] (nativo); K22 pulso [T]; aqui ARB LFM [P]"),
    "ism_24": CatalogEntry(gen_multitone, 2440.000e6, -40, "ISM 2.4 GHz multitone (placeholder)", PERMANENT_ARB, "WLAN/BT/Zigbee opciones [T] si las tuvieras; multitono ARB [P]"),
    "datalink_c": CatalogEntry(gen_psk, 4700.000e6, -40, "C-band data link QPSK", PERMANENT_ARB, "Custom / ARB [P]"),
    "radar_c": CatalogEntry(gen_lfm_pulse, 5400.000e6, -20, "Airborne C-band radar LFM", PERMANENT_ARB, "Pulse Sequencer K300/K301 [P] (nativo); aqui ARB LFM [P]"),
    "satcom_c": CatalogEntry(gen_apsk16, 5850.000e6, -40, "SATCOM C-band 16APSK", PERMANENT_ARB, "Constelacion 16APSK; framing DVB-S2/S2X real -> toolchain o WinIQSIM2 [P/T segun opciones]"),
    "satcom_x": CatalogEntry(gen_psk, 8100.000e6, -40, "X-band satcom QPSK", PERMANENT_ARB, "Custom / ARB [P]"),
    "radar_x": CatalogEntry(gen_lfm_pulse, 9400.000e6, -20, "X-band radar LFM", PERMANENT_ARB, "Pulse Sequencer K300/K301 [P] (nativo); B1044N BW I/Q a verificar; ARB LFM [P]"),
    "ku_dl": CatalogEntry(gen_apsk16, 12200.000e6, -40, "Ku downlink 16APSK", PERMANENT_ARB, "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]"),
    "vsat_ku": CatalogEntry(gen_apsk32, 14100.000e6, -40, "Maritime VSAT DVB-S2/S2X 32APSK", PERMANENT_ARB, "32APSK rings 4+12+16; BBFRAME/FEC DVB-S2X real -> toolchain o WinIQSIM2; BBWV:WAV \"vsat_dvbs2x.wv\""),
    "starlink_ku": CatalogEntry(gen_starlink_ku, 14250.000e6, -40, "Starlink Ku-band uplink OFDM ~250 MHz", PERMANENT_ARB, "Custom wideband OFDM ARB [P] - encoder propietario SpaceX; BBWV:WAV \"starlink_ku.wv\""),
    "ka_dl": CatalogEntry(gen_apsk16, 19500.000e6, -40, "Ka downlink 16APSK", PERMANENT_ARB, "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]"),
    "ka_ul": CatalogEntry(gen_apsk16, 29500.000e6, -40, "Ka uplink 16APSK", PERMANENT_ARB, "16APSK modulacion; BBFRAME/FEC DVB -> export ARB [P]"),
}


# ============================================================================
# Playback on SMW200A
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
    """Generate I/Q, write .wv, upload to SMW200A, set carrier/power, optionally tune FSW."""
    emit = log or print
    e = CATALOG[name]
    call_kw = build_gen_call_kwargs(e, gen_kwargs or {})
    iq, fs = e.gen(**call_kw)
    freq = float(e.carrier_hz if carrier_hz is None else carrier_hz)
    power = float(e.power_dbm if power_dbm is None else power_dbm)
    addr = instr_addr or resolve_instr_addr(None, None)
    span = fsw_span_hz if fsw_span_hz is not None else estimate_fsw_span_hz(fs)
    ref = fsw_ref_dbm if fsw_ref_dbm is not None else default_fsw_ref_dbm(power)

    emit(f"[{name}] generator kwargs: {call_kw}")
    emit(
        f"[{name}] {e.description} | fs={fs / 1e6:.3f} MS/s | N={len(iq)} "
        f"| RF={freq / 1e6:.3f} MHz | {power} dBm | {e.permanent_delivery}"
    )
    emit(f"VISA: {addr}")
    if fsw_ip and fsw_ip.strip():
        emit(
            f"FSW (planned): center {freq / 1e9:.9f} GHz, span {span / 1e6:.3f} MHz, "
            f"RLEV {ref:.1f} dBm @ {fsw_ip.strip()}:{fsw_port}"
        )

    i_data = np.real(iq).astype(float)
    q_data = np.imag(iq).astype(float)

    with VsgSmw200a(visa_resource=addr) as vsg:
        smw = vsg.smw
        emit(f"IDN: {smw.utilities.idn_string}")
        smw.arb_files.create_waveform_file_from_samples(
            i_data, q_data, PC_WV,
            clock_freq=fs, auto_scale=True,
            comment=f"{name}: {e.description}",
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
            emit("RF ON. Use conducted/shielded setup only.")

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
        except OSError as exc:
            emit(f"FSW: could not connect or configure ({fsw_ip.strip()}:{fsw_port}): {exc}")


if __name__ == "__main__":
    with VsgSmw200a() as vsg:
        print("Config:", config_file_path())
        print("Resource:", vsg.visa_resource)
        print("IDN:", vsg.idn())
