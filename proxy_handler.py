"""
Прокси используются только для веб-формы telegram.org/support (Playwright).
Bot API и Telethon-сессии работают без прокси.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    scheme: str
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]

    def as_playwright_dict(self) -> dict:
        server = f"{self.scheme}://{self.host}:{self.port}"
        d: dict = {"server": server}
        if self.username and self.password:
            d["username"] = self.username
            d["password"] = self.password
        return d


class ProxyPool:
    def __init__(self, proxies_dir: Path):
        self.proxies_dir = proxies_dir
        self._pool: List[ProxyConfig] = []
        self.reload()

    @property
    def count(self) -> int:
        return len(self._pool)

    def reload(self) -> None:
        self._pool.clear()
        self.proxies_dir.mkdir(parents=True, exist_ok=True)
        for fp in self.proxies_dir.glob("*.txt"):
            try:
                for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    for p in self._parse_line(line):
                        if p not in self._pool:
                            self._pool.append(p)
            except Exception as exc:
                logger.debug("Proxy file %s: %s", fp.name, exc)

    def pick(self, _key: str = "", mode: str = "rotate") -> Optional[ProxyConfig]:
        if not self._pool:
            return None
        if mode == "random":
            import random
            return random.choice(self._pool)
        idx = hash(_key) % len(self._pool) if _key else 0
        return self._pool[idx % len(self._pool)]

    def _parse_line(self, line: str) -> Iterable[ProxyConfig]:
        line = re.sub(r"\s+", " ", line.strip())
        parts = re.split(r"[\s:@/]+", line)
        if len(parts) < 2:
            return
        scheme = "http"
        if parts[0].lower() in ("http", "https", "socks4", "socks5"):
            scheme = parts[0].lower()
            parts = parts[1:]
        try:
            host = parts[0]
            port = int(parts[1])
            username = parts[2] if len(parts) > 3 else None
            password = parts[3] if len(parts) > 4 else None
            if not username and len(parts) == 3:
                password = parts[2]
            yield ProxyConfig(scheme=scheme, host=host, port=port, username=username, password=password)
        except (ValueError, IndexError):
            pass

    def _parse_noisy_text(self, text: str) -> Iterable[ProxyConfig]:
        seen: set[str] = set()
        for line in text.replace("\r", "\n").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for part in re.split(r"[,;]", line):
                part = part.strip()
                if not part:
                    continue
                for p in self._parse_line(part):
                    key = f"{p.scheme}://{p.host}:{p.port}"
                    if key not in seen:
                        seen.add(key)
                        yield p
