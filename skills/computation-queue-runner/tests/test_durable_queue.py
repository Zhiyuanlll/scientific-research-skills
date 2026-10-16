"""Portable behavioral tests; no production calculation or systemd modification.

Run: python3 -B -m unittest discover -s tests -v
Actual manager/cgroup survival needs a separately authorized systemd smoke run.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "durable_queue.py"
SPEC = importlib.util.spec_from_file_location("durable_queue", SCRIPT)
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)
PYTHON = str(Path(sys.executable).resolve())


class QueueBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="durable-queue-test-")
        self.root = Path(self.temp.name)
        self.run = self.root / "run"

    def tearDown(self):
        self.temp.cleanup()

    def task(self, task_id="one", code="print('done')", validate=None):
        task = {"id": task_id, "argv": [PYTHON, "-c", code], "cwd": str(self.root)}
        if validate is not None:
            task["validation_argv"] = [PYTHON, "-c", validate]
        return task

    def prepare(self, tasks=None, inputs=None):
        self.plan = queue.validate_plan({"schema_version": 1, "name": "test", "tasks": tasks or [self.task()], "inputs": inputs or []})
        return queue.prepare(self.plan, self.run, {"linger": False, "test_only": True})

    def worker(self):
        return subprocess.run([PYTHON, "-B", str(self.run / "worker.py"), "_worker", str(self.run)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)

    def state(self):
        return queue.read_json(self.run / "state.json")

    def wait_for(self, predicate, seconds=8):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for test process")

    def test_validates_paths_and_rejects_parallel_workers(self):
        plan = {"schema_version": 1, "name": "bad", "tasks": [self.task()]}
        plan["tasks"][0]["argv"][0] = "python"
        with self.assertRaises(queue.QueueError):
            queue.validate_plan(plan)
        plan["resources"] = {"workers": 1}
        plan["tasks"][0]["id"] = 5
        with self.assertRaises(queue.QueueError):
            queue.validate_plan(plan)
        plan["tasks"][0]["argv"][0] = PYTHON
        plan["resources"] = {"workers": 2}
        with self.assertRaises(queue.QueueError):
            queue.validate_plan(plan)

    def test_exit_zero_is_pending_not_scientific_success(self):
        self.prepare()
        self.assertEqual(self.worker().returncode, 0)
        self.assertEqual(self.state()["state"], "process_finished_validation_pending")
        self.assertEqual(self.state()["tasks"]["one"]["exit_code"], 0)

    def test_scientific_validator_required_for_validated(self):
        self.prepare([self.task(validate="assert 2+2 == 4")])
        self.assertEqual(self.worker().returncode, 0)
        self.assertEqual(self.state()["state"], "validated")

    def test_failed_validator_stops_next_task(self):
        self.prepare([self.task(validate="raise SystemExit(3)"), self.task("two", "open('forbidden','w').write('bad')")])
        self.assertEqual(self.worker().returncode, 1)
        self.assertEqual(self.state()["state"], "validation_failed")
        self.assertFalse((self.root / "forbidden").exists())

    def test_failed_compute_stops_next_task(self):
        self.prepare([self.task(code="raise SystemExit(7)"), self.task("two", "open('forbidden','w').write('bad')")])
        self.assertEqual(self.worker().returncode, 1)
        self.assertEqual(self.state()["tasks"]["one"]["exit_code"], 7)
        self.assertNotIn("two", self.state()["tasks"])
        self.assertFalse((self.root / "forbidden").exists())

    def test_input_snapshot_runs_saved_code_after_original_edit(self):
        source = self.root / "science.py"
        source.write_text("open('result','w').write('original')", encoding="utf-8")
        task = {"id": "one", "argv": [PYTHON, str(source)], "cwd": str(self.root)}
        self.prepare([task], [{"path": str(source), "snapshot": True}])
        source.write_text("raise SystemExit('changed')", encoding="utf-8")
        self.assertEqual(self.worker().returncode, 0)
        self.assertEqual((self.root / "result").read_text(), "original")

    def test_unsnapshotted_input_edit_refuses_execution(self):
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.prepare(inputs=[{"path": str(config), "snapshot": False}])
        config.write_text('{"change":true}', encoding="utf-8")
        self.assertEqual(self.worker().returncode, 1)
        self.assertIn("Input changed", self.state()["error"])

    def test_duplicate_run_directory_refused_without_overwrite(self):
        self.prepare()
        marker = self.run / "marker"
        marker.write_text("keep")
        with self.assertRaises(queue.QueueError):
            queue.prepare(self.plan, self.run, {})
        self.assertEqual(marker.read_text(), "keep")

    def test_snapshot_paths_cannot_escape_through_parent_traversal(self):
        source = self.root / "input"
        source.write_text("data")
        source_with_traversal = str(self.root) + "/../" + self.root.name + "/input"
        with self.assertRaises(queue.QueueError):
            self.prepare(inputs=[{"path": source_with_traversal, "snapshot": True}])

    def test_short_task_finishing_before_service_query_is_accepted(self):
        metadata = self.prepare()
        self.assertEqual(self.worker().returncode, 0)
        # systemd can already have collected a very short successful service.
        with mock.patch.object(queue, "run_command", return_value=""), \
                mock.patch.object(queue, "manager_state", return_value={"ActiveState": "inactive", "MainPID": "0", "Result": "success"}):
            self.assertEqual(queue.launch(self.run, metadata, self.plan["resources"])["Result"], "success")

    def test_live_launch_requires_cgroup_ownership_and_writes_receipt(self):
        metadata = self.prepare()
        service = {"ActiveState": "active", "MainPID": "999", "ControlGroup": "/test/unit.service"}
        with mock.patch.object(queue, "run_command", return_value=""), \
                mock.patch.object(queue, "manager_state", return_value=service), \
                mock.patch.object(queue, "cgroup_matches", return_value=False):
            with self.assertRaises(queue.QueueError):
                queue.launch(self.run, metadata, self.plan["resources"])
        with mock.patch.object(queue, "run_command", return_value=""), \
                mock.patch.object(queue, "manager_state", return_value=service), \
                mock.patch.object(queue, "cgroup_matches", return_value=True):
            result = queue.launch(self.run, metadata, self.plan["resources"])
        self.assertTrue(result["OwnershipEvidence"]["proc_cgroup_matches"])
        receipt = json.loads((self.run / "submission-receipts.jsonl").read_text())
        self.assertEqual(receipt["unit"], metadata["unit"])
        self.assertEqual(receipt["attempt"], 1)

    def test_controller_lease_blocks_duplicate_worker(self):
        self.prepare()
        with (self.run / "queue.lock").open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            duplicate = self.worker()
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.state()["state"], "queued")

    def test_argv_dollar_and_shell_text_are_literal_and_environment_filtered(self):
        marker = self.root / "must-not-exist"
        code = "import os,sys,json;open('observed','w').write(json.dumps({'args':sys.argv[1:],'secret':os.environ.get('PRIVATE_TEST_SECRET'),'threads':os.environ.get('OMP_NUM_THREADS')}))"
        task = self.task(code=code)
        task["argv"] += ["$HOME", "$(touch " + str(marker) + ")", "; echo unsafe"]
        self.prepare([task])
        with mock.patch.dict(os.environ, {"PRIVATE_TEST_SECRET": "do-not-copy"}):
            self.assertEqual(self.worker().returncode, 0)
        observed = json.loads((self.root / "observed").read_text())
        self.assertEqual(observed["args"], task["argv"][3:])
        self.assertIsNone(observed["secret"])
        self.assertEqual(observed["threads"], "1")
        self.assertFalse(marker.exists())

    def test_graceful_stop_terminates_child_and_prevents_next_task(self):
        self.prepare([self.task(code="import os,time;open('child.pid','w').write(str(os.getpid()));time.sleep(30)"), self.task("two")])
        process = subprocess.Popen([PYTHON, "-B", str(self.run / "worker.py"), "_worker", str(self.run)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_for(lambda: (self.root / "child.pid").exists())
            pid = int((self.root / "child.pid").read_text())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=15)
            self.assertEqual(self.state()["state"], "cancelled")
            self.assertNotIn("two", self.state()["tasks"])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_status_reconciles_dead_controller_and_does_not_invent_progress(self):
        self.prepare()
        state = self.state()
        state.update(state="running", current_task="one", tasks={"one": {"state": "running"}})
        queue.write_json(self.run / "state.json", state)
        with mock.patch.object(queue, "manager_state", return_value={"ActiveState": "inactive", "MainPID": "0"}):
            result = queue.status(self.run)
        self.assertEqual(result["state"]["state"], "interrupted")
        self.assertEqual(result["state"]["tasks"]["one"]["state"], "interrupted")
        self.assertIn("not scientific progress", result["heartbeat_meaning"])

    def test_status_manager_unreachable_does_not_claim_interruption(self):
        self.prepare()
        with mock.patch.object(queue, "manager_state", side_effect=queue.QueueError("manager unreachable")):
            result = queue.status(self.run)
        self.assertEqual(result["manager_health"], "unknown")
        self.assertEqual(self.state()["state"], "queued")

    def test_deactivating_without_mainpid_cannot_resume(self):
        self.prepare()
        service = {"ActiveState": "deactivating", "MainPID": "0"}
        with mock.patch.object(queue, "manager_state", return_value=service):
            result = queue.status(self.run)
        self.assertEqual(result["manager_health"], "transitioning")
        self.assertEqual(self.state()["state"], "queued")
        with self.assertRaises(queue.QueueError):
            queue.require_stopped(service)

    def checkpoint_request(self):
        checkpoint = self.root / "checkpoint.bin"
        checkpoint.write_bytes(b"test-checkpoint")
        metadata = queue.read_json(self.run / "metadata.json")
        return {"schema_version": 1, "task_id": "two", "plan_sha256": metadata["plan_sha256"],
                "checkpoint": {"path": str(checkpoint), "sha256": queue.digest(checkpoint)},
                "input_sha256": {}, "compatibility_statement": "Synthetic counter checkpoint version1 matches the stored plan.",
                "checkpoint_validator_argv": [PYTHON, "-c", "assert 1 == 1"],
                "resume_argv": [PYTHON, "-c", "open('resumed','w').write('yes')"]}

    def test_resume_requires_compatible_checkpoint_and_skips_finished_tasks(self):
        first = self.task(code="open('first-count','a').write('1')")
        self.prepare([first, self.task("two", "raise SystemExit(8)")])
        self.assertEqual(self.worker().returncode, 1)
        request = self.checkpoint_request()
        bad = dict(request, plan_sha256="wrong")
        with self.assertRaises(queue.QueueError):
            queue.resume_request(self.run, bad)
        queue.resume_request(self.run, request)
        request["executable_sha256"] = {PYTHON: queue.digest(PYTHON)}
        meta = queue.read_json(self.run / "metadata.json")
        meta["resume_request_sha256"] = hashlib.sha256(queue.canonical(request)).hexdigest()
        queue.write_json(self.run / "metadata.json", meta)
        queue.write_json(self.run / "resume-request.json", request)
        self.assertEqual(self.worker().returncode, 0)
        self.assertEqual((self.root / "first-count").read_text(), "1")
        self.assertEqual((self.root / "resumed").read_text(), "yes")
        self.assertTrue((self.run / "logs/two.attempt-001.compute.stderr.log").exists())
        self.assertTrue((self.run / "logs/two.attempt-002.compute.stdout.log").exists())
        self.assertTrue((self.run / "resume-request.attempt-002.json").exists())
        old_state = queue.read_json(self.run / "attempts/attempt-001.state.json")
        self.assertEqual(old_state["tasks"]["two"]["exit_code"], 8)
        self.assertEqual(old_state["state"], "failed")
        old_metadata = queue.read_json(self.run / "attempts/attempt-001.metadata.json")
        self.assertIn("unit", old_metadata)
        self.assertTrue((self.run / "attempts/attempt-002.state.json").exists())


if __name__ == "__main__":
    unittest.main()
