"""Кастомная сессия aiogram: опционально только IPv4 (частая проблема на Windows с api.telegram.org)."""

from __future__ import annotations

import socket
from typing import Any

from aiogram.client.session.aiohttp import AiohttpSession


class IPv4AiohttpSession(AiohttpSession):
    """
    Принудительно IPv4 для исходящих соединений.
    Если у провайдера «битый» IPv6 до Telegram, без этого aiohttp может долго таймаутиться.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connector_init["family"] = socket.AF_INET
