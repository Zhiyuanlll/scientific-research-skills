# 审计格式与边界

`scripts/audit_workflow.py` 使用 Python 3.9+ 标准库，只读文件，不启动计算、不改状态、不联网。项目已有成熟格式时直接审计已有材料即可；JSON 模板用于需要轻量机器检查的新项目。

## 阶段说明 workflow-spec.json

根字段为 `schema_version: 1`、非空 `workflow_id`、非空 `stages` 数组。每个阶段包含：

| 字段 | 含义 |
|---|---|
| `id` | 唯一阶段标识 |
| `role` | 自定义证据角色，如 measurement、validation、prediction、analysis |
| `depends_on` | 直接依赖阶段 ID 数组，可为空；整体须为无环图 |
| `acceptance` | `{id, description}` 数组；列出本阶段应通过的检查 |
| `outputs` | `{id, description, units}` 数组；列出必需输出，单位可为 null |

检查与输出 ID 在各自阶段内唯一。空验收/输出列表虽可表示无相应产物的编排节点，但不得用来绕过计算阶段的实际完成要求。脚本只核对声明，不能判断约定是否充分。

模板中的 descriptions 是设计提示，不是已确定的阈值。将具体检查、实际依据和证据位置写入项目约定与真实检查报告。

## 运行说明 run-records.json

根字段为 `schema_version: 1`、匹配的 `workflow_id` 和 `runs` 数组。每项至少包含：

- `run_id`：唯一运行标识；`case_id`：算例身份；`stage_id`：已声明阶段。
- `status`：planned、running、complete、partial、failed 或 stale。
- `upstream_run_ids`：同一文件中已列出的直接上游运行 ID，覆盖本阶段全部直接依赖。可跨 case 引用共享基线；是否物理兼容由项目判断，不能由不同/相同 case 名推断。

对 complete 记录，另外强制要求：

```json
{
  "provenance": {
    "config": {"path": "snapshots/config.json", "sha256": "实际64位SHA256"},
    "code": {"path": "snapshots/source.zip", "sha256": "实际64位SHA256"},
    "input_fingerprint": "项目定义的实际输入指纹"
  },
  "checks": {"实际验收ID": "passed"},
  "artifacts": [
    {"output_id": "实际输出ID", "path": "outputs/result.json", "sha256": "实际64位SHA256"}
  ]
}
```

这只是字段示例，中文散列占位值不能通过审计。`config` 与 `code` 是文件快照；单源文件可直接记录，多源项目可记录包含相关源文件的归档，不接受目录。Git 提交、工作区差异、环境和输入指纹算法另存项目记录；此脚本不调用 Git 或解包归档。

所有已声明验收须为 passed，所有必需 output 均有唯一文件、匹配 SHA-256；上游记录必须 complete 且通过同次记录审计。check 可取 passed/failed/pending，不能使用布尔值混淆未知。未知阶段、检查、输出、重复 ID 或重复 JSON key 均报错。

非 complete 记录可省略 provenance/checks/artifacts，或使用模板中的 null 占位；它们仍被标记为不完整。非空文件声明仍检查实际文件与散列。恢复或诊断材料可在项目日志中保留，不需伪装为正式输出。

`--root` 必须为已有项目目录；所有被核验的 config/code/artifact 文件在该根目录内。可用相对路径或根目录内绝对路径，解析后的符号链接也不能越界。原始输入在外部数据仓库时可保留其来源和内容指纹；受此助手核验的证据文件应在 root 内创建合法的副本/快照，不能通过越界链接规避检查。

输入指纹只检查非空，**不重新计算、不判断它是否与上游内容一致**。验收结果同样为记录中的声明，脚本不会运行科学测试。代码散列只证明所记录快照的当前字节未变，不证明运行实际使用过它，也不证明源代码快照覆盖全部依赖。

## 命令与报告

```text
python3 <skill_dir>/scripts/audit_workflow.py --spec <project>/workflow-spec.json
python3 <skill_dir>/scripts/audit_workflow.py --spec <project>/workflow-spec.json --runs <project>/run-records.json --root <project>
python3 <skill_dir>/scripts/audit_workflow.py --spec <project>/workflow-spec.json --changed-stage extract_inputs
```

`--runs` 与 `--root` 同时使用。`--changed-stage` 可重复，输出变化阶段自身及传递下游的 `affected_stages`；它是候选影响清单，不自动要求全部重算或改变任何记录。

报告为 JSON，包括 `audit_mode`、`scope`、`completion`、`errors`，运行审计还给出逐条 `records`。错误含 code、location 和 message。

| completion | 实际含义 |
|---|---|
| `not_evaluated` | 仅检查设计，未评价执行完成情况 |
| `records_complete` | 所提供的非空运行记录全部 complete 且满足本助手的声明/文件检查 |
| `incomplete` | 没有记录、存在非 complete 状态、记录错误或证据检查未通过 |

退出码 0 表示设计检查通过，或提供记录全部通过；退出码 1 表示错误或记录仍不完整。计划模板正常返回 incomplete/1，即使 errors 为空。

报告始终保留科学验证及任务矩阵覆盖为 `not_evaluated`。一个上游运行通过记录审计不代表整个研究完成；必须另对照预期任务清单、协议、参考、接受条件和科学证据。退出码 0 不能替代该判断。
