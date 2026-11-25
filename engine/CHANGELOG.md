# 变更记录

## [未发布]

### 变更

- `checkpoint_job` 不再接受 `artifacts`、`sources`：成果与来源只能由 `save_artifact` / `publish_file` 写入。
- `list_projects` 不再写 `.workspace/registry/projects.json`（该文件无读取方）。

### 修复

- `doctor` 与 `list_jobs` 遇到损坏的 `job.json` 时返回结构化问题清单，不再抛出 `KeyError`。
- 任务记录缺少必需字段时，错误信息指明具体字段名。

### 新增

- CLI `--debug`：失败时把 traceback 输出到 stderr，stdout 仍为 JSON。
