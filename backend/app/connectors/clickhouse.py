# backend/app/connectors/clickhouse.py
from __future__ import annotations

import asyncio
from typing import Any

import clickhouse_connect

from app.common.exceptions import ConnectorExecutionError
from app.connectors.base import BaseConnector


class TelcoClickHouseConnector(BaseConnector):
    def __init__(
        self,
        host: str,
        port: int = 8123,
        username: str = "default",
        password: str = "",
        database: str | None = None,
        timeout_seconds: int = 15,
        max_result_rows: int = 1000,
    ):
        if not host:
            raise ConnectorExecutionError(
                "ClickHouse host is not configured.",
                details={"connector": "clickhouse"},
            )
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.timeout_seconds = timeout_seconds
        self.max_result_rows = max_result_rows
        self._client = None

    def connect(self) -> None:
        if self._client is not None:
            return
        kwargs = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "connect_timeout": self.timeout_seconds,
            "send_receive_timeout": self.timeout_seconds,
        }
        if self.database:
            kwargs["database"] = self.database
        self._client = clickhouse_connect.get_client(**kwargs)

    def _sync_query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        self.connect()
        # Thực thi câu lệnh truy vấn log viễn thông
        result = self._client.query(
            sql,
            parameters=params,
            settings={
                "readonly": 2,
                "max_result_rows": self.max_result_rows,
                "result_overflow_mode": "break",
            },
        )

        # Chuyển đổi dữ liệu bảng thô về dạng List[Dict] sạch để Agent dễ đọc hiểu cấu trúc
        return [dict(zip(result.column_names, row, strict=False)) for row in result.result_rows]

    def _sync_execute(
        self,
        sql: str,
        params: dict | None = None,
        *,
        allow_mutation: bool = False,
    ) -> list[dict[str, Any]]:
        if not allow_mutation:
            return self._sync_query(sql, params)
        self.connect()
        result = self._client.command(
            sql,
            parameters=params,
            settings={"readonly": 0},
        )
        return [{"status": "SUCCESS", "result": result}]

    async def execute(
        self,
        sql: str,
        params: dict | None = None,
        *,
        allow_mutation: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(
                self._sync_execute,
                sql,
                params,
                allow_mutation=allow_mutation,
            )
        except Exception as exc:
            raise ConnectorExecutionError(
                f"Lỗi thực thi SQL ClickHouse: {exc}",
                details={"host": self.host},
            ) from exc

    async def query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """
        Hàm dành cho kỹ sư query dữ liệu log/alarm tập trung.
        Bảo vệ Parameterized queries 100%, chống SQL Injection bằng tham số truyền vào.
        """
        try:
            return await asyncio.to_thread(self._sync_query, sql, params)
        except Exception as e:
            raise ConnectorExecutionError(
                f"Lỗi truy vấn cơ sở dữ liệu ClickHouse: {str(e)}",
                details={"host": self.host},
            ) from e

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
