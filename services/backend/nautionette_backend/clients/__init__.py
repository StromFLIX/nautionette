"""Clients for everything outside this process.

Each module is named after the service it talks to; each singleton is named
after the role that service plays here.
"""

from __future__ import annotations

from .agentgateway import GatewayClient, gateway
from .docker_broker import BrokerClient, broker
from .http import internal_headers, upstream_problem
from .provider_catalog import ModelCatalogClient, model_catalog
from .temporal_server import TemporalGateway, temporal
from .workflow_mcp import AuthoringClient, authoring

__all__ = [
    "AuthoringClient",
    "BrokerClient",
    "GatewayClient",
    "ModelCatalogClient",
    "TemporalGateway",
    "authoring",
    "broker",
    "gateway",
    "internal_headers",
    "model_catalog",
    "temporal",
    "upstream_problem",
]
