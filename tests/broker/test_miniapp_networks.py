"""Compact pools must not weaken isolation or consume another deployment's space."""

import ipaddress
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import APIError, NotFound
from nautionette_docker_broker import miniapp_networks as pools
from nautionette_docker_broker import miniapps as apps

POOL = "10.240.0.0/24"
DEPLOYMENT = "staging-workflows"


def network(subnet, *, deployment=DEPLOYMENT, app="other"):
    return SimpleNamespace(
        attrs={
            "IPAM": {"Config": [{"Subnet": subnet}]},
            "Labels": {"nautionette.deployment": deployment, "nautionette.miniapp": app},
        }
    )


def test_small_deterministic_subnet_and_full_instance_capacity():
    networks = []
    for index in range(16):
        identity = f"app-{index}"
        selected = pools.subnet(POOL, identity, DEPLOYMENT, networks)
        assert selected == pools.subnet(POOL, identity, DEPLOYMENT, networks)
        parsed = ipaddress.ip_network(selected)
        assert parsed.prefixlen == 29 and parsed.subnet_of(ipaddress.ip_network(POOL))
        assert not any(
            parsed.overlaps(ipaddress.ip_network(n.attrs["IPAM"]["Config"][0]["Subnet"])) for n in networks
        )
        networks.append(network(selected, app=identity))
    assert len({n.attrs["IPAM"]["Config"][0]["Subnet"] for n in networks}) == 16


@pytest.mark.parametrize(
    "cidr",
    [
        "",
        "not-a-cidr",
        "10.0.0.1/24",
        "10.0.0.0/8",
        "10.0.0.0/25",
        "10.0.0.0/29",
        "8.8.8.0/24",
        "169.254.0.0/16",
        "192.0.2.0/24",
        "fd00::/64",
    ],
)
def test_only_reserved_rfc1918_ipv4_pools_are_accepted(cidr):
    with pytest.raises(ValueError, match="MINIAPP_NETWORK_CIDR"):
        pools.subnet(cidr, "app", DEPLOYMENT, [])


def test_rejects_foreign_or_non_miniapp_overlap_without_naming_other_resources():
    for item in (network("10.240.0.0/29", deployment="production"), network("10.240.0.0/16", app="")):
        with pytest.raises(ValueError, match="unrelated Docker network") as error:
            pools.subnet(POOL, "app", DEPLOYMENT, [item])
        assert "production" not in str(error.value)


def test_exhaustion_never_falls_back_to_large_automatic_pools():
    occupied = [network(str(n)) for n in ipaddress.ip_network(POOL).subnets(new_prefix=29)]
    with pytest.raises(ValueError, match="exhausted"):
        pools.subnet(POOL, "app", DEPLOYMENT, occupied)


def test_empty_ipam_and_unrelated_ipv6_are_not_allocated_or_modified():
    rows = [
        SimpleNamespace(attrs={"IPAM": {"Config": None}}),
        network("fd00::/64"),
        network("172.20.0.0/16", deployment="foreign"),
    ]
    assert ipaddress.ip_network(pools.subnet(POOL, "app", DEPLOYMENT, rows)).prefixlen == 29


def test_compact_pool_is_used_without_changing_the_runtime_security_policy(monkeypatch):
    client = Mock()
    client.version.return_value = {"Version": "29.9.2"}
    client.networks.get.side_effect = NotFound("absent")
    client.networks.list.return_value = []
    net = client.networks.create.return_value
    net.attrs = {
        "Internal": True,
        "EnableIPv6": False,
        "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
        "Labels": apps.labels("one"),
    }
    client.containers.get.return_value.attrs = {"NetworkSettings": {"Networks": {}}}
    monkeypatch.setattr(apps.daemon, "client", lambda: client)
    monkeypatch.setattr(apps, "NETWORK_CIDR", POOL)
    assert apps.network("one") is net
    args = client.networks.create.call_args.kwargs
    assert args["internal"] and not args["enable_ipv6"]
    assert args["options"] == {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}
    assert ipaddress.ip_network(args["ipam"]["Config"][0]["Subnet"]).prefixlen == 29
    net.connect.assert_called_once()


def test_existing_owned_isolated_network_is_reused_without_readdressing(monkeypatch):
    client = Mock()
    client.version.return_value = {"Version": "29.9.2"}
    net = client.networks.get.return_value
    net.attrs = {
        "Internal": True,
        "EnableIPv6": False,
        "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
        "Labels": apps.labels("one"),
        "IPAM": {"Config": [{"Subnet": "172.30.0.0/16"}]},
    }
    client.containers.get.return_value.attrs = {"NetworkSettings": {"Networks": {}}}
    monkeypatch.setattr(apps.daemon, "client", lambda: client)
    monkeypatch.setattr(apps, "NETWORK_CIDR", POOL)
    assert apps.network("one") is net
    client.networks.create.assert_not_called()
    client.networks.list.assert_not_called()


def test_automatic_pool_exhaustion_has_actionable_safe_error(monkeypatch):
    client = Mock()
    client.version.return_value = {"Version": "29.9.2"}
    client.networks.get.side_effect = NotFound("absent")
    client.networks.create.side_effect = APIError(
        "all predefined address pools have been fully subnetted: private-data"
    )
    monkeypatch.setattr(apps.daemon, "client", lambda: client)
    monkeypatch.setattr(apps, "NETWORK_CIDR", "")
    with pytest.raises(ValueError, match="Configure MINIAPP_NETWORK_CIDR") as error:
        apps.network("one")
    assert "private-data" not in str(error.value)
    client.containers.get.assert_not_called()
