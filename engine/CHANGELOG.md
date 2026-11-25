# 变更记录

## [未发布]

### 变更

- 完成任务时两种部署都要求 `verification`：每条验收条件一条证据，`criterion` 必须逐项匹配；`path`/`hash` 可留空（决策类任务），填写则必须成对并校验文件。
- 任务有已发布成果时，至少一条证据必须指向该成果的内容。
- `checkpoint_job` 不再接受 `artifacts`、`sources`：成果与来源只能由 `save_artifact` / `publish_file` 写入。
- `list_projects` 不再写 `.workspace/registry/projects.json`（该文件无读取方）。

### 修复

- 时间线事件一律写入完整的结构化块（含 `memory: null` 的普通事件），不再被当成 `legacy` 自由文本污染项目记忆。
- 正文中的 `<!--` 在写入时单向转义为 `<\!--`，摘要含事件终止符也不会再让记录静默降级。
- `doctor` 与 `list_jobs` 遇到损坏的 `job.json` 时返回结构化问题清单，不再抛出 `KeyError`。
- 任务记录缺少必需字段时，错误信息指明具体字段名。

### 新增

- `move_work_file`：在工作稿与过程材料之间移动，字节不变；存在未提交 checkout 或待决发布时拒绝。
- `delete_work_file`：删除工作稿或过程材料，旧字节进入历史；成果正本不可删除。
- CLI `--debug`：失败时把 traceback 输出到 stderr，stdout 仍为 JSON。
