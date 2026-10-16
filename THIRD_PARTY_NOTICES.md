# 来源、署名与分项许可

本仓库由 Zhiyuanlll 整合为 23 项科研技能工具集。Nature 系列基于 [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills)，保留 fork 关系及上游历史。上游许可核查基准为 `19be8d45e0d5233aa44be6d4572e655b9914ff54`；本地技能快照并非该提交的逐字节副本，也不声称所有内容均由本仓库作者原创。

## 许可范围

- 根目录 [LICENSE](LICENSE) 为 Apache-2.0，适用于上游按该协议发布的内容及本仓库新增的整合文档、安装/校验工具与自编技能内容；下列已有许可和第三方权利优先。
- `skills/nature-downloader/LICENSE` 保留 MIT 许可及 Copyright (c) 2026 baihe26。
- `nature-literature-pipeline` 与 `nature-proposal-writer` 的 SKILL.md 声明 MIT，保留这些声明及原作者元数据；它们不因根目录许可而改为 Apache。
- `skills/nature-figure/assets/figures4papers/` 来自 [ChenLiu-1996/figures4papers](https://github.com/ChenLiu-1996/figures4papers)，作者 Chen Liu，按 **CC BY-NC 4.0（署名—非商业性使用）** 使用。全文见 [LICENSES/CC-BY-NC-4.0.txt](LICENSES/CC-BY-NC-4.0.txt)。许可核查提交为 `16c00e69bf7427a30df9646e220df4072cca3600`；这是核查基准，不表示所有嵌入文件均直接来自该版本。
- 绘图技能中引用、改写该项目的参考文档（包括 design-theory.md、api.md、common-patterns.md、chart-types.md 及相关 demos/tutorials）所含派生部分也应遵守该来源许可。需保留署名、来源及改动说明；商业使用应先取得相应授权。
- 论文截图、出版商指南摘录和其他外部材料保留各自权利；根目录协议不赋予超出原来源的权利。引用链接也不表示将被链接的项目重新许可。

因此，整套工具集含有非商业限制材料，不能将全仓库统一描述为“无限制商用”或“全部原创”。

## 整合及改动说明（2026-10-15）

本次将本地已有的 Nature 技能适配快照与 research-context-recommender、knowledge-library-builder、scientific-workflow-builder、research-weekly-review、academic-writing-fidelity、jmps-writing-fidelity 组织为科研全流程，并编写安装、校验、配置示例及中文使用指南。保留本地快照已有作者、版本和许可声明。

已知分享适配：四个技能将 author/version 移入 metadata；nature-reader 评测中的作者本机路径替换为通用路径；nature-proposal-writer 与 nature-literature-pipeline 增加工具/平台缺失时的适配说明。精确路径见 PACKAGE_MANIFEST.json 的 share_copy_modifications。发布版另补充许可文件、来源说明、GitHub 安装方式和技能入口中的再分发标记。

仓库中的其他差异可能来自较早快照或此前本地适配，不全部归因于本次整合。UPSTREAM_COMPARISON.json 记录相对于上述核查提交的文件散列差异，供追溯，不能替代逐文件著作权判断。

## 独立计算执行能力（2026-10-16）

新增自编 computation-queue-runner，按 Apache-2.0 提供正式科研计算的独立提交、状态核验与恢复工具，适用于长短任务；同步调整工作流交接、中文指南、离线手册及清单。已有第三方许可及署名保留，未纳入个人聊天或项目运行记录。
