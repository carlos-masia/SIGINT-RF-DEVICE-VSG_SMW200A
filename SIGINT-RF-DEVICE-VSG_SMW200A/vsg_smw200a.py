"""
R&S SMW200A VSG control via RsSmw (VISA: HiSLIP or SOCKET).

This module lives in SIGINT-RF-DEVICE-VSG_SMW200A (instrument layer).
The SIGINT-RF-GUI project re-exports the same API through vsg_bridge.py (``from vsg_bridge import VsgSmw200a``).

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

import os
from pathlib import Path
from typing import Any, Literal

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


if __name__ == "__main__":
    with VsgSmw200a() as vsg:
        print("Config:", config_file_path())
        print("Resource:", vsg.visa_resource)
        print("IDN:", vsg.idn())
