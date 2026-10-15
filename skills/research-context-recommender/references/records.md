# 配置和持久记录

运行配置与个人科研数据放在调用方的 `runtime_dir`，不放在技能安装目录。`config.json` 保存发现入口和输出偏好；`records.json` 保存带来源的追加事件。记录助手只存储、校验和整理记录，不负责理解用户、检索、判断文献版本关系或安排调度。

## 配置

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "threads": {
    "recommendation": "实际聊天ID",
    "weekly_summary": "实际聊天ID",
    "weekly_plan": "实际聊天ID"
  },
  "discovery_seeds": [{"project": "某项目", "path": "/实际项目目录", "purpose": "仅作发现入口，重点以最新证据为准"}],
  "preferences": {"recommendation_mode": "dual-track", "core_papers": {"min": 5, "max": 10}, "latest_papers": {"min": 0, "max": 3}, "active_questions": 3, "lookback_days": 7, "fallback_lookback_days": 14, "initial_publication_days": 14, "require_ratings": false},
  "search_skill_path": "/实际技能目录/nature-academic-search/SKILL.md",
  "legacy_recommender": {"read_only": true, "path": null}
}
```

`threads` 是只读资料来源和调用方指定的交付上下文，不授权给其他聊天发消息。`discovery_seeds` 可补充新项目，不能当成已经核实的研究状态。旧反馈器路径为空时不导入其数据库；用户在推荐聊天中的文字反馈可直接使用。

`core_papers`、`latest_papers` 是所有方向合计的目标范围，合格论文不足时可低于核心下限；两类跨类去重且不得双计。`initial_publication_days` 仅用于首次最新进展检索，核心检索不附发表年份限制。旧 `focus_papers`/`exploration_papers` 是历史配置，不等同于新旧分类或学习深度；迁移时采用双类字段，保留其他个人配置。

可选 `domain_background` 保存用户对领域背景的明确说明，每项包括稳定 `domain_id`、名称、初始 `stage`、原话与来源；只在该领域被选中时辅助判断，不自动指定每周方向或占用名额。运行中的最新有证据画像保存在状态事件，不能只依赖初始背景永久定级。

## 命令

以下路径用实际绝对路径替换：

```text
python3 <skill_dir>/scripts/record_store.py --store <runtime_dir>/records.json init
python3 <skill_dir>/scripts/record_store.py --store <runtime_dir>/records.json context
python3 <skill_dir>/scripts/record_store.py --store <runtime_dir>/records.json append --input <absolute_events_json>
```

`init` 不覆盖已有文件；`append` 接受一个事件或事件数组。整批通过校验才写入；相同 ID 与内容重复提交不追加，已存在 ID 的内容不同则报冲突。事件 ID 应由原始消息或运行标识产生，重试复用原事件的时间和内容。

`context` 返回最新状态（包含领域画像）、全部原话反馈、去重后的历史文献标识，以及最近 12 次运行概况。`known_papers` 只提供身份摘要，不包含旧推荐的栏目、前置知识或阅读安排；要回查这些内容或最新进展覆盖终点，在 `records.json` 的 `events` 中按运行事件 ID 读取原始推荐及 `track_searches`，避免每次把所有报告灌入上下文。历史记录缺少新字段时保留原样，不把旧探索组自动解释为最新进展。

助手使用 Python 标准库，写入时锁定同目录锁文件并原子替换 JSON。该版本面向当前 Linux/POSIX 本地环境。不要用它写共享网络目录或推断跨设备同步。不要手动重置损坏记录。

## 统一事件

```json
{
  "id": "稳定事件标识",
  "kind": "state",
  "recorded_at": "2026-10-19T09:05:00+08:00",
  "payload": {}
}
```

`kind` 为 `state`、`recommendation` 或 `feedback`。所有时间使用带时区的 ISO 8601 字符串。`recorded_at` 是实际记入时间；原证据日期保留在 `sources` 或对应事实中，不能用记入时间替代核实日期。示例日期只说明格式，不是实际成果。

来源对象统一为 `{"uri":"codex://threads/实际ID","message_id":"实际消息ID","date":"原消息日期","evidence":"依据的简短内容"}`，文件来源使用实际绝对路径或相应文件定位。网站使用原始 URL。必须有非空 `uri`，其余字段按实际证据填写。任何来源都不生成虚构 ID、引文或摘录。

## 研究状态事件

`id` 例如 `state:<run_id>:<as_of>`，同一次未完成运行重新观察状态时使用新的观察时间，避免与先前快照冲突；`payload` 至少包括：

- `as_of`：本次状态观察时间，带时区。
- `projects`：项目数组，建议每项包含 `project_id`、`questions`、`results`、`blockers`、`priority`、`last_verified_at`、`sources`。问题 ID 稳定。结果保留 `status`（可核查/用户报告/推断/计划）和底层证据。
- `sources`：本次实际读取的来源对象数组；`coverage` 可另记读取失败或未覆盖范围。

状态中新增 `domain_profiles` 数组，按领域保存：`domain_id`、`name`、`stage`、`known_topics`、`learning_needs`、`current_goal`、`assessment_basis`（用户明确说明/有证据的推断/待确认）、`sources` 和 `last_verified_at`。`stage` 为 `entry`、`orienting`、`problem_driven`、`results` 或 `unknown`，含义以 Skill 正文为准；每个领域独立判断，并允许部分概念已掌握、部分仍需补足。旧状态缺少画像时表示尚未判断，不从聊天量或推送记录补造研究深度。

首次初始化保留空事件，不根据项目名称预填研究结论。每次状态是完整的已知状态快照；旧项目可以标记信息过时，不能仅因本周没有聊天就断言项目停止。

## 推荐事件

`id` 为 `recommendation:<run_id>`；定时 `run_id` 如 `weekly:2026-10-19`，手动追加使用 `manual:<带时区时间>`。`payload` 至少包括：

- `run_id`、`status`（`complete`、`partial`、`failed`）。
- `papers`：推荐数组，检索失败或没有合格论文时允许为空。
- `report_markdown`：本次准备交付的完整报告。它保留可追溯来源和限制，不证明已经发布或用户已阅读。

另保存 `query_plan`、`actual_sources`、`limitations` 及 `track_searches`：按 `core`、`latest` 分别记录 `status`、查询、实际来源、范围与限制；核心 `publication_window` 为 `null`（不限年份），最新保存明确的 `start`/`end` 与日期口径。只有最新分支实际完整成功，其窗口终点才可作为下次增量起点；旧记录缺少此信息时不能猜测覆盖已完成。其中检索式和来源必须来自实际执行。`complete` 表示拟执行的检索已完成，不能宣称穷尽文献；存在未完成来源/范围时用 `partial`，无法形成有效检索时用 `failed`。

每篇至少包含：

```json
{
  "paper_id": "doi:规范DOI或arxiv:规范ID或url:出版商原文URL",
  "aliases": [],
  "title": "核实后的题名",
  "url": "可点击的DOI或原文链接",
  "question_id": "当前稳定问题ID",
  "reason": "为什么值得为此问题阅读",
  "unverified": ["未核实的具体条件"],
  "evidence_level": "abstract",
  "sources": [{"uri": "实际核实来源"}]
}
```

新推荐为每篇补充 `domain_id`、`group`（唯一展示栏目 `core` 或 `latest`）、`tracks`（匹配类别数组，可同时包含 `core` 与 `latest`）、`reading_role`、`prerequisites`（前置知识数组）、`reading_priority`（包含顺序与精读/浏览建议）。同一篇只有一条记录及一个展示栏目；兼属两类时 `tracks` 保留双重归属，不重复计数。按实际情况补充 `authors`、`publication_date`、`online_date`、`publication_status`、`verified_at`、`version` 和 `work_family_id`。`reason`、`unverified` 支持字符串或字符串数组，`unverified` 可为空数组。`evidence_level` 只能为 `metadata`、`abstract`、`fulltext`。

`paper_id` 与 `aliases` 使用规范标识符，不能以展示序号标识论文。别名/版本族只在查到明确关联时填写；可同时保留 DOI 与 arXiv 标识，并在 `sources` 中保留版本关联证据。同一工作的历史推荐按明确标识关联，不能仅凭同题名归并。

## 自然语言反馈事件

`id` 建议为 `feedback:<thread_id>:<message_id>`；一个消息包含多个条件时可一起保存于 `interpretation`。`payload` 至少包括：

```json
{
  "text": "用户的原话，逐字保留",
  "source": {"uri": "codex://threads/实际ID", "message_id": "原始消息ID", "date": "原消息日期"},
  "scope": {"kind": "question", "id": "适用的问题ID"},
  "interpretation": {"action": "prioritize", "meaning": "本周优先找参数确定方法", "valid_until": "2026-10-25", "certainty": "explicit"}
}
```

`scope.kind` 为 `paper`、`question`、`project` 或 `global`；除明确全局反馈外，填写相应稳定 ID。`interpretation` 是助手对检索决策的解释，始终与用户原话分开。临时条件使用 `valid_until` 或原始时间表达，过期后不作持续硬过滤；歧义标为 `unclear`。后续纠正追加新事件，不覆盖旧反馈。不要生成合成用户反馈或把助手建议写成用户偏好。
