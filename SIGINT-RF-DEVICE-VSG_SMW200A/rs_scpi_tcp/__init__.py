"""
rs_scpi_tcp — minimal SCPI over TCP for R&S instruments.
Re-exports everything from rs_scpi_tcp.rs_scpi_tcp so that
``from rs_scpi_tcp import ScpiTcp, smw_query_idn, ...`` keeps working.
"""

from rs_scpi_tcp.rs_scpi_tcp import (
    ScpiTcp,
    fsw_bw_summary,
    fsw_configure_spectrum,
    opc_wait,
    smw_query_idn,
    smw_set_cw,
)

__all__ = [
    "ScpiTcp",
    "opc_wait",
    "smw_query_idn",
    "smw_set_cw",
    "fsw_configure_spectrum",
    "fsw_bw_summary",
]
