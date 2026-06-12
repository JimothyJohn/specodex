from __future__ import annotations

from typing import Literal, get_args


CommunicationProtocol = Literal[
    "EtherCAT",
    "EtherNet/IP",
    "PROFINET",
    "Modbus TCP",
    "Modbus RTU",
    "CANopen",
    "POWERLINK",
    "Sercos III",
    "CC-Link IE",
]

COMMUNICATION_PROTOCOLS: tuple[str, ...] = get_args(CommunicationProtocol)
