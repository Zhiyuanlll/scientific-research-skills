---
name: computation-queue-runner
description: Submit, inspect, stop and recover formal scientific computations of any duration through an independent service or batch scheduler, so submitted tasks and queued successors continue when Codex is inactive or closed. Use for 开始计算、提交计算、单算例、参数扫描、正式计算队列 and checkpoint recovery; exclude syntax checks, unit tests and other development-only checks.
metadata:
  version: "1.0.0"
  license: Apache-2.0
---

# Computation Queue Runner

For every formal research calculation, short or long, make the operating system or an existing batch scheduler own the calculation and queue. Codex prepares, submits and inspects work; continued chat activity must not be necessary for either the current task or the next queued task to start.

## Choose the execution boundary

Read project instructions and inspect the requested commands, existing processes/jobs, resources and outputs. Preserve the user's authorized workload and concurrency. When the user asks to start/submit a calculation, Codex prepares, checks **and executes submission**, then verifies the independent supervisor. Do not stop at handing over a script or require the user to repeat an existing submission instruction. Requests only to plan or package this skill do not authorize production calculations. Syntax checks, unit tests and lightweight development diagnostics may run directly; classifying a formal scientific case as a “quick test” must not bypass independent submission.

- On a Linux workstation with a reachable user systemd manager, use the included helper described in [local-queue.md](references/local-queue.md). It provides a serial queue, submission receipts, input hashes, logs and explicit recovery. It does not wrap arbitrary existing processes into a service.
- On an HPC cluster, use its existing batch scheduler; read [scheduler-adapters.md](references/scheduler-adapters.md). Do not run a workstation queue on a login node or treat an interactive allocation as a durable batch submission.
- For another platform, use a service/scheduled-task facility whose actual lifetime and ownership can be verified. The included executor supports Linux systemd only. If no suitable supervisor is available, prepare the plan and report that durable submission is unavailable; do not silently downgrade to a chat-owned background command.

State which boundary was established: Codex inactivity/closure, terminal disconnect, logout, host sleep, or reboot. These are different. A user service can outlive Codex while its user manager remains alive; logout needs separate manager-lifetime checks. Suspend pauses execution. A transient service does not provide reboot recovery. Do not change linger, power policy, login policy or boot configuration merely to package or submit a calculation.

## Prepare a reviewable run

1. Identify the real executable/interpreter, working directory, task arguments, input/config files, expected outputs and project acceptance conditions. Use argv arrays and absolute paths; capture only deliberately selected files, never credentials or an environment dump. Resolve interpreter/module/library paths instead of assuming an interactive shell's Conda activation survives.
2. Inspect current jobs plus CPU, available memory, disk and accelerators. Distinguish queue size from active workers and solver threads. Start from a small measured case when cost is unknown. The helper intentionally runs one task at a time; its thread setting does not constrain every MPI/GPU/custom thread pool. Do not hard-code a particular project's core or memory budget as a universal rule.
3. Ensure the **queue controller itself** will be supervised, not merely its first solver. Freeze the plan/controller, record selected code/config hashes, use a unique external run directory, and arrange a project-level exclusion lock when separate runs could write the same outputs. A per-run lock cannot deduplicate unrelated output directories or an already active old launcher.
4. Define failure handling and checkpoint semantics before submission. A heartbeat proves controller activity, not solver progress; a PID alone does not establish identity. Use the scheduler job/unit identity, process start identity where available, cgroup and recorded inputs. Read [recovery-and-evidence.md](references/recovery-and-evidence.md) for interruptions, retries and evidence reporting.

## Submit and prove ownership

Use a service or batch submission that returns control after transfer. With systemd use a transient `.service` managed by the service manager, not `--scope`, `--pty`, `--pipe` or a long-lived tool cell. The included helper requests `Type=exec`, explicit resource properties and no automatic restart.

After submission, inspect the recorded receipt and current scheduler state. For a running local queue, verify the unit's MainPID, process/cgroup membership and fresh queue state. For a short task that already ended, inspect scheduler exit/result and terminal task records. For Slurm, a job ID can mean pending; verify pending/running/terminal status separately. Do not report “running” solely because a submission command returned zero.

`nohup`, `&`, `disown`, a retained PTY, a tool session ID, or `start_new_session=True` alone do not prove survival of an application's process cleanup or cgroup termination. They are not the durable submission path of this skill. Do not keep a chat awake or schedule recurring assistant messages as a substitute for an independent supervisor.

## Inspect, stop and recover

Keep all run artifacts in the project/runtime directory, outside the installed skill and public sharing repository. Report task/attempt counts, scheduler state, exit codes, last observed scientific progress and actual checkpoint coverage. Distinguish `process_finished_validation_pending`, accepted results, failed validation, resource failures, explicit cancellation, interruption and unknown state. Resource/OOM attribution needs scheduler/system evidence; a missing heartbeat alone is insufficient.

Do not restart existing production jobs just to demonstrate this skill. On an interruption, preserve the attempt and logs, verify no matching job/worker is still alive, and check input/code/checkpoint compatibility. Re-run only explicitly selected unfinished work with the project's recovery command. The helper's recovery manifest is a compatibility assertion, not a physical checkpoint converter or proof that a nonlinear solver can resume.

Estimate remaining time from comparable **completed** tasks or reliable completed steps. Separate waiting time, recorded active duration and time since last heartbeat. If no comparable task has completed, state that a measured estimate is unavailable; label any scenario table as an assumption. Do not count stale wall time as productive compute time.

## Automatic selection and project policy

Keep implicit invocation enabled. The description covers formal computation requests without a duration threshold; explicit `$computation-queue-runner` remains available. For projects that require consistent application, offer the opt-in [project rule](references/project-policy.md). Do not edit unrelated project instructions or claim automatic skill matching is an unconditional guarantee.

## Deliver

Return the supervisor/backend, unit or job ID, external run path, concurrency/threads and verified state, then concise commands to inspect and stop it. State the established lifetime boundary and the last confirmed checkpoint/acceptance state. For an integration task, also deliver the portable skill, an example plan, behavior tests and updated toolkit documentation; do not include the user's diagnostic logs, private paths or chat identifiers.
