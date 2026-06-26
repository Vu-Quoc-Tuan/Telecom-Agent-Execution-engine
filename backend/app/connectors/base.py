# backend/app/connectors/base.py
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseConnector(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Khởi tạo kết nối vật lý"""
        pass

    @abstractmethod
    def close(self) -> None:
        """Giải phóng tài nguyên kết nối"""
        pass
