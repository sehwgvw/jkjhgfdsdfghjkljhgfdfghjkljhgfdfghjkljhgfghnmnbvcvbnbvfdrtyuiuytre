from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

from config import ARCHIVE_DIR, SESSIONS_DIR, Settings

logger = logging.getLogger(__name__)


@dataclass
class ModerationSession:
    name: str
    client: TelegramClient
    flood_until: float = 0


@dataclass
class ReportTask:
    target: str
    reason_key: str
    text: str


class ComplianceEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: list[ModerationSession] = []
        self.queue: asyncio.Queue[ReportTask] = asyncio.Queue()
        self._sem = asyncio.Semaphore(settings.max_parallel_reports)

    async def initialize(self) -> dict[str, int]:
        await self.disconnect_all()
        self.sessions.clear()

        session_files = list(SESSIONS_DIR.glob("*.session"))
        live = 0
        archived = 0

        for path in session_files:
            session_name = path.stem
            client = TelegramClient(
                str(path.with_suffix("")),
                self.settings.api_id,
                self.settings.api_hash,
                proxy=None,
                request_retries=1,
                connection_retries=1,
            )
            try:
                await self._connect_with_backoff(client)
                if not await client.is_user_authorized():
                    raise RuntimeError("Session is not authorized")
                await client.get_me()
                self.sessions.append(ModerationSession(session_name, client))
                live += 1
            except Exception:
                archived += 1
                try:
                    await client.disconnect()
                except Exception:
                    pass
                await self._archive_session(path)

        return {"sessions_total": len(session_files), "sessions_live": live, "sessions_archived": archived}

    async def enqueue_reports(self, target: str, reason_key: str, count: int, text: str) -> None:
        for _ in range(count):
            await self.queue.put(ReportTask(target=target, reason_key=reason_key, text=text))

    async def get_target_info(self, target: str) -> dict:
        if not self.sessions:
            raise RuntimeError("No live sessions loaded")
        session = random.choice(self.sessions)
        entity = await session.client.get_entity(self._normalize_target(target))
        return {
            "target": target,
            "id": getattr(entity, "id", None),
            "access_hash": getattr(entity, "access_hash", None),
            "type": entity.__class__.__name__,
        }

    async def process_queue(self, dry_log_cb: Callable[[str], Awaitable[None]]) -> None:
        workers = [asyncio.create_task(self._drain_worker(dry_log_cb)) for _ in range(self.settings.queue_workers)]
        await self.queue.join()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await dry_log_cb("Queue is empty")

    async def report_once(self, item: ReportTask, dry_log_cb: Callable[[str], Awaitable[None]]) -> None:
        if not self.sessions:
            await dry_log_cb("No live sessions available")
            return
        session = random.choice(self.sessions)
        now = asyncio.get_running_loop().time()
        if session.flood_until > now:
            wait_left = int(session.flood_until - now)
            await dry_log_cb(f"{session.name} -> Waiting (Flood {wait_left}s)")
            return

        async with self._sem:
            try:
                await self._send_report(session, item)
                await dry_log_cb(f"{session.name} -> Success")
            except FloodWaitError as flood:
                session.flood_until = asyncio.get_running_loop().time() + flood.seconds
                await dry_log_cb(f"{session.name} -> Waiting (Flood {flood.seconds}s)")
            except ConnectionError:
                await dry_log_cb(f"{session.name} -> Failed (connection)")
            except Exception as exc:
                logger.exception("Report error: %s", exc)
                await dry_log_cb(f"{session.name} -> Failed ({type(exc).__name__})")

    async def reload(self) -> dict[str, int]:
        return await self.initialize()

    async def disconnect_all(self) -> None:
        for session in self.sessions:
            try:
                await session.client.disconnect()
            except Exception:
                pass

    async def _drain_worker(self, dry_log_cb: Callable[[str], Awaitable[None]]) -> None:
        while True:
            task = await self.queue.get()
            try:
                await self.report_once(task, dry_log_cb)
            finally:
                self.queue.task_done()

    async def _send_report(self, session: ModerationSession, item: ReportTask) -> None:
        client = session.client
        normalized = self._normalize_target(item.target)

        if self._looks_like_joinable_link(normalized):
            try:
                await client(functions.messages.ImportChatInviteRequest(normalized.split("/")[-1]))
            except Exception:
                pass

        entity = await client.get_entity(normalized)
        reason = self._reason_to_tl(item.reason_key)

        await client(functions.account.ReportPeerRequest(peer=entity, reason=reason, message=item.text))

    async def validate_and_prune_sessions(self) -> list[str]:
        removed: list[str] = []
        for session in list(self.sessions):
            try:
                if not await session.client.is_user_authorized():
                    raise RuntimeError("not authorized")
                await asyncio.wait_for(session.client.get_me(), timeout=60)
            except Exception:
                name = session.name
                try:
                    await session.client.disconnect()
                except Exception:
                    pass
                self.sessions = [s for s in self.sessions if s.name != name]
                path = SESSIONS_DIR / f"{name}.session"
                if path.exists():
                    await self._archive_session(path)
                removed.append(name)
        return removed

    async def _connect_with_backoff(self, client: TelegramClient) -> None:
        delays = [1, 2, 4, 8, 16]
        for delay in delays:
            try:
                await client.connect()
                return
            except Exception:
                await asyncio.sleep(delay)
        raise ConnectionError("Failed to connect")

    async def _archive_session(self, path: Path) -> None:
        dest = ARCHIVE_DIR / path.name
        suffix = 1
        while dest.exists():
            dest = ARCHIVE_DIR / f"{path.stem}_{suffix}.session"
            suffix += 1
        for _ in range(5):
            try:
                path.replace(dest)
                return
            except PermissionError:
                await asyncio.sleep(0.5)

    def _reason_to_tl(self, reason_key: str):
        mapping = {
            "spam": types.InputReportReasonSpam(),
            "violence": types.InputReportReasonViolence(),
            "pornography": types.InputReportReasonPornography(),
            "childabuse": types.InputReportReasonChildAbuse(),
            "copyright": types.InputReportReasonCopyright(),
            "other": types.InputReportReasonOther(),
        }
        return mapping.get(reason_key.lower(), types.InputReportReasonOther())

    def _normalize_target(self, target: str) -> str:
        target = target.strip()
        if target.startswith("https://") or target.startswith("http://"):
            parsed = urlparse(target)
            return parsed.path.strip("/")
        if target.startswith("@"):
            return target
        return target

    def _looks_like_joinable_link(self, normalized: str) -> bool:
        return normalized.startswith("+") or "joinchat" in normalized
