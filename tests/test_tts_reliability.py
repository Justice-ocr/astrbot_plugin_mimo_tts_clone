import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.tts_reliability import (
    CircuitOpenError,
    ReliabilityController,
)


class _FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_retryable_errors_with_exponential_backoff(self):
        fake = _FakeTime()
        calls = 0
        controller = ReliabilityController(
            max_retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=10,
            clock=fake.clock,
            sleep=fake.sleep,
        )

        async def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise TimeoutError("slow")
            return "ok"

        result = await controller.execute(operation, retryable=lambda exc: isinstance(exc, TimeoutError))

        self.assertEqual(result, "ok")
        self.assertEqual(calls, 3)
        self.assertEqual(fake.sleeps, [1, 2])
        self.assertEqual(controller.snapshot()["retries"], 2)

    async def test_nonretryable_error_fails_immediately(self):
        fake = _FakeTime()
        controller = ReliabilityController(clock=fake.clock, sleep=fake.sleep)

        async def operation():
            raise ValueError("bad request")

        with self.assertRaises(ValueError):
            await controller.execute(operation, retryable=lambda _exc: False)
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(controller.snapshot()["circuit_failures"], 0)

    async def test_circuit_opens_then_half_open_probe_recovers(self):
        fake = _FakeTime()
        controller = ReliabilityController(
            max_retries=0,
            circuit_failure_threshold=2,
            circuit_recovery_seconds=10,
            clock=fake.clock,
            sleep=fake.sleep,
        )

        async def fail():
            raise TimeoutError("down")

        for _ in range(2):
            with self.assertRaises(TimeoutError):
                await controller.execute(fail, retryable=lambda _exc: True)
        self.assertEqual(controller.snapshot()["circuit_state"], "open")
        with self.assertRaises(CircuitOpenError):
            await controller.execute(fail, retryable=lambda _exc: True)

        fake.now += 10
        result = await controller.execute(
            lambda: asyncio.sleep(0, result="healthy"),
            retryable=lambda _exc: True,
        )
        self.assertEqual(result, "healthy")
        self.assertEqual(controller.snapshot()["circuit_state"], "closed")

    async def test_nonretryable_half_open_probe_closes_transient_circuit(self):
        fake = _FakeTime()
        controller = ReliabilityController(
            max_retries=0,
            circuit_failure_threshold=1,
            circuit_recovery_seconds=5,
            clock=fake.clock,
            sleep=fake.sleep,
        )

        async def transient():
            raise TimeoutError("temporary")

        with self.assertRaises(TimeoutError):
            await controller.execute(
                transient, retryable=lambda exc: isinstance(exc, TimeoutError)
            )
        fake.now = 5

        async def invalid_request():
            raise ValueError("bad request")

        with self.assertRaises(ValueError):
            await controller.execute(invalid_request, retryable=lambda _exc: False)
        self.assertEqual(controller.snapshot()["circuit_state"], "closed")

    async def test_rate_limit_wait_is_observable(self):
        fake = _FakeTime()
        controller = ReliabilityController(
            requests_per_minute=1,
            max_retries=0,
            clock=fake.clock,
            sleep=fake.sleep,
        )
        operation = lambda: asyncio.sleep(0, result=True)

        await controller.execute(operation, retryable=lambda _exc: False)
        await controller.execute(operation, retryable=lambda _exc: False)

        snapshot = controller.snapshot()
        self.assertEqual(fake.sleeps, [60.0])
        self.assertEqual(snapshot["rate_limit_wait_count"], 1)
