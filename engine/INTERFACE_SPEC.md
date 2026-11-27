# 接口与数据契约

## Git 主动上下文接口

- initialize_project_git(project_id)：显式启用项目根独立 Git；不变更框架根仓库。
- prepare_work_context(project_id?,jid?,session_id?,intent='',detail='brief')：定位优先 jid、绑定session、明确项目；返回 identity/project_brief/task_context/git_status/observation/git_changes/memory/memory_references/reading_queue/coverage/expand。观察可创建Git对象但不推进任务revision；无Git项目仍可读背景。
- resume_job/resume_session：保留任务结果字段，并附带快速接续的 work_context，只读取目标任务，不扫描任务列表或刷新检索索引。默认接续的 timeline memory 受预算限制，`memory.resume_budget` 说明 totals、omitted 与 full 展开入口，`progress` 保留主稿工作位置、下一步和未完事项，`attention` 给出待审/阻塞计数。明确的单项目可返回主稿位置、Git 观察和已读资料的变化；无法确定唯一主稿或项目时不猜。若受控保存已落盘但清单未保存，返回 `recovery` 和 `recover_artifacts` 下一动作，只扫描该任务的恢复日志。需要完整项目上下文时，调用显式入口 prepare_work_context 或 get_project_overview。
- get_project_overview(project_id,detail='brief')：完整分页聚合后裁剪，coverage提供省略数和任务revision。
- read_project_file(project_id,path,commit=None)：项目相对路径，读取当前观察或指定Git版本；返回文本/locator/chunks及readability，不要求artifact登记。
- compare_project_file(project_id,path,before,after=None)：提取前后文本与diff；受限格式不会伪造差异。
- list_project_memory(project_id,query='',categories=None,view='current',limit=20,offset=0)、read_project_memory(project_id,event_id,include_history=true)：区分当前、tentative、legacy、pending_review；历史由Git及事件关系保留。
- append_timeline增加memory={category,state,reason,supersedes}；category支持decision/constraint/conclusion/experience/hypothesis/question，state支持established/tentative/resolved/withdrawn。未知正文改动不静默当现行结论。
- mark_context_read(jid,observation_commit,paths,lease_token?)：按文件及实际观察版本记录阅读确认，推进任务revision。
- save_work_checkpoint(jid,revision,context_patch?,patch?,lease_token?)：创建快照后更新任务；在此之前业务文件与记忆已保存。context包含working_on/resume_note/working_files/memory_refs/reviewed_files/base_commit/checkpoint_commit/checkpoint_ref，可缺省。
- Git快照保存refs/aiws/observations及checkpoints；不改HEAD/分支/真实index。文件变更以Git blob为版本；旧manifest和受控发布校验不因此删除。

## open_work 会话入口

- open_work(intent, project?, jid?, session_id?, query='', request_id?, create_project=false, new_task=false)：聊天会话的统一工作入口。intent 必填并在每个响应中原样返回；project 接受项目 ID、名称或别名（大小写不敏感）；显式 jid 与显式 project 优先于 session 绑定。参数：intent(str)、project(str,可选)、jid(str,可选)、session_id(str,可选)、query(str,默认'')、request_id(str,可选)、create_project(bool,默认false)、new_task(bool,默认false)。
- status=opened：显式项目/别名唯一命中、已绑定 session 或显式 jid 时返回 work（与 resume_job 相同结构的接续载荷，含 work_context/scenarios/publication_decisions/context_entry），只读取目标任务，不扫描任务列表；created 报告本次是否新建了项目或任务。显式项目下唯一进行中任务视为相关工作直接打开；多任务命中返回候选。
- status=candidates：project/jid/session 均无法定位时返回有界项目候选（≤8，附 has_more_projects）、原始 intent 与 created={'project':false,'task':false}；提供 query 时附跨项目任务线索（含 project_ids 归属）。歧义（多项目/多任务/别名撞名）返回 match=ambiguous 与可区分候选；未知 project 返回 unknown_project 与近似候选。候选路径零写入：不创建项目、任务、会话文件或创建请求；即使工作区只有一个项目也不代选。
- 创建必须显式且幂等：new_task=true 且提供 request_id 才创建任务——标题取 intent 首行（≤80 字符），goal 保存完整 intent，scope 为单项目，复用 create_job 的 request_id 契约（同参重试返回原任务并带 replayed=true；同 request_id 不同参数返回 CONFLICT）。不同 request_id 是不同工作，绝不静默挂到已活跃任务上。缺 request_id 报 INVALID_ARGUMENT，不产生部分写入。
- create_project=true 仅在名称/别名/ID 均未命中时按名称创建项目（命中即复用；PRJ- 前缀只作查找不作创建名）。该路径属于工作区级分配，走独占 gate 锁。
- 会话只在成功打开时绑定；session 已绑定其他任务时打开新工作不改绑（state=elsewhere，附显式 bind_session 切换提示），候选路径原样报告绑定状态（bound/unbound）。
- 跨项目任务在 work_context.project_candidates 列出目标项目，context_entry 指向 prepare_work_context；目标项目必须显式选择。旧数据（缺 context/directory_name 的 job、旧 session 文件）直接兼容。

## 项目记忆条目（Claude 风格）

每个项目在 `记忆/` 下同时保存两类记忆：`TIMELINE.md` 是只追加的任务事件（上一节的 timeline memory），`MEMORY.md` 与 `条目/` 是长期事实。条目一条记忆一个 Markdown 文件，frontmatter 为 `name`、`description`、`metadata.type`（user/feedback/project/reference，可附 `created`、`updated`、`source`），正文用 `[[名称]]` 互链。文件是权威，`MEMORY.md` 由核心根据文件重建；手工改动索引会在 `index_drift` 中报告，下一次写入时修复。

- list_memory_notes(project_id,query='',types=None,detail='index'|'full',limit=20,offset=0)：返回索引文本、按名称/标题/描述/正文匹配的条目、类型计数、`needs_review` 清单、断链和索引漂移。detail=full 附带正文；分页上限 100。
- read_memory_note(project_id,name)：返回单条正文、链接解析、反向链接与当前 hash，供更新或删除前读取。
- save_memory_note(project_id,name,description,type,content,source=None,expected_hash=None)：新建或更新。已存在的条目必须带当前 hash，否则 `CONFLICT`；`name` 为小写字母、数字、短横线或中文的 slug（≤64）；`description` 单行（≤200）；`content` ≤20000 字符；feedback 与 project 正文必须包含 `**Why:**` 与 `**How to apply:**`。相同内容重试返回 `replayed=true`。
- delete_memory_note(project_id,name,expected_hash=None)：hash 必须匹配当前文件；删除后重建索引，并返回仍指向它的 `backlinks`。
- 分工：时间线保存带 sources、日期、责任人和后续的事件与 supersede 演进，默认只返回当前有效结论；记忆条目保存跨任务仍成立的当前真相。决定先写时间线，影响以后工作时再蒸馏为记忆条目，`source` 约定为 `event:<事件ID>`（不校验事件是否存在，便于先写条目后补事件）；同一事实不重复叙述。
- prepare_work_context 的 `memory_notes`（索引与条目描述）和 get_project_overview 的 `memory_notes` 用于接续时加载记忆；时间线记忆仍是 `memory`/`memory_digest`，两者独立维护。
- `记忆/` 不进入检索索引：记忆是上下文，不是来源证据。需要作为证据的事实应回读原件并记录指纹。

所有入口共享 Service。CLI 成功返回 `ok=true` 和 `result`，失败返回 `ok=false` 和 `error`，退出码为 1。`aiws_core/aiws` 默认定位同级 `Workspace/`，可用 `--workspace` 或 `AIWS_WORKSPACE` 显式定位。CLI 默认输出 JSON。

基础命令：init；project list/create --name；index update；search --query --project（可重复）--company --professional；source read；job create/list/resume/checkpoint；artifact save；backup；restore；doctor。非快捷参数以 --params JSON 传入。通用 `call 操作名 --params JSON` 与 MCP 对应同名业务操作。

操作参数：
- create_job: title(str), goal(str), scope(object), acceptance(list[str],可选), agent(str,可选)
- resume_job: jid(str)
- checkpoint_job: jid(str), revision(int), patch(object), reopen(bool,默认false)
- search_sources: query(str), scope(object), limit(int,默认20), strict(bool,默认false)。常规搜索枚举当前范围，未变化的本地文件用设备、文件标识、大小与修改/状态时间复用已有索引。strict=true 或显式 index update 重新校验文件内容。read_source 引用前始终回读原件并核对指纹。旧索引在首次访问时自动补齐派生缓存。
- read_source: source_id(str), scope(object), expected_hash(str,可选)。复核已保存的引用时传入当时的 sha256，原件换版后返回 STALE_INDEX。
- save_artifact: jid(str), relative(str), content(str)或content_base64(str), expected_hash(str|null,默认null表示必须不存在), confirm(bool,默认false), purpose(str,默认draft)
- receive_source: jid(str), project_id(str), name(str,单个文件名), content(str)或content_base64(str), expected_hash(str|null,默认null表示必须不存在), confirm(bool,默认false), lease_token(str,可选), source_ref(str,可选)。聊天附件原件收件：宿主把实际读到的字节按文件名存入项目 资料/，字节只能来自宿主真实读取（文本或base64），不提供任意主机路径读取；宿主未能读取原始附件链接时必须如实告知用户，不得凭空生成内容。新收原件标记为 original。同一工作同内容重试复用首次登记。替换旧文件需 confirm=true 及旧指纹，历史原文保留。source_ref 记录原始附件出处（如聊天文件标识）写入历史事件。收件后按既有检索链路 search_sources/read_source 回读；原件变化按 STALE_INDEX 回查；范围隔离与 search_sources 相同。
- recover_artifacts: jid(str), lease_token?(str)。仅从受控保存留下的 committed journal 回填缺失 artifact 清单。当前文件 hash、任务 scope 和记录的 artifact 必须全匹配，绝不覆盖或回滚人工改动；飞书启用时仍须有效租约，远端 pending 仍按 reconcile_remote 处理。
- restore: archive(str), destination(str,必须为不存在目录)

scope 必须包含 include_company(bool)、include_professional(bool)、project_ids(list[str])、topic_ids(list[str]，当前仅空)。未明确范围不全库回退。工作草稿默认不索引；正式成果归入项目范围。查询阶段SQL过滤，回读再次校验范围、路径和原件hash。

job.json schema_version=1：job_id/title/goal/status/last_agent/updated_at 为必填字符串；scope为必填对象；revision为正整数；acceptance/completed/next_steps/open_issues/decisions为字符串数组；artifacts为path/hash/purpose/status字符串对象数组；sources为source_id/locator/hash字符串对象数组。所有字段必填，未知schema拒绝而非猜测迁移。checkpoint禁止变更ID和scope；当前变更scope需创建新任务。HANDOFF从权威状态派生。快照先写，job.json原子替换为提交点；不能将未提交快照当成最新状态。

关键错误：INVALID_SCOPE、NOT_FOUND、CONFLICT、OUT_OF_ROOT、PARSE_UNSUPPORTED、PARSE_LIMITED、PARSE_ERROR、STALE_INDEX、CONFIRMATION_REQUIRED、INVALID_STATE、SCHEMA_UNSUPPORTED、DELIVERABLE_REQUIRED。

DELIVERABLE_REQUIRED：任务转completed时，若 工作/JOB/工作稿/ 仍有非空文件而对应 成果/JOB/ 无已发布文件（不含 历史版本/），拒绝完成。定稿用publish_file移交成果，不交付的移入过程材料；无文件任务不触发。检索更新的解析失败通过result.index.failures报告；消费者必须检查。源文档指令不具备授权效力。

并发保护使用 macOS/Linux 跨进程 flock；锁随进程退出释放，不按超时删除活跃锁。历史记录 prepared/committed；发现prepared事件应核对当前hash，不能自动认定已提交。Agent用其他文件工具直接修改业务文件不在入口保护范围。

## 任务、时间线和飞书适配接口

- list_jobs: project_id/query/status 可选，limit 默认20最大100，offset默认0。返回 items/has_more/next_offset；不返回附件和历史。旧完整列表显式 call list_jobs_full。
- resume_job: detail默认brief；完整材料与hash校验需detail=full。brief明确返回omitted、expand、integrity，不能把未校验解释为校验通过。常规 brief 保持原形；仅超过 16KiB MCP 预算时确定性压缩长文本和列表，保留主稿、未完/阻塞计数、待审出版身份及 `brief_budget.expand`。被省略的约束执行前须展开。
- append_timeline: project_id/event_id/event_date/record_date/kind/summary/status/impact/owner/next_action/sources/expected_hash。项目内原子追加；旧hash冲突拒绝；相同事件重试去重；不确认建议为事实。
- feishu_query: entity=projects或jobs，key/value精确过滤，limit默认20最大100，offset分页。
- `.workspace/feishu.json` 保存资源坐标，不存凭据。CLI用户凭据由lark-cli维护。单协调工作空间锁不提供多主机并发保障。

## 状态管理、成果发布与迁移

- list_projects(query='',limit=20,offset=0)：items/has_more/next_offset。
- create_job增加request_id；飞书启用后必须提供项目范围及request_id。重复请求复用同一任务。
- checkpoint_job、save_artifact增加lease_token；飞书启用后必需。
- record_task_feedback(jid,feedback,lease_token?)：记录只对当前任务有效的一次性用户意见，不写入项目记忆；相同文本重试返回 `replayed=true`。跨任务仍有效的项目约定继续使用 `save_memory_note(type='project' 或 'feedback')`。
- prepare_handoff(jid,note,requested_agent='',independent_review=false)：生成可带走的接续包，默认建议当前 Agent 继续。点名接收者只记录请求，不把未验证可用性称为已送达。独立复核会明确等待独立接收者。read_handoff 读取接续包，accept_handoff 仅记录已绑定 AIWS 会话的 `claimed_by_bound_session`，不证明外部渠道送达或人工接收；用户明确确认后以 acknowledge_handoff 单独记录 `human_acknowledged`。
- claim_job(jid,owner,revision,ttl=1800)：返回lease.token及新revision。renew_claim(jid,revision,token,ttl)；release_job(jid,revision,token,next_steps)。TTL 30..7200秒。
- resolve_job(project?,query='',jid?)：唯一时返回task；多匹配返回候选，不自动选择。
- bind_session(session_id,jid)、resume_session(session_id)：绑定稳定任务身份，恢复读取远端最新状态。
- get_project(project_id)：返回state_hash；update_project(project_id,expected_hash,patch)：可改name/aliases/status/status_note/goal/owner，路径和ID不变。
- write_work_file(jid,project_id,name,content,kind='draft',expected_hash?,lease_token?)，kind为draft/process。
- publish_file(jid,project_id,source,source_hash,name,expected_hash?,confirm=false,lease_token?)：具体源版本确认后发布，旧目标进入历史版本。任务已关闭（completed/cancelled）时无需租约即可发布，用于存量成果物迁移；confirm=true 仍必须由用户对具体版本给出。
- record_sent(jid,project_id,name,source_hash,channel='',evidence_path?,evidence_hash?,lease_token?)：只记录用户已经发送的已发布成果版本，不会发送、登录或查询外部渠道。无凭据时保留为 `user_statement`；可将已放入项目资料且 hash 匹配的渠道回执作为 `channel_receipt`。传入渠道名本身不构成外部核实。list_sent 按记录时间倒序，按文件名和精确 hash 查询，并返回当前或归档的精确版本；read_sent_version 读取该版本。
- move_work_file(jid,project_id,source,target_kind,name?,expected_hash,lease_token?)：在工作稿与过程材料之间移动，字节不变。expected_hash 必填；目标已存在、源已变化、存在未提交的 checkout 或待决发布时拒绝。
- delete_work_file(jid,project_id,relative,expected_hash,lease_token?)：只允许 工作/ 下；删除前旧字节写入 history/files；成果正本不能删除，改版请用新版本发布。
- completed要求verification数组与acceptance等长、逐项匹配criterion；path与hash必须成对（决策类任务可留空），填写时校验范围与指纹；任务已有已发布成果时，至少一条证据的hash必须指向该成果。阻塞未清空时拒绝完成。本地与飞书模式规则相同。
- migrate_feishu(assignments={},activate=false)：备份后逐项比对远端读回与本地原状态；activate切换权威。rollback_feishu：回读最新状态再切回本地，不删除远端记录。
- reconcile_remote：回读prepared操作，只确认确实匹配的远端结果；未知结果保持未解决。

飞书“执行数据”“数据指纹”为系统维护列，普通视图隐藏；不包含业务文件正文。附件/来源清单存本地内容寻址manifest，远端只存其hash。项目表与任务表的“状态”均为single select。所属项目ID为单向link，所属项目名称为lookup只读列。英文状态映射在remote_store.py中定义。

单协调主机按任务或项目锁定冲突的读-检查-写；工作空间级维护仍独占。远端字段比对检测入口外修改，仍不声称飞书 API 提供原子 CAS，也不支持不同协调主机并行写入。远端成功但本地中断后，manifest和pending回执用于恢复。恢复副本默认offline staging，不自动写远端。

日期时间字段契约：任务表「更新时间」「认领有效期」为 datetime，style.format=`yyyy-MM-dd HH:mm`。CLI CellValue 为带时区 RFC3339 或 null；更新时间从 canonical updated_at 转换，租约 expires 从 Unix 秒转换；UTC 毫秒归一化后比较，时区偏移等价不算漂移，真实时刻变化仍拒绝。执行数据保留原始精度与数据指纹；空租约不写 epoch。项目表暂无时间值字段。

## 员工桌面端

- `checkout_draft(jid,project_id,name,lease_token?,create=false)` 返回基于磁盘当前内容的副本路径与 ID，创建新文件时指定 create。编辑后 `commit_draft(jid,copy_id,lease_token?)` 校验主稿原始版本，冲突时原稿和副本分别保留。副本提交成功后其 ID 固定对应当次结果。
- `request_publication(jid,project_id,name,target_name,reason,lease_token?)` 生成绑定源和目标版本的决定事项。`list_decisions(project_id,jid?,state?,limit?,offset?)` 可分页。用户审阅后 `resolve_publication(jid,project_id,decision_id,approve,source_hash,note?,lease_token?)` 处理指定版本。被新版替换的源稿确认时标为 superseded。
- `save_scenario(jid,title,instructions,reason,confirmed?,expected_version?,scenario_id?,lease_token?)` 保存已泛化的模板、步骤和验收示例。更新时携带原版本及长期修改确认。`list_scenarios` 和 `read_scenario` 用于检索或读取旧版。`apply_scenario(jid,scenario_id,version?,lease_token?)` 将版本固定到任务。任务接续返回 scenarios 与 publication_decisions。
- MCP 的 `verify_desktop_binding` 读取当前握手中的客户端名称和版本；CLI `call list_host_bindings` 可查看历史连接。首次连接记录不代表重启或弹窗测试已通过。

## 框架问题反馈

- `report_issue(title, details, steps?, expected?, component?, confirm=false, include_environment=true, client?)`：用户同意后把框架自身的问题提交到上游仓库。`confirm=false` 只返回 `requires_confirmation`、`repository`、`issue`（标题与正文）和 `submission`，不写文件、不联网；Agent 把标题和正文展示给用户，用户同意后再带 `confirm=true` 调用。
- 提交方式：本机 `gh` 已安装并通过 `gh auth status` 时执行 `gh issue create --repo <仓库> --title <标题> --body-file -`，返回 `status=submitted` 和 Issue `url`；否则返回 `status=manual` 和预填好的新建 Issue 链接，由用户点开提交。`gh` 提交失败同样降级为链接，并在 `submission.error` 记录异常类型。
- 提交记录写入 `.workspace/feedback/reports/<报告ID>.json`，并附一份同名 `.md`；记录 `status`、`url`、`fingerprint` 与完整正文。相同标题和正文已经成功提交时返回原结果并带 `replayed=true`，不重复创建。
- 正文环境块只含框架版本、系统版本、Python 版本、工作空间格式、提交时间和客户端名称；不含工作空间路径、项目名称、文件名或文件内容。`include_environment=false` 可省略整个环境块。
- 默认仓库为 `lakii2982/ai-workspace`，可用环境变量 `AIWS_ISSUE_REPO=owner/name` 覆盖（用于开发实例或镜像）。
- 参数上限：`title` 120 字符、`details` 8000、`expected` 2000、`component` 200、`steps` 20 条且每条 500；`title` 会折叠为单行。
