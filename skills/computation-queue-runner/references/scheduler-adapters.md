# 已有调度器与其他平台

这份参考说明通用适配要求；随附 Python 工具的自动提交后端仅为 Linux 用户级 systemd。不要把下面的示例当作已在本机验收的 Slurm、macOS 或 Windows 执行器。

## Slurm 集群

已有 Slurm 时优先使用 `sbatch` 批任务，遵守站点分区、账户、时限、内存、CPU/GPU 和登录节点规则。`sbatch` 成功只表示脚本传给控制器并获得 ID，可能仍在排队；它不会自动传输其他代码或输入文件。[官方 sbatch 文档](https://slurm.schedmd.com/sbatch.html)

准备不可变的脚本/配置和共享存储中的独立运行目录，再提交脚本。最小示例需要先替换路径及资源，确认项目的线程和验收约定：

```bash
#!/bin/bash
#SBATCH --job-name=research-pilot
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:10:00
#SBATCH --mem=1G
#SBATCH --output=/absolute/run/slurm-%j.out
set -euo pipefail
cd /absolute/project
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
/absolute/venv/bin/python /absolute/project/solver.py --config /absolute/run/config.json
/absolute/venv/bin/python /absolute/project/validate.py --run /absolute/run
```

`#SBATCH` 指令不做 shell 变量展开，输出目录应提交前存在。用 `sbatch --parsable script.sbatch` 保存回执；用 `squeue -j JOB_ID` 看等待/运行，用 `sacct -j JOB_ID --format=JobID,State,ExitCode,Elapsed,MaxRSS` 检查终态/步骤，用 `scancel JOB_ID` 取消选定任务。批脚本必须传播求解与验证退出码；仅看总脚本成功可能漏掉内部步骤失败。[官方退出码说明](https://slurm.schedmd.com/job_exit_code.html)

大批量可用 array 和 `%N` 控制活跃任务数，或用 `afterok` 依赖；这不免除每个任务的线程/内存核对。不要通过高频查询给控制器施压。抢占、节点故障和时限需要应用保存检查点并遵守站点恢复政策；不能承诺跨这些事件无损运行。

## macOS、Windows 与远程主机

macOS 可按需求使用 launchd，Windows 可使用任务计划程序或系统服务，远程主机使用该主机已有服务/批调度设施。适配前核实工作目录、解释器、非交互身份、会话/注销边界、日志、资源、取消及恢复。应在目标环境用短小合成任务测试，不直接套用 Linux 命令或要求朋友必须购买某种服务。

远程 SSH 中的终端复用器可帮助交互维护，但自身不提供调度、自动恢复和科学验收。容器也需要宿主机独立管理，不能仅因用了 Docker、tmux 或 nohup 就声明已满足关闭 Codex 后持续计算。提供已验证的边界和剩余限制。
