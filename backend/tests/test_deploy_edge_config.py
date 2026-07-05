from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_defines_single_origin_edge_proxy_for_ngrok() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "edge" in services
    edge = services["edge"]

    assert edge["image"].startswith("nginx:")
    assert "frontend" in edge["depends_on"]
    assert any(mapping == "host.docker.internal:host-gateway" for mapping in edge["extra_hosts"])
    assert edge["environment"]["BACKEND_PORT"] == "${BACKEND_PORT:-8000}"
    assert any(
        "${BIND_ADDRESS:-127.0.0.1}:${EDGE_PORT:-8080}:8080" == port
        for port in edge["ports"]
    )
    assert any(
        (
            "./deploy/nginx/ngrok-edge.conf:"
            "/etc/nginx/templates/default.conf.template:ro"
        )
        == volume
        for volume in edge["volumes"]
    )
    assert edge["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget -qO- http://127.0.0.1:8080/health >/dev/null || exit 1",
    ]


def test_compose_binds_host_ports_to_loopback_by_default() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["postgres"]["ports"] == [
        "${BIND_ADDRESS:-127.0.0.1}:${POSTGRES_PORT:-5432}:5432"
    ]
    assert services["frontend"]["ports"] == [
        "${BIND_ADDRESS:-127.0.0.1}:${FRONTEND_PORT:-3000}:3000"
    ]


def test_edge_proxy_routes_api_to_backend_without_sse_buffering() -> None:
    config = (REPO_ROOT / "deploy/nginx/ngrok-edge.conf").read_text(encoding="utf-8")

    api_location_match = re.search(r"location\s+/api/v1/\s*\{(?P<body>.*?)\n\s*\}", config, re.S)
    assert api_location_match is not None
    api_location = api_location_match.group("body")

    assert "proxy_buffering off;" in api_location
    assert "proxy_cache off;" in api_location
    assert "proxy_read_timeout 3600s;" in api_location
    assert "proxy_pass http://backend_api/api/v1/;" in api_location
    assert "add_header X-Accel-Buffering no always;" in api_location
    assert "server host.docker.internal:${BACKEND_PORT};" in config
    assert "server frontend:3000;" in config
    assert "client_max_body_size 12m;" in config
    assert "map $http_x_forwarded_proto $upstream_forwarded_proto" in config
    assert "proxy_set_header X-Forwarded-Proto $upstream_forwarded_proto;" in config
    assert "events {" not in config
    assert "http {" not in config


def test_deploy_workflow_supports_stable_single_ngrok_url() -> None:
    workflow = (REPO_ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "public_url:" in workflow
    assert 'upsert_env NEXT_PUBLIC_API_BASE_URL "/api/v1"' in workflow
    assert "upsert_env CORS_ORIGINS" in workflow
    assert "http://localhost:8080" in workflow
    assert "docker compose up --build -d --remove-orphans postgres frontend edge" in workflow
    assert "http://127.0.0.1:8080/health" in workflow
    assert "http://127.0.0.1:8080/chat" in workflow
    assert "ngrok-skip-browser-warning" in workflow
    assert '"service":"telecom-ai-agent-backend"' in workflow
    assert 'docker build -t "$sandbox_image" -f sandbox.Dockerfile .' in workflow
    assert 'case "${NGROK_MANAGED:-false}"' in workflow
    assert "--network host" in workflow
    assert "http://127.0.0.1:8080" in workflow
    assert '"${NGROK_INTERNAL_URL:-https://default.internal}"' in workflow


def test_ci_runs_frontend_unit_and_database_integration_tests() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'RUN_INTEGRATION_TESTS: "1"' in workflow
    assert "npm test" in workflow


def test_make_up_can_include_public_ngrok_origin_for_local_smoke_tests() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PUBLIC_URL=... make up" in makefile
    assert "public_url=$${PUBLIC_URL:-}" in makefile
    assert 'local_cors_origins="$$public_url,$$local_cors_origins"' in makefile
    assert "CORS_ORIGINS=$${CORS_ORIGINS:-$$local_cors_origins}" in makefile
    assert "cd $(FRONTEND_DIR) && npm test" in makefile
    assert "$(COMPOSE) up -d postgres frontend edge" in makefile


def test_redteam_http_provider_declares_its_direct_httpx_dependency() -> None:
    pyproject = (REPO_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")

    assert '"httpx>=0.28.1,<1"' in pyproject
