from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


class SlidingWindowRateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.limit = max(1, int(requests_per_minute))
        self._clock = clock
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.wait_count = 0
        self.total_wait_seconds = 0.0

    async def acquire(self) -> None:
        while True:
            wait_for = 0.0
            async with self._lock:
                now = self._clock()
                cutoff = now - 60.0
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                wait_for = max(0.001, 60.0 - (now - self._timestamps[0]))
                self.wait_count += 1
                self.total_wait_seconds += wait_for
            await self._sleep(wait_for)

    def snapshot(self) -> dict[str, int | float]:
        return {
            "rate_limit_rpm": self.limit,
            "rate_limit_wait_count": self.wait_count,
            "rate_limit_wait_seconds": round(self.total_wait_seconds, 3),
        }


class ReliabilityController:
    def __init__(
        self,
        *,
        requests_per_minute: int = 90,
        max_retries: int = 2,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 15.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self.backoff_max_seconds = max(
            self.backoff_base_seconds, float(backoff_max_seconds)
        )
        self.failure_threshold = max(1, int(circuit_failure_threshold))
        self.recovery_seconds = max(1.0, float(circuit_recovery_seconds))
        self._clock = clock
        self._sleep = sleep
        self._limiter = SlidingWindowRateLimiter(
            requests_per_minute, clock=clock, sleep=sleep
        )
        self._lock = asyncio.Lock()
        self._state = "closed"
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_probe = False
        self._stats = {
            "attempts": 0,
            "retries": 0,
            "circuit_rejections": 0,
            "last_failure": "",
        }

    async def execute(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        retryable: Callable[[Exception], bool],
    ) -> T:
        await self._before_operation()
        try:
            for attempt in range(self.max_retries + 1):
                await self._limiter.acquire()
                self._stats["attempts"] += 1
                try:
                    result = await operation()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not retryable(exc):
                        await self._finish_nonretryable()
                        raise
                    if attempt >= self.max_retries:
                        await self._finish_failure(exc)
                        raise
                    delay = min(
                        self.backoff_max_seconds,
                        self.backoff_base_seconds * (2**attempt),
                    )
                    self._stats["retries"] += 1
                    await self._sleep(delay)
                    continue
                await self._finish_success()
                return result
            raise AssertionError("retry loop terminated unexpectedly")
        except asyncio.CancelledError:
            await self._release_probe()
            raise

    async def _before_operation(self) -> None:
        async with self._lock:
            if self._state == "open":
                elapsed = self._clock() - self._opened_at
                if elapsed < self.recovery_seconds:
                    self._stats["circuit_rejections"] += 1
                    raise CircuitOpenError(
                        f"MiMo circuit is open; retry after {self.recovery_seconds - elapsed:.1f}s."
                    )
                self._state = "half_open"
            if self._state == "half_open":
                if self._half_open_probe:
                    self._stats["circuit_rejections"] += 1
                    raise CircuitOpenError("MiMo circuit is half-open and a probe is running.")
                self._half_open_probe = True

    async def _finish_success(self) -> None:
        async with self._lock:
            self._state = "closed"
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._half_open_probe = False

    async def _finish_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            self._stats["last_failure"] = f"{type(exc).__name__}: {exc}"
            if (
                self._state == "half_open"
                or self._consecutive_failures >= self.failure_threshold
            ):
                self._state = "open"
                self._opened_at = self._clock()
            self._half_open_probe = False

    async def _finish_nonretryable(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._state = "closed"
                self._consecutive_failures = 0
                self._opened_at = 0.0
            self._half_open_probe = False

    async def _release_probe(self) -> None:
        async with self._lock:
            self._half_open_probe = False

    def snapshot(self) -> dict[str, object]:
        retry_after = 0.0
        if self._state == "open":
            retry_after = max(0.0, self.recovery_seconds - (self._clock() - self._opened_at))
        return {
            "circuit_state": self._state,
            "circuit_failures": self._consecutive_failures,
            "circuit_retry_after_seconds": round(retry_after, 3),
            **self._stats,
            **self._limiter.snapshot(),
        }
