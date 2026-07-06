from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from app.common.exceptions import ConnectorExecutionError
from app.connectors.base import BaseConnector


class TelcoPostgresConnector(BaseConnector):
    """Dedicated connector for the external telecom PostgreSQL database."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
        read_only: bool = True,
        timeout_seconds: int = 15,
        max_result_rows: int = 1000,
    ) -> None:
        if not host:
            raise ConnectorExecutionError("EXTERNAL_POSTGRES_HOST is not configured.")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.read_only = read_only
        self.timeout_seconds = timeout_seconds
        self.max_result_rows = max_result_rows
        self._engine: Engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username=username,
                password=password,
                host=host,
                port=port,
                database=database,
            ),
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": timeout_seconds,
                "options": f"-c statement_timeout={timeout_seconds * 1000}",
            },
        )

    def connect(self) -> None:
        with self._engine.connect():
            pass

    def _sync_execute_sql(
        self,
        sql: str,
        params: dict | None = None,
    ) -> list[dict[str, Any]]:
        bind_params = params or {}
        statement = text(sql)
        with self._engine.begin() as connection:
            if self.read_only:
                # The external DB boundary is enforced by transaction/user permissions.
                connection.execute(text("SET TRANSACTION READ ONLY"))
            result = connection.execute(statement, bind_params)
            if result.returns_rows:
                return [dict(row._mapping) for row in result.fetchmany(self.max_result_rows)]
            return [{"status": "SUCCESS", "affected_rows": result.rowcount}]

    async def execute_query(
        self,
        sql: str,
        params: dict | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy.exc import OperationalError

        retries = 3
        delay = 1.0
        for attempt in range(retries):
            try:
                return await asyncio.to_thread(self._sync_execute_sql, sql, params)
            except OperationalError as exc:
                if attempt == retries - 1:
                    raise ConnectorExecutionError(
                        f"External PostgreSQL query failed (connection error after {retries} attempts): {exc}",
                        details={"host": self.host, "database": self.database},
                    ) from exc
                # Dispose and recreate engine pool to recover from stale connection/pool state
                self._engine.dispose()
                await asyncio.sleep(delay)
                delay *= 2
            except ConnectorExecutionError:
                raise
            except Exception as exc:
                raise ConnectorExecutionError(
                    f"External PostgreSQL query failed: {exc}",
                    details={"host": self.host, "database": self.database},
                ) from exc

    async def query(
        self,
        sql: str,
        params: dict | None = None,
    ) -> list[dict[str, Any]]:
        return await self.execute_query(sql, params)

    def close(self) -> None:
        self._engine.dispose()
