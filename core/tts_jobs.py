from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


JobSource = Literal["command", "auto"]
JobStatus = Literal[
    "queued",
    "running",
    "delivering",
    "completed",
    "failed",
    "cancelled",
]
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True, slots=True)
class TTSJob:
    session: str
    text: str
    voice: str = ""
    emotion: str = ""
    context: str = ""
    user_id: str = ""
    group_id: str = ""
    source: JobSource = "auto"
    notify_on_failure: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TTSJob":
        source = str(data.get("source") or "auto")
        return cls(
            session=str(data.get("session") or ""),
            text=str(data.get("text") or ""),
            voice=str(data.get("voice") or ""),
            emotion=str(data.get("emotion") or ""),
            context=str(data.get("context") or ""),
            user_id=str(data.get("user_id") or ""),
            group_id=str(data.get("group_id") or ""),
            source="command" if source == "command" else "auto",
            notify_on_failure=bool(data.get("notify_on_failure", False)),
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass(slots=True)
class _JobRecord:
    job: TTSJob
    priority: int
    sequence: int
    status: JobStatus = "queued"
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    output_path: str = ""
    recovered: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_JobRecord":
        status = str(data.get("status") or "queued")
        if status not in {"queued", "running", "delivering", *TERMINAL_STATUSES}:
            status = "queued"
        return cls(
            job=TTSJob.from_dict(dict(data.get("job") or {})),
            priority=int(data.get("priority") or 10),
            sequence=int(data.get("sequence") or 0),
            status=status,  # type: ignore[arg-type]
            started_at=float(data.get("started_at") or 0.0),
            finished_at=float(data.get("finished_at") or 0.0),
            error=str(data.get("error") or ""),
            output_path=str(data.get("output_path") or ""),
            recovered=bool(data.get("recovered", False)),
        )

    def persist_dict(self) -> dict[str, Any]:
        return {
            "job": asdict(self.job),
            "priority": self.priority,
            "sequence": self.sequence,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "output_path": self.output_path,
            "recovered": self.recovered,
        }

    def public_dict(self) -> dict[str, Any]:
        preview = self.job.text.replace("\r", " ").replace("\n", " " ).strip()
        if len(preview) > 120:
            preview = preview[:120].rstrip() + "..."
        return {
            "id": self.job.id,
            "status": self.status,
            "source": self.job.source,
            "session": self.job.session,
            "text_preview": preview,
            "created_at": self.job.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "has_output": bool(self.output_path and Path(self.output_path).is_file()),
            "recovered": self.recovered,
        }


class TTSJobManager:
    STORE_VERSION = 1

    def __init__(
        self,
        *,
        processor: Callable[[TTSJob], Awaitable[Path]],
        deliverer: Callable[[TTSJob, Path], Awaitable[None]],
        failure_handler: Callable[[TTSJob, Exception], Awaitable[None]],
        max_queue_size: int = 20,
        worker_count: int = 1,
        persistence_path: str | Path | None = None,
        history_size: int = 100,
        recovery_max_age_seconds: float = 24 * 3600,
    ):
        self._processor = processor
        self._deliverer = deliverer
        self._failure_handler = failure_handler
        self._max_queue_size = max(1, int(max_queue_size))
        self._worker_count = max(1, int(worker_count))
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._history_size = max(10, int(history_size))
        self._recovery_max_age_seconds = max(0.0, float(recovery_max_age_seconds))
        self._pending: list[_JobRecord] = []
        self._records: OrderedDict[str, _JobRecord] = OrderedDict()
        self._active_sessions: set[str] = set()
        self._running_tasks: dict[str, asyncio.Task[Path]] = {}
        self._cancel_requested: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._condition = asyncio.Condition()
        self._idle = asyncio.Event()
        self._idle.set()
        self._sequence = 0
        self._accepting = True
        self._stopping = False
        self._stats: dict[str, float | int | str] = {
            "completed_jobs": 0,
            "failed_jobs": 0,
            "cancelled_jobs": 0,
            "recovered_jobs": 0,
            "dropped_jobs": 0,
            "total_latency_seconds": 0.0,
            "last_error": "",
        }
        self._load()

    def _load(self) -> None:
        path = self._persistence_path
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stats = raw.get("stats") or {}
            for key in self._stats:
                if key in stats:
                    self._stats[key] = stats[key]
            now = time.time()
            for item in list(raw.get("jobs") or []):
                record = _JobRecord.from_dict(item)
                if not record.job.session or not record.job.text or not record.job.id:
                    continue
                self._sequence = max(self._sequence, record.sequence)
                output_ready = bool(
                    record.output_path and Path(record.output_path).is_file()
                )
                recoverable_delivery_failure = (
                    record.status == "failed" and output_ready
                )
                if record.status not in TERMINAL_STATUSES or recoverable_delivery_failure:
                    age = max(0.0, now - record.job.created_at)
                    if self._recovery_max_age_seconds and age > self._recovery_max_age_seconds:
                        record.status = "cancelled"
                        record.finished_at = now
                        record.error = "expired before recovery"
                        self._stats["cancelled_jobs"] = int(self._stats["cancelled_jobs"]) + 1
                    else:
                        record.status = "queued"
                        record.started_at = 0.0
                        record.finished_at = 0.0
                        record.error = ""
                        record.recovered = True
                        self._pending.append(record)
                        self._stats["recovered_jobs"] = int(self._stats["recovered_jobs"]) + 1
                self._records[record.job.id] = record
            if self._pending:
                self._idle.clear()
            self._prune_history()
            self._persist()
        except Exception:
            corrupt = path.with_suffix(path.suffix + f".corrupt.{int(time.time())}")
            try:
                path.replace(corrupt)
            except OSError:
                pass

    def _persist(self) -> None:
        path = self._persistence_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = {
            "version": self.STORE_VERSION,
            "stats": self._stats,
            "jobs": [record.persist_dict() for record in self._records.values()],
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"mimo-tts-worker-{index}")
            for index in range(self._worker_count)
        ]

    async def submit(self, job: TTSJob) -> bool:
        self.start()
        async with self._condition:
            if not self._accepting or len(self._pending) >= self._max_queue_size:
                self._stats["dropped_jobs"] = int(self._stats["dropped_jobs"]) + 1
                self._persist()
                return False
            self._sequence += 1
            record = _JobRecord(
                job=job,
                priority=0 if job.source == "command" else 10,
                sequence=self._sequence,
            )
            self._records[job.id] = record
            self._pending.append(record)
            self._idle.clear()
            self._prune_history()
            self._persist()
            self._condition.notify_all()
            return True

    def snapshot(self) -> dict[str, object]:
        completed = int(self._stats["completed_jobs"])
        failed = int(self._stats["failed_jobs"])
        cancelled = int(self._stats["cancelled_jobs"])
        finished = completed + failed + cancelled
        return {
            "queued_jobs": len(self._pending),
            "running_jobs": len(self._running_tasks),
            **self._stats,
            "average_latency_seconds": (
                round(float(self._stats["total_latency_seconds"]) / finished, 3)
                if finished
                else 0.0
            ),
        }

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        records = sorted(self._records.values(), key=lambda item: item.sequence, reverse=True)
        return [record.public_dict() for record in records[: max(1, min(200, int(limit)))]]

    def protected_output_paths(self) -> set[Path]:
        """Return complete WAVs that may still need delivery or recovery."""
        return {
            Path(record.output_path).resolve()
            for record in self._records.values()
            if record.output_path
            and record.status in {"queued", "running", "delivering", "failed"}
            and Path(record.output_path).is_file()
        }

    async def cancel(self, job_id: str) -> bool:
        async with self._condition:
            record = self._records.get(str(job_id or ""))
            if record is None or record.status in TERMINAL_STATUSES:
                return False
            if record in self._pending:
                self._pending.remove(record)
                self._finish_cancelled(record, "cancelled by operator")
                self._persist()
                if not self._pending and not self._running_tasks:
                    self._idle.set()
                self._condition.notify_all()
                return True
            task = self._running_tasks.get(record.job.id)
            if task is None:
                return False
            self._cancel_requested.add(record.job.id)
            task.cancel()
            return True

    async def clear(self, *, include_active: bool = False) -> int:
        async with self._condition:
            affected_ids: set[str] = set()
            if include_active:
                for record in list(self._pending):
                    self._pending.remove(record)
                    self._finish_cancelled(record, "cleared by operator")
                    affected_ids.add(record.job.id)
                for job_id, task in list(self._running_tasks.items()):
                    self._cancel_requested.add(job_id)
                    task.cancel()
                    affected_ids.add(job_id)
            for job_id, record in list(self._records.items()):
                if record.status in TERMINAL_STATUSES:
                    self._records.pop(job_id, None)
                    affected_ids.add(job_id)
            self._persist()
            if not self._pending and not self._running_tasks:
                self._idle.set()
            self._condition.notify_all()
            return len(affected_ids)

    async def stop(self, *, drain_timeout: float = 5.0) -> None:
        self._accepting = False
        if self._workers and (self._pending or self._running_tasks):
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=max(0.0, drain_timeout))
            except TimeoutError:
                pass
        self._stopping = True
        async with self._condition:
            self._condition.notify_all()
        for worker in self._workers:
            if not worker.done():
                worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        async with self._condition:
            now = time.time()
            for record in self._records.values():
                if record.status not in TERMINAL_STATUSES:
                    record.status = "queued"
                    record.started_at = 0.0
                    record.finished_at = 0.0
                    record.error = ""
                    record.recovered = True
            self._workers.clear()
            self._running_tasks.clear()
            self._active_sessions.clear()
            self._cancel_requested.clear()
            self._persist()
            self._idle.set()

    def _take_next(self) -> _JobRecord | None:
        first_by_session: dict[str, _JobRecord] = {}
        for record in self._pending:
            session = record.job.session
            current = first_by_session.get(session)
            if current is None or record.sequence < current.sequence:
                first_by_session[session] = record
        candidates = [
            record
            for session, record in first_by_session.items()
            if session not in self._active_sessions
        ]
        if not candidates:
            return None
        selected = min(candidates, key=lambda item: (item.priority, item.sequence))
        self._pending.remove(selected)
        self._active_sessions.add(selected.job.session)
        selected.status = "running"
        selected.started_at = time.time()
        selected.error = ""
        self._persist()
        return selected

    async def _execute(self, record: _JobRecord) -> Path:
        output = Path(record.output_path) if record.output_path else None
        if output is None or not output.is_file():
            output = await self._processor(record.job)
            async with self._condition:
                record.output_path = str(output)
                record.status = "delivering"
                self._persist()
        else:
            async with self._condition:
                record.status = "delivering"
                self._persist()
        await self._deliverer(record.job, output)
        return output

    async def _worker(self, _index: int) -> None:
        while True:
            async with self._condition:
                selected = self._take_next()
                while selected is None and not self._stopping:
                    if not self._pending and not self._running_tasks:
                        self._idle.set()
                    await self._condition.wait()
                    selected = self._take_next()
                if selected is None and self._stopping:
                    return
                task = asyncio.create_task(
                    self._execute(selected),
                    name=f"mimo-tts-job-{selected.job.id}",
                )
                self._running_tasks[selected.job.id] = task

            try:
                await task
            except asyncio.CancelledError:
                async with self._condition:
                    if selected.job.id in self._cancel_requested:
                        self._finish_cancelled(selected, "cancelled by operator")
                    else:
                        selected.status = "queued"
                        selected.started_at = 0.0
                        selected.error = ""
                        selected.recovered = True
                    self._persist()
                if self._stopping:
                    return
            except Exception as exc:
                async with self._condition:
                    selected.status = "failed"
                    selected.finished_at = time.time()
                    selected.error = f"{type(exc).__name__}: {exc}"
                    self._stats["failed_jobs"] = int(self._stats["failed_jobs"]) + 1
                    self._stats["last_error"] = selected.error
                    self._add_latency(selected)
                    self._persist()
                try:
                    await self._failure_handler(selected.job, exc)
                except Exception:
                    pass
            else:
                async with self._condition:
                    selected.status = "completed"
                    selected.finished_at = time.time()
                    selected.error = ""
                    self._stats["completed_jobs"] = int(self._stats["completed_jobs"]) + 1
                    self._add_latency(selected)
                    self._persist()
            finally:
                async with self._condition:
                    self._running_tasks.pop(selected.job.id, None)
                    self._cancel_requested.discard(selected.job.id)
                    self._active_sessions.discard(selected.job.session)
                    self._prune_history()
                    self._persist()
                    if not self._pending and not self._running_tasks:
                        self._idle.set()
                    self._condition.notify_all()

    def _finish_cancelled(self, record: _JobRecord, reason: str) -> None:
        record.status = "cancelled"
        record.finished_at = time.time()
        record.error = reason
        self._stats["cancelled_jobs"] = int(self._stats["cancelled_jobs"]) + 1
        self._add_latency(record)

    def _add_latency(self, record: _JobRecord) -> None:
        self._stats["total_latency_seconds"] = float(
            self._stats["total_latency_seconds"]
        ) + max(0.0, time.time() - record.job.created_at)

    def _prune_history(self) -> None:
        terminal = [
            record for record in self._records.values() if record.status in TERMINAL_STATUSES
        ]
        excess = len(terminal) - self._history_size
        if excess <= 0:
            return
        for record in sorted(terminal, key=lambda item: item.sequence)[:excess]:
            self._records.pop(record.job.id, None)
