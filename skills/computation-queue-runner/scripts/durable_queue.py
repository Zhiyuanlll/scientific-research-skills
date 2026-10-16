#!/usr/bin/env python3
"""Small serial research queue supervised by a systemd user service (Linux).

No shell evaluation, automatic retries, or detached subprocess fallback. Runtime
records belong in an explicitly selected run directory, never in this skill.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

sys.dont_write_bytecode = True
TERMINAL_OK = {"validated", "process_finished_validation_pending"}
RESUMABLE = {"failed", "validation_failed", "interrupted", "cancelled"}
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


class QueueError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise QueueError("Cannot read JSON %s: %s" % (path, error)) from error


def write_json(path, obj):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(obj, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def absolute_file(value, label):
    if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
        raise QueueError("%s must be an existing absolute file path" % label)
    if ".." in Path(value).parts:
        raise QueueError("%s must not contain parent-directory traversal" % label)
    return value


def argv_check(argv, label):
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or "\0" in x for x in argv):
        raise QueueError("%s must be a nonempty JSON array of strings" % label)
    absolute_file(argv[0], label + "[0]")
    if not os.access(argv[0], os.X_OK):
        raise QueueError("%s executable has no execute permission" % label)


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise QueueError("Plan schema_version must be 1")
    if not isinstance(plan.get("name"), str) or not plan["name"].strip():
        raise QueueError("Plan name must be nonempty")
    resources = plan.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise QueueError("resources must be an object")
    threads = resources.setdefault("threads", 1)
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise QueueError("resources.threads must be a positive integer")
    if resources.get("workers", 1) != 1:
        raise QueueError("This serial runner supports resources.workers=1 only")
    resources["workers"] = 1
    for key in ("memory_max", "cpu_quota"):
        if key in resources and (not isinstance(resources[key], str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[KMGTPE]|%)?", resources[key])):
            raise QueueError("resources.%s must be a systemd numeric limit such as 8G or 100%%" % key)
    inputs = plan.setdefault("inputs", [])
    if not isinstance(inputs, list):
        raise QueueError("inputs must be a list of explicitly approved files")
    seen_paths = set()
    for entry in inputs:
        if not isinstance(entry, dict):
            raise QueueError("Each input must be {path, snapshot}")
        absolute_file(entry.get("path"), "input.path")
        if not isinstance(entry.get("snapshot"), bool):
            raise QueueError("Each input requires an explicit snapshot true/false")
        if entry["path"] in seen_paths:
            raise QueueError("Duplicate input path")
        seen_paths.add(entry["path"])
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise QueueError("tasks must be a nonempty list")
    seen_ids = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task["id"]):
            raise QueueError("Task id must use 1-80 letters, digits, underscores or hyphens")
        if task["id"] in seen_ids:
            raise QueueError("Task ids must be unique")
        seen_ids.add(task["id"])
        cwd = task.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise QueueError("Task cwd must be an existing absolute directory")
        argv_check(task.get("argv"), task["id"] + ".argv")
        if "validation_argv" in task:
            argv_check(task["validation_argv"], task["id"] + ".validation_argv")
    return plan


def run_command(argv):
    try:
        result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QueueError("Command unavailable: %s: %s" % (argv[0], error)) from error
    if result.returncode:
        raise QueueError("Command failed: %s: %s" % (argv[0], result.stderr.strip()[:600]))
    return result.stdout.strip()


def manager_preflight(require_linger=False):
    if sys.platform != "linux":
        raise QueueError("This backend requires Linux and a systemd user manager")
    version = run_command(["systemd-run", "--version"]).splitlines()[0]
    match = re.search(r"systemd\s+(\d+)", version)
    if not match or int(match.group(1)) < 254:
        raise QueueError("systemd >=254 required for --expand-environment=no")
    # This queries the manager without dumping its environment (which can contain secrets).
    run_command(["systemctl", "--user", "show", "--property=Version", "--value"])
    try:
        linger = run_command(["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"]) == "yes"
    except QueueError:
        linger = None
    if require_linger and linger is not True:
        raise QueueError("Linger=yes was required but could not be verified; no job submitted")
    return {"systemd_version": version, "linger": linger,
            "survival_scope": "user-manager-lifetime; machine sleep pauses CPU; reboot does not resume transient units"}


def manager_state(unit):
    properties = ("ActiveState", "SubState", "MainPID", "ControlGroup", "Result", "ExecMainStatus")
    result = subprocess.run(["systemctl", "--user", "show", unit, "--no-pager"] +
                            ["--property=" + key for key in properties],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    if result.returncode:
        if "not found" in result.stderr.lower() or "not loaded" in result.stderr.lower():
            return {"ActiveState": "not-found", "MainPID": "0"}
        raise QueueError("Cannot query systemd unit: " + result.stderr.strip()[:600])
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def prepare(plan, run_dir, manager):
    run_dir = Path(run_dir)
    if not run_dir.is_absolute():
        raise QueueError("--run-dir must be absolute")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise QueueError("Run directory already exists; refusing duplicate submission") from error
    os.chmod(run_dir, 0o700)
    (run_dir / "logs").mkdir()
    (run_dir / "snapshots").mkdir()
    runtime = []
    for entry in plan["inputs"]:
        source = Path(entry["path"])
        saved = None
        if entry["snapshot"]:
            saved = run_dir / "snapshots" / source.relative_to(source.anchor)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, saved)
        runtime.append({"path": str(source), "sha256": digest(source),
                        "snapshot": str(saved) if saved else None})
        if saved and digest(saved) != runtime[-1]["sha256"]:
            raise QueueError("Input changed while taking snapshot")
    executable_paths = {str(Path(sys.executable).resolve())}
    for task in plan["tasks"]:
        executable_paths.add(task["argv"][0])
        if "validation_argv" in task:
            executable_paths.add(task["validation_argv"][0])
    plan_digest = hashlib.sha256(canonical(plan)).hexdigest()
    worker = run_dir / "worker.py"
    shutil.copy2(Path(__file__).resolve(), worker)
    metadata = {"schema_version": 1, "created_at": now(), "unit": "research-queue-" + uuid.uuid4().hex + ".service",
                "submitted_attempt": 1,
                "plan_sha256": plan_digest, "inputs": runtime,
                "executable_sha256": {p: digest(p) for p in sorted(executable_paths)},
                "worker_sha256": digest(worker), "python": str(Path(sys.executable).resolve()), "manager": manager}
    write_json(run_dir / "plan.json", plan)
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "state.json", {"state": "queued", "created_at": now(), "tasks": {}, "attempt": 0})
    return metadata


def cgroup_matches(pid, expected):
    if not expected or not expected.startswith("/"):
        return False
    text = Path("/proc/%d/cgroup" % pid).read_text(encoding="utf-8")
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and (parts[2] == expected or parts[2].startswith(expected.rstrip("/") + "/")):
            return True
    return False


def require_stopped(service):
    if service.get("ActiveState") in ("active", "activating", "deactivating", "reloading"):
        raise QueueError("Previous service is still active or cleaning up; wait before checkpoint resume")
    if int(service.get("MainPID", "0") or "0") > 0:
        raise QueueError("Previous service still has a MainPID")
    group = service.get("ControlGroup", "")
    if group:
        if not group.startswith("/") or ".." in Path(group).parts:
            raise QueueError("Unexpected cgroup path")
        members = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.procs"
        try:
            if members.read_text(encoding="utf-8").strip():
                raise QueueError("Previous service cgroup still has processes")
        except FileNotFoundError:
            pass
        except OSError as error:
            raise QueueError("Cannot verify previous cgroup cleanup: " + str(error)) from error


def launch(run_dir, metadata, resources):
    run_dir = Path(run_dir)
    argv = ["systemd-run", "--user", "--unit=" + metadata["unit"], "--expand-environment=no",
            "--property=Type=exec", "--property=KillMode=control-group", "--property=Restart=no",
            "--property=TimeoutStopSec=30s", "--property=WorkingDirectory=" + str(run_dir),
            "--property=StandardOutput=append:" + str(run_dir / "controller.log"),
            "--property=StandardError=append:" + str(run_dir / "controller.log")]
    for key, prop in (("memory_max", "MemoryMax"), ("cpu_quota", "CPUQuota")):
        if key in resources:
            argv.append("--property=" + prop + "=" + resources[key])
    argv += ["--", metadata["python"], "-I", "-B", str(run_dir / "worker.py"), "_worker", str(run_dir)]
    run_command(argv)
    service = manager_state(metadata["unit"])
    state = read_json(run_dir / "state.json")
    active = service.get("ActiveState") in ("active", "activating")
    valid_pid = int(service.get("MainPID", "0") or "0") > 0
    if not (active and valid_pid and service.get("ControlGroup")) and state["state"] not in TERMINAL_OK | RESUMABLE:
        raise QueueError("Service launch could not be verified; inspect status; do not resubmit blindly")
    if active and state.get("state") == "running" and state.get("worker_pid") and int(service["MainPID"]) != state["worker_pid"]:
        raise QueueError("Service MainPID does not match the recorded queue controller")
    evidence = {"method": "terminal_state_and_scheduler", "terminal_state": state["state"]}
    if active and valid_pid and service.get("ControlGroup"):
        try:
            matches = cgroup_matches(int(service["MainPID"]), service["ControlGroup"])
        except OSError:
            matches = False
        if matches:
            evidence = {"method": "live_mainpid_cgroup", "main_pid": int(service["MainPID"]),
                        "control_group": service["ControlGroup"], "proc_cgroup_matches": True}
        else:
            # Short tasks may finish between systemctl show and /proc inspection.
            state = read_json(run_dir / "state.json")
            if state["state"] not in TERMINAL_OK | RESUMABLE:
                raise QueueError("Live MainPID cgroup ownership could not be verified")
            evidence["terminal_state"] = state["state"]
    service["OwnershipEvidence"] = evidence
    with (run_dir / "submission-receipts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": now(), "unit": metadata["unit"], "attempt": metadata["submitted_attempt"],
                                 "plan_sha256": metadata["plan_sha256"], "worker_sha256": metadata["worker_sha256"],
                                 "ownership": evidence}, ensure_ascii=False) + "\n")
    return service


def check_integrity(run_dir, metadata, plan):
    if hashlib.sha256(canonical(plan)).hexdigest() != metadata["plan_sha256"]:
        raise QueueError("Saved plan digest mismatch")
    if digest(Path(run_dir) / "worker.py") != metadata["worker_sha256"]:
        raise QueueError("Saved worker code changed")
    for entry in metadata["inputs"]:
        path = entry["snapshot"] or entry["path"]
        if digest(path) != entry["sha256"]:
            raise QueueError("Input changed: " + path)
    for path, expected in metadata["executable_sha256"].items():
        if digest(path) != expected:
            raise QueueError("Executable changed: " + path)


def mapped_argv(argv, metadata):
    replacements = {e["path"]: e["snapshot"] for e in metadata["inputs"] if e["snapshot"]}
    result = []
    for arg in argv:
        if arg in replacements:
            result.append(replacements[arg])
        elif arg.startswith("--") and "=" in arg and arg.split("=", 1)[1] in replacements:
            key, value = arg.split("=", 1)
            result.append(key + "=" + replacements[value])
        else:
            result.append(arg)
    return result


def resume_request(run_dir, manifest):
    metadata = read_json(Path(run_dir) / "metadata.json")
    state = read_json(Path(run_dir) / "state.json")
    if state["state"] not in RESUMABLE:
        raise QueueError("Only failed/interrupted/cancelled queues may resume")
    if manifest.get("schema_version") != 1 or manifest.get("plan_sha256") != metadata["plan_sha256"]:
        raise QueueError("Checkpoint plan digest is incompatible")
    task_id = manifest.get("task_id")
    if task_id not in state["tasks"] or state["tasks"][task_id]["state"] not in RESUMABLE:
        raise QueueError("Checkpoint task must be the interrupted or failed task")
    if manifest.get("input_sha256") != {e["path"]: e["sha256"] for e in metadata["inputs"]}:
        raise QueueError("Checkpoint code/config compatibility hashes differ")
    if not isinstance(manifest.get("compatibility_statement"), str) or not manifest["compatibility_statement"].strip():
        raise QueueError("Scientific checkpoint compatibility_statement is required")
    checkpoint = manifest.get("checkpoint", {})
    absolute_file(checkpoint.get("path"), "checkpoint.path")
    if digest(checkpoint["path"]) != checkpoint.get("sha256"):
        raise QueueError("Checkpoint content hash differs")
    argv_check(manifest.get("checkpoint_validator_argv"), "checkpoint_validator_argv")
    argv_check(manifest.get("resume_argv"), "resume_argv")
    return manifest


class Worker:
    def __init__(self, root):
        self.root = Path(root)
        self.plan = read_json(self.root / "plan.json")
        self.meta = read_json(self.root / "metadata.json")
        self.state = read_json(self.root / "state.json")
        self.lock = threading.RLock()
        self.stop_requested = False
        self.finished = threading.Event()
        self.child = None

    def save(self):
        with self.lock:
            write_json(self.root / "state.json", self.state)

    def event(self, kind, **values):
        with self.lock:
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": now(), "event": kind, **values}, ensure_ascii=False) + "\n")

    def heartbeat(self):
        while not self.finished.wait(2):
            with self.lock:
                self.state["heartbeat_at"] = now()
                self.save()

    def on_stop(self, _signum, _frame):
        self.stop_requested = True
        if self.child and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def execute(self, argv, task, kind, attempt):
        prefix = self.root / "logs" / (task["id"] + ".attempt-%03d.%s" % (attempt, kind))
        env = {"PATH": os.defpath, "HOME": str(Path.home()), "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}
        env.update({key: str(self.plan["resources"]["threads"]) for key in THREAD_VARS})
        with prefix.with_suffix(prefix.suffix + ".stdout.log").open("ab") as stdout, \
                prefix.with_suffix(prefix.suffix + ".stderr.log").open("ab") as stderr:
            self.child = subprocess.Popen(argv, cwd=task["cwd"], env=env,
                                          stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                          start_new_session=True)
            with self.lock:
                self.state["child_pid"] = self.child.pid
                self.save()
            stopping_since = None
            while self.child.poll() is None:
                if self.stop_requested:
                    if stopping_since is None:
                        stopping_since = time.monotonic()
                        self.on_stop(None, None)
                    elif time.monotonic() - stopping_since > 10:
                        try:
                            os.killpg(self.child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                time.sleep(0.1)
            code = self.child.wait()
        self.child = None
        with self.lock:
            self.state.pop("child_pid", None)
            self.save()
        return code

    def run(self):
        with (self.root / "queue.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise QueueError("Another queue controller holds this run directory") from error
            signal.signal(signal.SIGTERM, self.on_stop)
            signal.signal(signal.SIGINT, self.on_stop)
            self.state.update(state="running", worker_pid=os.getpid(), heartbeat_at=now(), started_at=now())
            self.state["attempt"] += 1
            attempt = self.state["attempt"]
            (self.root / "attempts").mkdir(exist_ok=True)
            write_json(self.root / "attempts" / ("attempt-%03d.metadata.json" % attempt), self.meta)
            self.save()
            self.event("controller_started", attempt=attempt, unit=self.meta["unit"])
            beat = threading.Thread(target=self.heartbeat, daemon=True)
            beat.start()
            current = None
            request_path = self.root / "resume-request.json"
            try:
                check_integrity(self.root, self.meta, self.plan)
                request = read_json(request_path) if request_path.exists() else None
                if request:
                    if hashlib.sha256(canonical(request)).hexdigest() != self.meta.get("resume_request_sha256"):
                        raise QueueError("Saved resume request changed")
                    for path, expected in request["executable_sha256"].items():
                        if digest(path) != expected:
                            raise QueueError("Resume executable changed: " + path)
                    # Recheck checkpoint bytes immediately before invoking its compatibility checker.
                    if digest(request["checkpoint"]["path"]) != request["checkpoint"]["sha256"]:
                        raise QueueError("Checkpoint changed after resume request")
                for task in self.plan["tasks"]:
                    current = task["id"]
                    previous = self.state["tasks"].get(current, {})
                    if previous.get("state") in TERMINAL_OK:
                        continue
                    if previous and previous.get("state") in RESUMABLE and (not request or request["task_id"] != current):
                        raise QueueError("A failed task requires an explicit compatible checkpoint request")
                    if self.stop_requested:
                        self.state["state"] = "cancelled"
                        break
                    check_integrity(self.root, self.meta, self.plan)
                    record = {"state": "running", "attempt": attempt, "started_at": now()}
                    self.state["tasks"][current] = record
                    self.state["current_task"] = current
                    self.save()
                    self.event("task_started", task_id=current, attempt=attempt)
                    argv = task["argv"]
                    if request and request["task_id"] == current:
                        code = self.execute(mapped_argv(request["checkpoint_validator_argv"], self.meta), task, "checkpoint-check", attempt)
                        if code:
                            raise QueueError("Scientific checkpoint compatibility checker failed with code %s" % code)
                        argv = request["resume_argv"]
                    code = self.execute(mapped_argv(argv, self.meta), task, "compute", attempt)
                    record.update(exit_code=code, process_finished_at=now())
                    if self.stop_requested:
                        record["state"] = "cancelled"
                    elif code:
                        record["state"] = "failed"
                    elif "validation_argv" in task:
                        validation = self.execute(mapped_argv(task["validation_argv"], self.meta), task, "validation", attempt)
                        record["validation_exit_code"] = validation
                        record["state"] = "cancelled" if self.stop_requested else ("validated" if validation == 0 else "validation_failed")
                    else:
                        record["state"] = "process_finished_validation_pending"
                    record["finished_at"] = now()
                    self.save()
                    self.event("task_finished", task_id=current, attempt=attempt, state=record["state"], exit_code=code)
                    if record["state"] not in TERMINAL_OK:
                        self.state["state"] = record["state"]
                        break
                else:
                    self.state["state"] = ("validated" if all(t["state"] == "validated" for t in self.state["tasks"].values())
                                           else "process_finished_validation_pending")
            except Exception as error:
                self.state["state"] = "cancelled" if self.stop_requested else "failed"
                self.state["error"] = str(error)
                if current and self.state["tasks"].get(current, {}).get("state") == "running":
                    self.state["tasks"][current].update(state=self.state["state"], error=str(error), finished_at=now())
                self.event("controller_error", error=str(error))
            finally:
                self.finished.set()
                beat.join(timeout=3)
                self.state["finished_at"] = now()
                self.state.pop("child_pid", None)
                self.save()
                write_json(self.root / "attempts" / ("attempt-%03d.state.json" % attempt), self.state)
                self.event("controller_finished", state=self.state["state"], attempt=attempt)
                if request_path.exists():
                    request_path.rename(self.root / ("resume-request.attempt-%03d.json" % attempt))
            return 0 if self.state["state"] in TERMINAL_OK else 1


def status(root):
    root = Path(root)
    metadata = read_json(root / "metadata.json")
    state = read_json(root / "state.json")
    try:
        service = manager_state(metadata["unit"])
    except (QueueError, OSError, subprocess.TimeoutExpired) as error:
        return {"run_dir": str(root), "state": state, "manager_health": "unknown", "manager_error": str(error),
                "heartbeat_meaning": "controller liveness only; not scientific progress"}
    transitioning = service.get("ActiveState") in ("active", "activating", "deactivating", "reloading")
    active = transitioning and int(service.get("MainPID", "0") or "0") > 0
    if not transitioning and state["state"] in ("queued", "running"):
        # Confirm the controller lock is free before recording an interruption.
        with (root / "queue.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                active = True
            else:
                state.update(state="interrupted", reconciled_at=now(),
                             interruption_reason="service has no live MainPID; prior queue state was nonterminal")
                current = state.get("current_task")
                if current and state["tasks"].get(current, {}).get("state") == "running":
                    state["tasks"][current].update(state="interrupted", finished_at=now())
                write_json(root / "state.json", state)
    return {"run_dir": str(root), "state": state, "manager": service,
            "manager_health": "live" if active else ("transitioning" if transitioning else "inactive"),
            "persistence": metadata["manager"], "heartbeat_meaning": "controller liveness only; not scientific progress"}


def submit(plan_path, run_dir, require_linger=False):
    plan = validate_plan(read_json(plan_path))
    manager = manager_preflight(require_linger)
    metadata = prepare(plan, run_dir, manager)
    try:
        service = launch(run_dir, metadata, plan["resources"])
    except Exception as error:
        state = read_json(Path(run_dir) / "state.json")
        if state["state"] == "queued":
            state.update(state="failed", error="Submission failed: " + str(error), finished_at=now())
            write_json(Path(run_dir) / "state.json", state)
        raise
    return {"run_dir": run_dir, "unit": metadata["unit"], "manager": service, "persistence": manager}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("template", help="write a sample plan (refuses overwrite)")
    p.add_argument("path")
    p = sub.add_parser("validate", help="validate plan paths/argv/resources; does not execute tasks")
    p.add_argument("plan")
    p = sub.add_parser("submit", help="submit an immutable queue copy to systemd --user")
    p.add_argument("plan")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--require-linger", action="store_true")
    for name in ("status", "stop", "_worker"):
        p = sub.add_parser(name, help=argparse.SUPPRESS if name == "_worker" else name + " a run")
        p.add_argument("run_dir")
    p = sub.add_parser("resume", help="explicit checkpoint-gated resume; never automatic retry")
    p.add_argument("run_dir")
    p.add_argument("--checkpoint-manifest", required=True)
    p.add_argument("--require-linger", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            plan = {"schema_version": 1, "name": "research-queue", "resources": {"workers": 1, "threads": 1}, "inputs": [],
                    "tasks": [{"id": "smoke", "argv": [str(Path(sys.executable).resolve()), "-c", "print('replace with your computation')"], "cwd": str(Path.cwd())}]}
            with Path(args.path).open("x", encoding="utf-8") as stream:
                json.dump(plan, stream, indent=2)
                stream.write("\n")
            result = {"template": str(Path(args.path).absolute()), "edit_before_submission": True}
        elif args.command == "validate":
            plan = validate_plan(read_json(args.plan))
            result = {"valid": True, "tasks": len(plan["tasks"]), "plan_sha256": hashlib.sha256(canonical(plan)).hexdigest()}
        elif args.command == "submit":
            result = submit(args.plan, args.run_dir, args.require_linger)
        elif args.command == "_worker":
            return Worker(args.run_dir).run()
        elif args.command == "status":
            result = status(args.run_dir)
        elif args.command == "stop":
            root = Path(args.run_dir)
            metadata = read_json(root / "metadata.json")
            write_json(root / "stop-request.json", {"requested_at": now(), "unit": metadata["unit"]})
            run_command(["systemctl", "--user", "stop", metadata["unit"]])
            result = status(args.run_dir)
        else:
            root = Path(args.run_dir)
            observation = status(root)
            if "manager" not in observation:
                raise QueueError("Cannot verify previous service cleanup while manager is unreachable")
            require_stopped(observation["manager"])
            state = observation["state"]
            if state["state"] not in RESUMABLE:
                raise QueueError("Run is not eligible for checkpoint resume")
            manager = manager_preflight(args.require_linger)
            manifest = resume_request(root, read_json(args.checkpoint_manifest))
            # A separate short admission lock prevents simultaneous resume launches.
            with (root / "admission.lock").open("a") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                require_stopped(manager_state(read_json(root / "metadata.json")["unit"]))
                manifest = resume_request(root, read_json(args.checkpoint_manifest))
                with (root / "queue.lock").open("a") as queue_lease:
                    try:
                        fcntl.flock(queue_lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise QueueError("A controller is still running") from error
                metadata = read_json(root / "metadata.json")
                metadata["unit"] = "research-queue-" + uuid.uuid4().hex + ".service"
                metadata["manager"] = manager
                metadata["submitted_attempt"] = read_json(root / "state.json")["attempt"] + 1
                manifest["executable_sha256"] = {p: digest(p) for p in
                    {manifest["checkpoint_validator_argv"][0], manifest["resume_argv"][0]}}
                metadata["resume_request_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
                write_json(root / "metadata.json", metadata)
                write_json(root / "resume-request.json", manifest)
                result = {"run_dir": str(root), "unit": metadata["unit"],
                          "manager": launch(root, metadata, read_json(root / "plan.json")["resources"])}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (QueueError, OSError, ValueError, KeyError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
