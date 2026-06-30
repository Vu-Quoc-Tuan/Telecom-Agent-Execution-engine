from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.common.config_parsing import parse_node_host_map
from app.config import settings

router = APIRouter()


def _resource(
    *,
    resource_id: str,
    name: str,
    kind: str,
    configured: bool,
    access: str,
    region: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "name": name,
        "kind": kind,
        "status": "connected" if configured else "disconnected",
        "access": access,
        "region": region,
        "metadata": metadata or {},
    }


@router.get("")
def list_runtime_resources():
    """Return the current connector/resource inventory without exposing credentials."""
    resources: list[dict[str, Any]] = []

    ssh_nodes = [node.strip() for node in settings.SSH_ALLOWED_NODES.split(",") if node.strip()]
    node_host_map = parse_node_host_map(settings.SSH_NODE_HOST_MAP)
    if ssh_nodes:
        for node_name in ssh_nodes:
            resources.append(
                _resource(
                    resource_id=f"ssh-{node_name}",
                    name=node_name,
                    kind="ssh",
                    configured=bool(node_host_map.get(node_name) or settings.SSH_HOST),
                    access="read_write",
                    region=node_host_map.get(node_name, settings.SSH_HOST or "ssh"),
                    metadata={"port": settings.SSH_PORT},
                )
            )
    else:
        resources.append(
            _resource(
                resource_id="ssh-default",
                name=settings.SSH_HOST or "ssh-default",
                kind="ssh",
                configured=bool(settings.SSH_HOST and settings.SSH_USER),
                access="read_write",
                region="SSH connector",
                metadata={"port": settings.SSH_PORT},
            )
        )

    resources.append(
        _resource(
            resource_id="clickhouse-default",
            name=settings.CLICKHOUSE_HOST or "clickhouse-default",
            kind="clickhouse",
            configured=bool(settings.CLICKHOUSE_HOST and settings.CLICKHOUSE_USER),
            access="read_write",
            region=settings.CLICKHOUSE_DATABASE,
            metadata={"port": settings.CLICKHOUSE_PORT, "database": settings.CLICKHOUSE_DATABASE},
        )
    )
    resources.append(
        _resource(
            resource_id="external-postgres-default",
            name=settings.EXTERNAL_POSTGRES_HOST or "external-postgres-default",
            kind="postgres",
            configured=bool(settings.EXTERNAL_POSTGRES_HOST and settings.EXTERNAL_POSTGRES_USER),
            access="read_write",
            region=settings.EXTERNAL_POSTGRES_DATABASE,
            metadata={
                "port": settings.EXTERNAL_POSTGRES_PORT,
                "database": settings.EXTERNAL_POSTGRES_DATABASE,
            },
        )
    )
    return resources
