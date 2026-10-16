# Linux 独立串行队列

## 适用环境

`scripts/durable_queue.py` 使用 Python 3.9+ 标准库，提交后由 Linux 的用户级 systemd 管理整个队列。要求 systemd 254+、可连接的 `systemctl --user` 和可写运行目录。它不需要 Codex 私有 API，也不依赖某种聊天软件的活跃状态。朋友可在自己的路径、解释器和项目上使用。

服务模式的进程由服务管理器启动；scope 模式的父进程和执行方式不同。辅助工具采用 `Type=exec`，并禁止命令参数中的环境变量展开，避免路径中的 `$` 被 systemd 改写。[systemd 官方 systemd-run 手册源](https://github.com/systemd/systemd/blob/main/man/systemd-run.xml)

先只读检查：

```bash
systemd-run --version
systemctl --user is-system-running
loginctl show-user "$(id -u)" -p Linger -p State
```

用户管理器显示 degraded 不等于不可用，应进一步检查相关单元与返回信息。若工具报权限/总线不可用，先确定是执行沙箱限制还是目标主机没有用户管理器；不能因此改为工具会话中的 `nohup`。需要授权的执行环境权限应按实际限制处理。

## 从模板到提交

所有正式科研计算（包括短小单算例）均走独立队列。用户已要求开始计算时，由 Codex 准备、检查并实际执行下列提交步骤；用户也可在普通终端独立使用相同脚本。仅要求设计/生成方案时不提交。

以下变量只作当前终端的便利别名；计划文件中写入实际绝对路径，不依赖变量展开：

```bash
runner=/absolute/path/to/computation-queue-runner/scripts/durable_queue.py
python3 "$runner" template /absolute/project/queue-plan.json
# 修改生成的 JSON：解释器、代码、配置、工作目录和任务列表。
python3 "$runner" validate /absolute/project/queue-plan.json
python3 "$runner" submit /absolute/project/queue-plan.json --run-dir /absolute/project/runs/pilot-001
python3 "$runner" status /absolute/project/runs/pilot-001
python3 "$runner" stop /absolute/project/runs/pilot-001
```

运行目录必须是新目录，不要放在技能安装目录或公开仓库内。提交前查看已有进程/队列并确认输出互斥；同一个任务写入相同输出时，应使用项目锁或独立输出目录。

计划为 JSON，`schema_version: 1`。主体字段如下，完整可运行骨架由 `template` 生成：

| 字段 | 用途 |
|---|---|
| name | 项目可读队列名；单元名由工具管理 |
| resources.threads | 单任务的数值线程环境变量；默认 1 |
| resources.memory_max / cpu_quota | 可选 systemd 资源边界；按实测与本机资源配置 |
| inputs | 明确选择的文件，逐项填写绝对 path 与 snapshot 布尔值 |
| tasks | 按顺序执行，每项含唯一 id、argv 数组和绝对 cwd |
| tasks[].validation_argv | 可选项目验收命令；只有该程序实际通过才记录相应验收结果 |

队列并发固定为 1。`argv[0]` 必须为可执行文件的绝对路径，例如虚拟环境的 Python；其余参数按真实含义填写，不写一整个 shell 命令，也不依赖 `conda activate`、管道或 shell 通配。脚本路径、配置和数据参数应使用实际可读取路径。

输入只记录显式选定的文件。`snapshot: true` 保存副本，并替换命令中与该输入绝对路径完全相等的参数，也支持 `--config=/path` 这种选项值完全匹配的形式；不会改写源码内部硬编码路径、import 搜索路径或目录树。需要维持相对 import/包结构的项目应使用独立、固定的代码 checkout，或自行明确打包方式。`snapshot: false` 保留路径并核对哈希，文件改变时不能悄悄继续。不要选中密钥、账号配置或整个私人工作区。

资源声明并不保证科学问题可解。CPU 配额不是独占核绑定；线程环境变量也不能控制所有自定义线程池或 GPU。内存上限被触发会导致失败，工具不会无限重启。检查当前负载与小试实测后，再确定预算。

## 运行边界与核验

提交回执写入 `submission-receipts.jsonl`，当前状态在 `state.json`，冻结计划/输入索引在 `plan.json` 与 `metadata.json`；逐次元数据及状态保存在 `attempts/attempt-NNN.metadata.json` 和 `attempts/attempt-NNN.state.json`，输出日志在 `logs/`。这些文件全部留在运行目录。使用 `status` 联合调度器记录检查：单元存在/状态、MainPID/cgroup、运行记录及心跳。提交失败或核验不全时，不能说“关闭 Codex 后保证运行”。短任务可能在查询前结束，应查终态和退出码。

关闭 Codex 后，只要主机运行且用户管理器仍在，服务可继续调度后续任务；控制器和求解器都不依赖聊天工具进程。`--require-linger` 可要求提交时 `Linger=yes`，否则拒绝提交；它不启用 linger。是否允许改变注销策略，应由使用者明确决定。[systemd 官方 loginctl 手册源](https://github.com/systemd/systemd/blob/main/man/loginctl.xml)

休眠会暂停计算；断电、主机重启或用户管理器退出可能终止任务。当前工具使用 transient 服务，`Restart=no`，不安装开机服务、不修改电源设置。跨重启恢复需要另外设计持久单元、恢复命令、幂等输出及兼容校验，并在目标主机验证。

## 恢复

先执行 `status` 并核对不存在同一队列的活动服务，再按 [中断与证据](recovery-and-evidence.md) 检查科学检查点。辅助工具使用显式恢复清单：

```bash
python3 "$runner" resume /absolute/project/runs/pilot-001 --checkpoint-manifest /absolute/project/recovery.json
```

恢复清单包含 `schema_version: 1`、`task_id`、原始 `plan_sha256`、`checkpoint` 的绝对 `path` 与 `sha256`、`input_sha256` 路径到哈希的映射、非空 `compatibility_statement`、项目自己的 `checkpoint_validator_argv` 和 `resume_argv`。从运行目录的记录读取实际哈希，不编造。先验证该格式适用于当前求解器，再填写恢复命令；验收/恢复命令同样使用 argv 数组及绝对可执行路径。

只恢复失败、中断或取消的当前任务；已结束但待科学验收的任务不会因此被重算或标成通过。以前的尝试和日志保留。检查点通过文件哈希与项目校验程序仍不等于适用于所有物理模型，项目应明确其恢复状态、适用版本和可能损失的工作段。

## 随附验证

```bash
python3 -m unittest discover -s /absolute/path/to/computation-queue-runner/tests -v
```

普通测试使用临时目录及合成命令，不提交研究任务。真实服务验证需要可用用户管理器：提交两个短小任务，让提交端先退出，核对两个任务仍按顺序完成，检查服务身份、取消行为与日志；不通过关闭 Codex 来冒险中断生产计算。测试范围与实际结果写入工具集的打包与验证记录。
