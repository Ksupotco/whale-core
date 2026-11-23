"""Devices: paired hardware (phones, glasses, root phones) as capability providers.

    registry  pairing codes, Ed25519 identity, scopes; ledgers for events/actions/notifications
    contract  device-protocol constants + schema-subset validator (schemas in ``contracts/device``)
    gateway   the ``/api/devices/ws`` edge adapter (whale-mobile wire protocol)
    bridge    device capability → core Capability catalog and outcome mapping
    service   ``DeviceService``: lifecycle, RPC, notifications, capability publishing
"""

from __future__ import annotations

from .service import DeviceService
