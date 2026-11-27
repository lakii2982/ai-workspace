# 日常使用

以下 CLI 示例在 `aiws_core/` 下执行，默认操作同级 `Workspace/`。项目和任务编号以工具实际返回为准。还没有项目时，先运行 `./aiws project create --name '个人知识整理'`。

## 主动上下文与项目 Git

在项目范围内运行 `./aiws call initialize_project_git --params '{"project_id":"PRJ-0001"}'` 启用项目独立版本库。框架源码仓库不承载业务文件；无远端也可使用。

Agent 开始工作先定位并恢复已有任务。resume_job/resume_session 直接返回当前任务的工作位置、检查点和下一步。已纳管的单项目任务会提示哪些已读资料有变化。快速接续不刷新索引；需要完整项目概览或按意图查资料时，再调用 prepare_work_context(jid=..., intent='选型 维护')。未启用 Git 的项目仍可恢复进度，但不会被标为已检查资料版本。

手改文件或丢入新资料后不需要先提交；系统比较阶段快照与当前目录。调用 read_project_file(project_id,path,commit=observation.commit) 阅读观察版本；path 是项目相对路径，含工作稿与未登记资料。compare_project_file 可提取旧新版办公文档文本差异。不支持的格式返回明确可读性结果。

阅读后调用 mark_context_read(jid,observation_commit,paths,lease_token)，只确认实际读过的文件。文件再变化会重新进入队列。主稿位置从 context.working_files 取得，不凭最后修改时间猜测。

阶段结束先保存文件和有意义的记忆，再调用 save_work_checkpoint(jid,revision,context_patch,patch,lease_token)。context_patch 可设 working_on、resume_note、working_files、memory_refs；patch 沿用任务进展、问题和下一步。快照写入独立 Git refs，不移动分支或改真实暂存区，不表示文件由 Agent 创作或业务成果已批准。

项目记忆通过 append_timeline 的可选 memory 字段记录 category/state/reason/supersedes；list_project_memory 和 read_project_memory 用于查询当前决定及理由。手改时间线不一致内容提示待整理，不继续当作核实后的约束。历史字节可经 read_project_file(...,path='记忆/TIMELINE.md',commit=...) 回看。

长期有效的知识用记忆条目保存，沿用 Claude 的记忆方式：`记忆/条目/` 下一条记忆一个 Markdown 文件，`记忆/MEMORY.md` 是索引。类型分为 user（用户角色与长期偏好）、feedback（对工作方式的纠正与已确认做法）、project（无法从资料推导的目标与约束）、reference（外部资源指针）。开始工作时先看 prepare_work_context 返回的 `memory_notes` 索引，按描述只读取相关条目，不全量加载。写入用 save_memory_note；更新或删除前先 read_memory_note，并带当前 hash。索引由核心按文件重建，手改 `MEMORY.md` 不会改变实际记忆。时间线记忆记录任务内的决定演进，记忆条目记录跨任务长期有效的事实，两者不互相替代。决定先写时间线并保留来源；需要跨任务记住时再蒸馏成记忆，`source` 写 `event:<事件ID>`，同一事实不重复叙述。

```sh
./aiws call list_memory_notes --params '{"project_id":"PRJ-0001"}'
./aiws call save_memory_note --params '{"project_id":"PRJ-0001","name":"report-style","description":"用户偏好要点式汇报","type":"feedback","content":"# 汇报用要点\n\n**Why:** 用户在手机上阅读。\n\n**How to apply:** 交付前压缩长段落。"}'
```

Git 版本与工作区以磁盘已保存内容为准。ignored 文件、嵌套仓库和不能解析的附件不会被宣称已读。普通 clone/push 不完整携带 AIWS 自定义 refs，完整迁移应使用工作空间私有备份，备份包含已注册项目的 Git 目录与任务状态。

get_project_overview 提供任务分组与成果、问题、下一步；不会把任务完成比例当成项目完成度。prepare 的 brief 若有省略，按 expand 取 full；Agent 仍须阅读需要修改的正文，不能只看摘要。

项目概览优先展示待验收任务，其次才是阻塞、进行中和新任务。已完成或已取消任务不占用当前工作的摘要名额，需要回看时用完整概览或归档资料。

告诉 Agent 你的目标和项目名称即可；Agent 负责查项目、绑定范围、查证来源和持久化任务。不要依赖聊天记录保存进度。

原件放在项目的资料目录。公司知识放已确认企业事实；专业知识放方法与模板；工作草稿不进入默认检索。当前不自动判断事实是否过期，引用时必须核查生效日期、批准状态和冲突版本。

```sh
./aiws project list
./aiws index update
./aiws search --project PRJ-0001 --query '验收'
./aiws source read --params '{"source_id":"用搜索返回值替换","scope":{"include_company":false,"include_professional":false,"project_ids":["PRJ-0001"],"topic_ids":[]}}'
./aiws job list
./aiws job resume --params '{"jid":"JOB-0001"}'
```

检索返回原路径、段落/表格/单元格/页码和文件指纹。索引有失败清单时应先处理，不能把无命中解释成无事实。扫描 PDF 提示 OCR 受限；DOC/XLS 提示人工另存；公式显示缓存状态，不计算或保证缓存新鲜。

正式发布前由 Agent 展示具体成果供确认。普通草稿保存在本任务工作目录，旧 hash 不匹配时拒绝覆盖；候选内容保留在 .workspace/history/conflicts。历史原内容保存在 .workspace/history/files，可查事件记录定位。

复杂跨项目工作可在 scope.project_ids 显式绑定多个项目；当前跨项目专题独立身份尚未实现，topic_ids 必须为空。

## 框架演进版：日常执行协议

新工作空间使用本地状态。已配置并启用飞书的工作空间可使用飞书保存共享状态，业务文件仍在本机目录。通过 `aiws_core/aiws` 或绑定同一工作空间的 MCP 服务操作 `job.json`、`HANDOFF.md` 和数据表。

1. `project list` / `job list` 默认20条；通过 `--params` 传 project_id/query/status/limit/offset。有任务ID直接resume，没有ID用 `call resolve_job`（project为项目ID、全名或别名）；多个匹配必须消歧。
2. `job resume` 默认摘要。omitted非空表示约束有省略，执行相关动作前用 `detail:"full"`展开。默认不校验所有附件；需要核验时使用完整恢复。
3. 飞书模式开始写入先 `call claim_job`，参数jid/owner/revision，返回lease.token；后续checkpoint、保存文件带lease_token。认领30秒至2小时，到期前renew_claim。旧凭证被拒绝后重新恢复，不盲目重试。
4. 新任务必须包含项目范围和稳定request_id。相同request_id重试不重复创建；同ID不同内容拒绝。
5. `call bind_session`绑定session_id/jid，换客户端用resume_session恢复最新状态。不要把上次会话的文本当成最新事实。
6. `call write_work_file`按jid/project_id/name/kind写工作稿或过程材料，旧文件必须带expected_hash。`call publish_file`须绑定source_hash和具体用户确认，旧成果进入同目录历史版本。没有成果表。
7. 重要决策、范围变化和交付结果用append_timeline写入所属项目；必须提供事件日期、记录日期、性质、状态、影响、责任人、后续和来源，带旧hash；事件ID可安全重试。
8. 完成任务先 review，再 completed。飞书模式必须给每条验收条件提供verification中的criterion/path/hash，且没有未解决阻塞。完成技术工作不代表用户已批准文件发布。交接用release_job并提供next_steps。

项目元数据用get_project取得state_hash，再以update_project提交expected_hash/patch。状态说明与状态分开。单选状态展示中文，API继续使用稳定英文值。

文件布局：新项目为 `项目/PRJ编号_名称/`；新任务为 `工作/JOB编号_标题/工作稿或过程材料/` 和 `成果/JOB编号_标题/`；时间线为项目内 `记忆/TIMELINE.md`，长期记忆为 `记忆/MEMORY.md` 索引与 `记忆/条目/`。旧项目与任务路径兼容保留，不改动历史来源链接。

### 过程稿与成果物

- `过程材料/`：证明与复现用（原始数据、日志、证据、脚本、导出件），不作为结论引用。
- `工作稿/`：可读的中间结论，还会被改，对外不可引用；定义上"终将成为成果物"。
- `成果/JOB编号_标题/`：交付正本，内容冻结；对外引用只认这里，旧版自动进同目录 `历史版本/`。

移交门槛（全部满足才发布）：脱稿可独立阅读；无 TODO/待补/占位符；内容冻结，后续修改走新版本号；关键事实有出处；用户对具体这一版确认；文件名为 `主题_v主.次`，不带"终版/最新"。

发布用 `publish_file`（source 为工作稿路径 + source_hash + confirm=true + lease_token）。任务完成时入口校验：工作稿仍有文件而成果目录为空返回 `DELIVERABLE_REQUIRED`；定稿发布到成果，不交付的移入过程材料。无文件任务（决策、排障）不受此限。存量任务不自动回溯：已关闭任务做存量迁移时直接对 成果/ 调用 `publish_file`（confirm=true 留痕、无需租约、不重开任务）；其他写入仍要求租约。

### 定稿与已发

`publish_file` 只表示定稿已经进入成果目录。员工明确表示已经发出后，才用 `record_sent` 记录该成果的 `source_hash`，没有链接也可以记录为用户陈述。若已有渠道回执，将回执文件放入项目资料并传其路径与 hash，才会标为 `channel_receipt`。渠道名或任意字符串本身不构成外部核实。它不会主动发送或访问渠道，改版后应对新 hash 另记一条，不能把旧版已发泛化成新版已发。

不交付的稿子用 `move_work_file` 移入 `过程材料/`（字节不变，需要当前 hash），删除工作稿用 `delete_work_file`（旧字节进入历史）。两者都拒绝存在未提交 checkout 或待决发布的文件；成果正本不能删除，改版请发布新版本。

完成任务时按验收条件逐条给出 `verification`。决策、排障类任务可以只给 `criterion`；有交付物的任务至少一条证据的 `path`/`hash` 必须指向已发布的成果文件。本地与飞书模式规则相同。

## 故障与部署边界

- 本版是**单机协调入口**：各Agent在同一工作空间使用CLI/MCP。多主机不得分别复制工作空间直接写同一飞书表；需要统一连接协调主机。配置检查会拒绝恢复副本成为第二写入者。
- 远端超时或回读尚未一致时返回REMOTE_UNCERTAIN；先call reconcile_remote。未核实之前不声称已成功，也不盲目重新创建。
- 直接改系统维护列会触发REMOTE_DRIFT。应使用update_project/checkpoint_job；界面隐藏系统列不等于访问权限隔离。
- backup保存本地文件与任务缓存；restore校验文件后默认恢复为离线本地快照，不自动成为远端写入者。
- rollback_feishu先回读远端最新任务再切回本地；保留远端记录。migrate_feishu可再次逐项验证并启用。不要手工切配置冒充迁移。
- 既有MCP进程在升级时应重启以刷新工具签名；新服务每次操作运行当前CLI逻辑，避免长期进程保留旧写入规则。

### 日期时间填报规范

项目表与任务表中承载时间值的字段统一使用 `datetime`，显示格式 `yyyy-MM-dd HH:mm`（24小时制）。显示到分钟，原始秒及毫秒保留，不截断为整分钟；接口传入带时区的 RFC3339 时间，禁止无时区字符串。系统内部统一 UTC，界面按飞书配置时区显示。

任务表「更新时间」取任务检查点更新时间，由 aiws 维护，不使用记录自动更新时间替代；「认领有效期」取租约到期时刻，由 Unix 秒转换为日期时间，未认领时留空，不填 0、文本或虚构日期。上述字段均通过受控入口更新。项目表目前无时间值字段；「时间线入口」为路径，仍为文本。今后新增时间字段沿用此规范。

## 问题反馈

使用框架时遇到工具报错、入口不可用、文档与实际不符，或某个场景因框架限制做不下去，Agent 会先处理你手头的事，再问你是否要把问题提交给框架仓库。

- 同意后，Agent 先展示将要提交的标题和正文，再调用 `report_issue` 提交。正文包含问题现象、复现步骤、预期与实际，以及框架版本、系统版本、Python 版本和客户端名称；不包含项目名称、文件路径、文件正文或客户资料。
- 本机安装并登录 GitHub CLI（`gh auth login`）时直接创建 Issue；否则返回预填好的新建 Issue 链接，点开确认即可提交。
- 报告同时保存在工作空间 `.workspace/feedback/reports/`；提交失败或当时没有网络时，可以让 Agent 重新提交。
- 不想上报直接拒绝即可，不影响当前工作。
