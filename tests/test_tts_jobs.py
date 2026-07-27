import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.tts_jobs import TTSJob, TTSJobManager


class TTSJobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_job_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "jobs.json"
            block = asyncio.Event()

            async def blocked(job):
                await block.wait()
                return Path(temp_dir) / f"{job.id}.wav"

            async def noop(*_args):
                return None

            first = TTSJobManager(
                processor=blocked,
                deliverer=noop,
                failure_handler=noop,
                persistence_path=store,
            )
            job = TTSJob(session="session", text="recover me")
            await first.submit(job)
            await asyncio.sleep(0)
            await first.stop(drain_timeout=0)

            processed = []

            async def process(recovered):
                processed.append(recovered.id)
                output = Path(temp_dir) / f"{recovered.id}.wav"
                output.write_bytes(b"audio")
                return output

            second = TTSJobManager(
                processor=process,
                deliverer=noop,
                failure_handler=noop,
                persistence_path=store,
            )
            second.start()
            await asyncio.wait_for(second._idle.wait(), 1)

            self.assertEqual(processed, [job.id])
            self.assertEqual(second.snapshot()["recovered_jobs"], 1)
            task = next(item for item in second.list_tasks() if item["id"] == job.id)
            self.assertEqual(task["status"], "completed")
            self.assertTrue(task["recovered"])
            await second.stop()

    async def test_recovery_reuses_existing_output_for_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "jobs.json"
            output = Path(temp_dir) / "ready.wav"
            output.write_bytes(b"ready")
            job = TTSJob(session="session", text="already synthesized")
            store.write_text(json.dumps({
                "version": 1,
                "stats": {},
                "jobs": [{
                    "job": {
                        "session": job.session,
                        "text": job.text,
                        "source": job.source,
                        "id": job.id,
                        "created_at": job.created_at,
                    },
                    "priority": 10,
                    "sequence": 1,
                    "status": "delivering",
                    "output_path": str(output),
                }],
            }), encoding="utf-8")
            processed = []
            delivered = []

            async def process(_job):
                processed.append(True)
                return output

            async def deliver(recovered, path):
                delivered.append((recovered.id, path))

            async def noop(*_args):
                return None

            manager = TTSJobManager(
                processor=process,
                deliverer=deliver,
                failure_handler=noop,
                persistence_path=store,
            )
            manager.start()
            await asyncio.wait_for(manager._idle.wait(), 1)

            self.assertEqual(processed, [])
            self.assertEqual(delivered, [(job.id, output)])
            await manager.stop()

    async def test_restart_retries_failed_delivery_without_resynthesis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "jobs.json"
            output = Path(temp_dir) / "ready.wav"
            process_calls = []

            async def process(job):
                process_calls.append(job.id)
                output.write_bytes(b"ready")
                return output

            async def fail_delivery(*_args):
                raise RuntimeError("platform offline")

            async def noop(*_args):
                return None

            job = TTSJob(session="session", text="deliver after restart")
            first = TTSJobManager(
                processor=process,
                deliverer=fail_delivery,
                failure_handler=noop,
                persistence_path=store,
            )
            await first.submit(job)
            await asyncio.wait_for(first._idle.wait(), 1)
            self.assertEqual(first.list_tasks()[0]["status"], "failed")
            self.assertEqual(first.protected_output_paths(), {output.resolve()})
            await first.stop()

            delivered = []

            async def deliver(recovered, path):
                delivered.append((recovered.id, path))

            second = TTSJobManager(
                processor=process,
                deliverer=deliver,
                failure_handler=noop,
                persistence_path=store,
            )
            second.start()
            await asyncio.wait_for(second._idle.wait(), 1)

            self.assertEqual(process_calls, [job.id])
            self.assertEqual(delivered, [(job.id, output)])
            self.assertTrue(second.list_tasks()[0]["recovered"])
            await second.stop()

    async def test_running_job_can_be_cancelled_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "jobs.json"
            started = asyncio.Event()

            async def process(_job):
                started.set()
                await asyncio.Event().wait()
                return Path("never.wav")

            async def noop(*_args):
                return None

            manager = TTSJobManager(
                processor=process,
                deliverer=noop,
                failure_handler=noop,
                persistence_path=store,
            )
            job = TTSJob(session="session", text="cancel me")
            await manager.submit(job)
            await started.wait()
            self.assertTrue(await manager.cancel(job.id))
            await asyncio.wait_for(manager._idle.wait(), 1)

            task = next(item for item in manager.list_tasks() if item["id"] == job.id)
            self.assertEqual(task["status"], "cancelled")
            persisted = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(persisted["jobs"][0]["status"], "cancelled")
            self.assertEqual(manager.snapshot()["cancelled_jobs"], 1)
            await manager.stop()
    async def test_command_priority_preserves_order_within_each_session(self):
        started = []
        gate = asyncio.Event()

        async def process(job):
            started.append(job.text)
            if job.text == "block":
                await gate.wait()
            return Path(job.text + ".wav")

        async def deliver(_job, _path):
            return None

        async def fail(_job, _exc):
            return None

        manager = TTSJobManager(
            processor=process, deliverer=deliver, failure_handler=fail, worker_count=1
        )
        await manager.submit(TTSJob(session="busy", text="block", source="auto"))
        await asyncio.sleep(0)
        await manager.submit(TTSJob(session="same", text="same-auto", source="auto"))
        await manager.submit(TTSJob(session="same", text="same-command", source="command"))
        await manager.submit(TTSJob(session="other", text="other-command", source="command"))
        gate.set()
        await asyncio.wait_for(manager._idle.wait(), 1)

        self.assertEqual(
            started, ["block", "other-command", "same-auto", "same-command"]
        )
        await manager.stop()

    async def test_queue_full_drops_new_job(self):
        gate = asyncio.Event()

        async def process(_job):
            await gate.wait()
            return Path("x.wav")

        async def noop(*_args):
            return None

        manager = TTSJobManager(
            processor=process, deliverer=noop, failure_handler=noop, max_queue_size=1
        )
        self.assertTrue(await manager.submit(TTSJob(session="a", text="one")))
        await asyncio.sleep(0)
        self.assertTrue(await manager.submit(TTSJob(session="b", text="two")))
        self.assertFalse(await manager.submit(TTSJob(session="c", text="three")))
        gate.set()
        await asyncio.wait_for(manager._idle.wait(), 1)
        self.assertEqual(manager.snapshot()["dropped_jobs"], 1)
        await manager.stop()

    async def test_multiple_workers_never_reorder_one_session(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        delivered = []

        async def process(job):
            if job.text == "first":
                first_started.set()
                await release_first.wait()
            return Path(job.text + ".wav")

        async def deliver(job, _path):
            delivered.append(job.text)

        async def fail(*_args):
            return None

        manager = TTSJobManager(
            processor=process, deliverer=deliver, failure_handler=fail, worker_count=3
        )
        await manager.submit(TTSJob(session="same", text="first"))
        await first_started.wait()
        await manager.submit(TTSJob(session="same", text="second", source="command"))
        await manager.submit(TTSJob(session="same", text="third"))
        await asyncio.sleep(0.02)
        self.assertEqual(delivered, [])
        release_first.set()
        await asyncio.wait_for(manager._idle.wait(), 1)

        self.assertEqual(delivered, ["first", "second", "third"])
        await manager.stop()
