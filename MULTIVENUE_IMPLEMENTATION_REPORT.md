# 多 Venue 人工程序实现报告

日期：2026-10-05，Asia/Shanghai。

已接通四个平台的人工年度采集、独立枚举对账、严格筛选门槛、隔离缓存与顺序批处理。**不能宣称四个平台均已完成生产验收**：AAAI、ECCV 仍为 **NOT_READY_FOR_PRODUCTION**。本轮没有执行任何全年度网络抓取，没有写入 Paper Library，没有启动后台任务。

## 1. Git 与开发边界

- 开发目录：`D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`。
- 基线/main 保持 `8148669c8cdea1525efc7b89fa04f8f92ae57a69`。
- 分支：`manual-multivenue`。创建前不存在；首次创建因沙盒拒绝 worktree HEAD 管理文件写入而部分完成，检查确认是本次创建的基线分支后安全切换，没有覆盖其他已有分支。
- clean migration checkpoint：`9f73b5f418fd059992695ab083d8ee3ec25ba802`，提交语义 `chore: checkpoint clean multivenue migration`。
- checkpoint 前重新验证原 76 项有限测试全部通过，再开始开发。首次普通沙盒测试因临时目录写权限失败，不是代码通过；获准在指定 worktree 写入后重新运行得到 76 passed。
- 未 merge、未 push、未改变 main。没有改动稳定仓库工作文件；Git worktree 共享对象库及分支/HEAD 管理元数据是提交与建分支的必要写入。
- `git diff 8148669 -- config/screening_rules_v1_2.yaml src main.py` 无差异。CVPR 核心实现和冻结 RuleEngine 没有修改。

## 2. 新增/修改文件

| 文件 | 内容 |
| --- | --- |
| `run_venue.py` | 人工统一入口；参数/年份检查；同命令严格顺序；每年独立失败处理；ICML2024 正式目录保护；只在 VERIFIED 后调用原筛选器 |
| `venue_runtime.py` | per-Venue-Year 独占锁；成功 HTTP 快照与 SHA256；原子文件替换；可验证旧快照导入；显式刷新证据；局部 manifest |
| `venue_pipelines.py` | ICML/ICLR/AAAI/ECCV 官方枚举与 adapter 编排；OAI resumption token；Springer 多卷/分页；正式详情与独立证据分离 |
| `venue_audit.py` | 稳定身份/唯一标题及作者匹配；保守差异分类；元数据验证；Open_Issues；仅通过后发布 Formal Corpus |
| `smoke_venues.py` | 固定 worktree/output/smoke；每平台 1 篇详情；有界目录/独立源探测；不调用年度 pipeline |
| `screen_verified.py` | 复用原 RuleEngine；额外严格年度/整型门槛；单年度锁；临时筛选目录整组发布；每个 CSV 输出哈希；不覆盖已有结果 |
| `metadata_transport.py` | 保留既有 retry 策略；最后一次尝试也明确报告过长 Retry-After，不隐藏服务器等待要求 |
| `springer_adapter.py` | 保留既有解析；将每个 `citation_author` 的 `Family, Given` 规范成显示姓名，原始值仍保存在 publisher metadata |
| `tests/test_venue_pipeline.py` | 年度隔离、两个实际子进程并行、重复锁、手动恢复、筛选门槛、保护、显式刷新、无旧运行时依赖 |
| `tests/test_pipeline_enumeration.py` | 小型完整假站点/历史快照端到端；ICML bulk、ICLR program、AAAI issue+OAI、ECCV 多卷翻页；OAI 跨页与循环拒绝 |
| `tests/test_metadata_transport.py` | 增加最后一次请求遇长 Retry-After 的回归 |
| `tests/test_springer_adapter.py` | 增加姓/名顺序和原始作者元数据保留回归 |
| `.gitattributes` | 保持规则 LF；历史 HTML 快照禁止换行转换，保护证据哈希 |
| `MANUAL_WORKFLOW.md` | 更新入口和原子筛选说明，指向新文档 |
| `MULTIVENUE_RUNBOOK.md` | 四终端可复制命令、状态定位、停止/恢复/锁检查和验收限制 |
| `MULTIVENUE_IMPLEMENTATION_REPORT.md` | 本报告 |

没有引入第三方新依赖；沿用当前 requirements 和本机 Python 3.13.5。没有旧 Work 运行时代码导入、global controller、队列、共享可写总表、scheduler、heartbeat 或自动恢复进程。

## 3. 离线验收

原 clean migration：**76 passed**。扩展最终有限测试：**123 passed**，所有测试禁止 DNS/网络。涵盖用户要求的 15 类场景，并补充了原子筛选失败恢复、显式刷新证据保留历史、多卷分页、作者截断、重复 OAI 身份与 token 循环。

最终命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
D:\Anaconda\python.exe -m pytest -q -p no:cacheprovider --basetemp=output/multivenue_acceptance_123 tests/test_allvenues_adapters.py tests/test_iclr_adapter.py tests/test_metadata_transport.py tests/test_ojs_adapter.py tests/test_springer_adapter.py tests/test_manual_screening.py tests/test_venue_pipeline.py tests/test_pipeline_enumeration.py
```

过程中的新增缓存刷新测试发现 Windows 路径长度问题：历史目录叠加两层完整哈希与临时文件长名超限。已改成短层级随机历史文件名和同目录短临时名，完整哈希仍存 JSON，随后全部回归通过。没有删除任何历史测试目录或 pytest_scratch。

没有运行稳定基线所有其他测试；本轮运行范围是原迁移有限集加新增多 Venue 测试。`git diff --check` 通过。

## 4. 小规模真实烟测

首次真实网络请求及随后独立源探测共访问 **14 个不同官方 URL、4 篇详情**。最终修改后的解析器又对这些缓存复核，最后一轮 `requests=0`；真实成功证据保留在每年的缓存 JSON 和 Fetch_Manifest，不能把缓存复核计为新增网络测试。

| 平台/代表年度 | 基础烟测 | 独立入口探测 | 本轮未证明的内容 |
| --- | --- | --- | --- |
| ICML2025 | PMLR v267 目录 3330；详情 1；必需字段通过 | 官方静态 program 3451 个目标 event、8 个非目标 event；事件数不是唯一论文数 | 没有重新联网抓全体详情，没有消除年度审计所有差异 |
| ICLR2025 | 官方 Conference 目录 3703；详情 1；必需字段通过 | 官方 program 3918 个目标 event、122 个非目标 event | 没有对三个年度全部详情进行真实年度审计 |
| AAAI2026 | OJS issue 683 目录 94；article 36958 详情 1；字段通过 | AAAI Press 年度页 48 个 issue 链接；OAI 第一页 100 个 record | 没有抓全 OAI resumption 链或全年度所有 issue/article |
| ECCV2024 | Springer 会议页、首卷第一页 19 章、详情 1；字段通过 | ECVA 目录 2387；会议时间线声明 89 卷/2388 篇；首卷 other-volumes 列出另 88 卷 | 首卷声明 27 章不等于第一页 19 章已齐；没有执行全部书卷/分页；2388 与 2387 差异未解释 |

全部实际响应为 HTTP 200；没有用额外请求故意触发限流。429、500/502/503/504、重试耗尽、长 Retry-After 和连接中断的行为由离线测试验证。真实生产时响应错误必须经 `raise_for_status()` 拒绝，不能解析为成功页。

结果目录：

```text
D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\output\smoke\ICML\2025\Smoke_Result.json
D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\output\smoke\ICLR\2025\Smoke_Result.json
D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\output\smoke\AAAI\2026\Smoke_Result.json
D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\output\smoke\ECCV\2024\Smoke_Result.json
```

官方证据入口包括 [AAAI 年度目录](https://aaai.org/proceeding/aaai-40-2026/)、[AAAI OAI](https://ojs.aaai.org/index.php/AAAI/oai?verb=ListRecords&metadataPrefix=oai_dc)、[Springer ECCV](https://link.springer.com/conference/eccv)、[ECVA 论文目录](https://www.ecva.net/papers.php)。表中计数取自本轮保存的响应快照，可能随官方更新变化。

## 5. ICML 既有数据复用与保守审计

只读原有 `Source_Registry.json` 指向的官方原始快照，验证字节哈希、大小、状态与年度后导入 worktree output，重新运行新 parser/对账器。没有直接信任旧 provisional CSV，也没有导入旧 scheduler 状态或资格标志。

最终代码离线重放结果如下，`Replay_Manifest.json` 记录零网络请求、3 次缓存命中及代码 SHA256：

| 年度 | Publisher / Metadata | 新审计未解决记录 | 状态 | 主要分类 |
| --- | --- | --- | --- | --- |
| ICML2025 | 3330 / 3330 | 1 | REVIEW_REQUIRED | EXACT 3249；NORMALIZED_MATCH 49；有稳定身份依据的 TITLE_VARIANT 32；重复 event 120；PROGRAM_ONLY 1 |
| ICML2026 | 6552 / 6552 | 103 | REVIEW_REQUIRED | EXACT 6199；NORMALIZED_MATCH 114；有稳定身份依据的 TITLE_VARIANT 216；重复 event 136；DUPLICATE_IDENTITY 46；PROGRAM_ONLY 57 |

原有 12/54 与新 1/103 使用不同的匹配、去重和问题记录口径，不能解释为“缺失论文从 12 减到 1”或“新增 49 篇缺失论文”。特别是冲突 program 身份不再简单按标题合并，同一问题可能形成多条差异记录。只有共享官方论文 URL/OpenReview 身份才允许解释真正标题变体；没有为追求零差异而放行冲突。

证据输出：`output\icml_existing_evidence_replay\ICML\2025` 与 `...\2026`。两年没有新 Formal Corpus、没有执行 V1.2。这是对已有数据的离线重放，不是全年网络抓取；Paper Library 原有文件未覆盖。

## 6. 各平台成熟度

| 平台 | 实现与成熟度结论 |
| --- | --- |
| ICML | 已具备受门槛保护的人工采集/审计入口；PMLR bulk 可复用，缺项可抓详情；program 独立核验、稳定 URL/OpenReview 身份、重复 event、变体和未对齐记录均处理。现有 2025/2026 数据仍需完整性问题复核，**不是年度 VERIFIED 成果**。2024 正式成果不重跑。 |
| ICLR | 已具备受门槛保护的人工入口；官方 proceedings 与 program 分离；年份、Conference track、publisher identity、重复 event 与标题/作者核验已接通，历史和真实少量详情通过。尚未宣称 2024/2025/2026 全部年度已 VERIFIED。 |
| AAAI | **NOT_READY_FOR_PRODUCTION**。已实现年度 issue 双目录核验、section 范围、OAI 独立文章枚举与 token 翻页、逐篇正式 DOI 证据及失败关闭。但本轮仅真实探测一个 issue/article 和 OAI 第一页，全年 OAI 覆盖、真实所有 section 边界尚未完成生产验收。 |
| ECCV | **NOT_READY_FOR_PRODUCTION**。已实现会议代表卷与声明计数、多卷发现、TOC 翻页、Part/Book DOI、逐章正式元数据、ECVA 独立目录核验；修复页 1 URL 别名、作者姓/名顺序与 ECVA 星号标记。全年真实多卷/分页仍未验收，且官方 2388/2387 差异需证据解释。不能以声明 count 代替枚举。 |
| TPAMI | **UNSUPPORTED / NOT_IMPLEMENTED**；没有 adapter、没有伪造支持。 |
| CVPR | 此新入口不支持，稳定 src 和 main.py 未改动，未重跑 CVPR2025/2026。 |

AAAI/ECCV 的代码不是虚假返回 VERIFIED 的占位 adapter：已有真实请求/解析/对账路径、离线端到端与小规模网络证据；不足之处是明确未完成的年度生产验收。依照本轮禁止全年抓取的边界，本报告不把它们升级为生产已认证。

## 7. 运行隔离与恢复

- 所有可变数据归属 `<LibraryRoot>/<Venue>/<Year>`。两个真实子进程分别持有 ICML2025、ICLR2025 锁的测试通过；第三个同年度进程被阻止。
- `O_CREAT|O_EXCL` 建锁；正常完成/异常/普通 Ctrl+C 由持有者 token 释放。stale lock 不会自动删除；人工核对 PID/host/start_time 后处理。锁竞争失败不写入持有者的报告文件。
- 单个命令用普通 for 循环依次执行用户提供年份。每年捕获异常保存证据并继续下一年；Ctrl+C 停止整个当前命令。
- HTTP 成功响应按 URL 缓存原始字节和 SHA256。所有 live 调用经 `response.raise_for_status()`，错误响应不进入成功缓存。连接失败和长 Retry-After 有日志。
- `--refresh-evidence` 仅由人工显式发起，刷新目录/独立证据并保留旧版本；详情缓存继续复用。没有自动重启、后台服务或项目级 state。
- Corpus 未 VERIFIED 时只有 provisional、Open_Issues、审计表和失败/复核状态。筛选器再独立检查状态、整数 unresolved_count=0、hash、计数、唯一身份、必需字段、Venue/Year 和冻结规则 hash。
- 筛选使用原 RuleEngine 的 Title + Abstract；七个 CSV 与 Run_Manifest 在年度临时目录全部成功后一次重命名。已有结果永不自动覆盖。筛选中断不破坏已经通过的 corpus gate，可人工重新执行继续完成筛选。
- 硬断电在 Formal CSV 与审计 JSON 两次发布之间的极小窗口可能留下孤立正式文件；后续安全拒绝，需人工核验，不能视作自动可恢复成功。

## 8. 规则、正式库与交付结论

冻结 V1.2 **未修改**，最终 SHA256：

```text
4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514
```

Paper Library **没有写入**。ICML2024、CVPR2025、CVPR2026 **没有修改或重抓**。没有 PDF 下载、人工二筛、TPAMI 实现、旧目录清理、任务计划、scheduler、heartbeat、merge 或 push。

统一入口和多终端隔离已具备人工启动条件；**四个平台整体尚不具备“全部已通过生产验收”的结论**。ICML/ICLR 可以由用户按手册发起受严格门槛保护的正式年度运行；AAAI/ECCV 保留 NOT_READY_FOR_PRODUCTION，需要后续人工批准范围内的年度验收及差异解释，不能承诺直接得到候选池。

运行手册绝对路径：`D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\MULTIVENUE_RUNBOOK.md`。

本轮开发和有限验证完成后停止；没有代用户开启正式批量运行。
