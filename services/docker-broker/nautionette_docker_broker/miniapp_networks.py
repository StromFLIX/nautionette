"""Compact, explicitly reserved IPv4 space for isolated per-app bridges.

Never changes daemon-wide pools or deletes/reconfigures another network. Docker
IPAM performs the final atomic overlap check; a caller cannot choose subnets.
"""

from __future__ import annotations

import hashlib
import ipaddress

_PRIVATE = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def subnet(cidr: str, identity: str, deployment: str, networks) -> str:
    try:
        pool = ipaddress.ip_network(cidr)
    except ValueError as exc:
        raise ValueError(
            "MINIAPP_NETWORK_CIDR must be a reserved RFC1918 IPv4 network (/16 through /24)"
        ) from exc
    if pool.version != 4 or not 16 <= pool.prefixlen <= 24 or not any(pool.subnet_of(p) for p in _PRIVATE):
        raise ValueError("MINIAPP_NETWORK_CIDR must be a reserved RFC1918 IPv4 network (/16 through /24)")
    occupied = []
    for network in networks:
        attrs = network.attrs
        for config in (attrs.get("IPAM") or {}).get("Config") or []:
            if not config.get("Subnet"):
                continue
            existing = ipaddress.ip_network(config["Subnet"], strict=False)
            if existing.version != 4 or not pool.overlaps(existing):
                continue
            labels = attrs.get("Labels") or {}
            if labels.get("nautionette.deployment") != deployment or not labels.get("nautionette.miniapp"):
                raise ValueError(
                    "MINIAPP_NETWORK_CIDR overlaps an unrelated Docker network; reserve a different pool"
                )
            occupied.append(existing)
    candidates = list(pool.subnets(new_prefix=29))
    offset = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], "big") % len(candidates)
    for index in range(len(candidates)):
        candidate = candidates[(offset + index) % len(candidates)]
        if not any(candidate.overlaps(used) for used in occupied):
            return str(candidate)
    raise ValueError("MINIAPP_NETWORK_CIDR is exhausted; reserve a larger non-overlapping pool")
