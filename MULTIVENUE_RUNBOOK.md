# 多 Venue 人工运行手册

更新：2026-10-05（Asia/Shanghai）。开发分支：`manual-multivenue-next`。

代码目录：`D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`。
数据根目录：`D:\_Knowledge Distillation\Paper Library`。

本轮真实全年验收仅写入开发 worktree 的 `output/production_acceptance`。下面的正式库命令没有被执行。不要到稳定仓库目录运行新入口，不要对正在运行的 ICML/ICLR 年度重复启动任务。

## 1. 当前可用程度

ICML、ICLR 保持既有生产实现；本手册不据历史验收数字推断它们当前生产任务的状态。

AAAI：**PRODUCTION_READY**。2024/2025/2026 分别 2517/3182/4417 篇，年度 OAI ListSets 全链与出版社逐项核验，三年均 VERIFIED 且已完成正式 V1.2。见 `AAAI_ACCEPTANCE.md`，不要重复已完成的开发验收。

ECCV：完整性差异仍需复核，不能把有可靠元数据等同于 VERIFIED。2024 的 2388 个出版社条目包含两个有原论文链接的更正通知；剩余差异必须逐项查看，不能仅凭数量闭合。

TPAMI：已有 IEEE adapter 和年度 pipeline，但公开官方全集来源尚未完成验收，**NOT_READY**。CVPR 在此入口明确拒绝，原有生产代码未改动。

## 2. 四个独立终端

以下每条命令均可直接用于 CMD 或 PowerShell，使用已验证的 `D:\Anaconda\python.exe`。无需激活旧 Work 环境。各终端只运行其中一条，不会启动子服务。

终端 1，ICML 2025 → 2026：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\run_venue.py" --venue ICML --years 2025 2026 --library-root "D:\_Knowledge Distillation\Paper Library"
```

终端 2，ICLR 2024 → 2025 → 2026：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\run_venue.py" --venue ICLR --years 2024 2025 2026 --library-root "D:\_Knowledge Distillation\Paper Library"
```

终端 3，AAAI 2024 → 2025 → 2026：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\run_venue.py" --venue AAAI --years 2024 2025 2026 --library-root "D:\_Knowledge Distillation\Paper Library"
```

终端 4，ECCV 2024 → 2026（尚待生产验收，不接受 2025）：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\run_venue.py" --venue ECCV --years 2024 2026 --library-root "D:\_Knowledge Distillation\Paper Library"
```

这些命令会在你人工启动后写入正式库。开发验收未代为执行。若先在沙盒验收，只将 `--library-root` 改为当前 worktree 的 `output\manual_acceptance` 绝对路径；这仍可能启动全年抓取，应由你决定何时执行。

年份严格按参数顺序执行，不排序、不推断缺失年份、不并发同一命令内的年份。某年失败或 REVIEW_REQUIRED 后继续后面的指定年份。Ctrl+C 则停止整个当前命令，不继续剩余年份。全部完成退出码 0；存在失败/复核年度退出码 2；主动中断 130。

## 3. 写入位置与进度

每一年度独占 `<LibraryRoot>\<Venue>\<Year>\`：

```text
raw/
  Publisher_Index.csv
  Official_Program.csv                  # AAAI 为独立 OAI 枚举，ECCV 为 ECVA 目录
  Non_Target_Records.csv
  PROVISIONAL_Formal_Proceedings_Corpus.csv
  Corpus_Completeness_Audit.csv
  Open_Issues.csv
  Audit_Summary.json
  Evidence_Manifest.json
  Formal_Proceedings_Corpus.csv          # 只有 VERIFIED 才发布
  Issue_Enumeration.json                # AAAI
  Volume_Enumeration.json               # ECCV
runtime/
  run.lock
  Progress.json
  Fetch_Manifest.json
  cache/                                # 每个 URL 的原始字节、来源、SHA256
  cache/history/                        # 显式刷新前的旧证据
  screening-<id>.partial/                # 筛选未完成时可能保留
reports/<run_id>.json
logs/<id>-request-error.json
logs/<run_id>-error.json
screening/                              # 整组筛选成功后一次发布
```

终端会打印年度 START、真实请求 URL、每 25 篇详情进度和最终状态。可用编辑器只读查看年度 `runtime\Progress.json`，以及 `reports`、`logs`。不会写共享 latest.log、全局 state、队列、CSV 或总锁。

普通中间表与缓存使用同目录临时文件和原子替换。Formal Corpus 与审计 JSON 是两个文件：若硬断电恰好发生在两次发布之间，会留下无法通过门槛的孤立 Formal 文件，程序拒绝覆盖，须人工核验；不会将其当成功。

## 4. 停止、恢复、刷新

按当前终端 Ctrl+C。正常中断会记录状态、保留缓存并释放该年度锁；不会留下后台自动续跑服务。再次执行同一条命令即可复用成功的 HTTP 快照，逐个验证 URL、Venue、Year、HTTP 200 与 SHA256。失败 HTTP 响应不进入成功缓存。

ICML 2025/2026 若原年度 `raw\Source_Registry.json` 及其快照仍在，会先只读校验并导入指定 PMLR index、citeproc 元数据和官方 program 快照。核验包括原始字节哈希、大小、HTTP 状态、年度、URL 和后续 parser scope。旧 provisional CSV、旧 VERIFIED 标记、旧调度状态均不是信任依据。源快照缺失时改抓官方源；损坏则报错保留证据。此步骤不加载任何旧运行时代码。

默认复用既有证据快照，因此反复运行相同快照不会自行消除真实差异。需要重新检查官方目录更新、修复后的 program 或已失效 OAI 分页 token 时，显式加 `--refresh-evidence`：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\run_venue.py" --venue ICML --years 2025 2026 --library-root "D:\_Knowledge Distillation\Paper Library" --refresh-evidence
```

刷新目录、批量元数据、program、OAI、book TOC 等证据，保留旧快照历史；已经成功的论文详情继续复用。不会覆盖已有正式 Corpus 或 screening。缓存损坏/需要刷新某篇详情时，应先确认该年度没有运行进程，再人工将对应 `.body`、`.json` 移到该年度的隔离目录，重跑后重新抓取；不要修改其中的哈希来绕过校验。

遇到 `RetryDeferred` 表示服务器要求等待超过 60 秒，当前年度停止该请求并报错。由人工决定何时重跑；没有 daemon 或自动计划任务。默认单进程串行请求，两次网络请求之间保守间隔 2 秒，重试继续由 `metadata_transport.py` 负责。

## 5. 锁冲突

每个年度只有一个 `runtime\run.lock`，包含 PID、hostname、start_time、venue、year、随机所有权 token。第二个进程遇到同年度任何既存锁都拒绝写入，不会用 PID 猜测后静默删锁。

示例只读检查：

```powershell
Get-Content "D:\_Knowledge Distillation\Paper Library\ICLR\2024\runtime\run.lock"
Get-Process -Id <锁中PID> -ErrorAction SilentlyContinue
```

强制杀进程/断电后可能留下 stale lock。必须人工核对主机、PID、启动时间，排除另一终端仍在运行及 PID 重用后，才可人工将锁移到该年度 reports 中留证并重新运行。不提供自动删锁/强制接管。不要移动正在使用的年度目录或缓存。

## 6. 判断审计和筛选是否完成

只有本年度 `raw\Audit_Summary.json` 为 `status=VERIFIED`、整数 `unresolved_count=0`，且同目录 `Formal_Proceedings_Corpus.csv` 的 SHA256、publisher_count、唯一 Paper_ID、唯一 Official_URL、完整必需字段、Venue/Year 均通过校验，才能调用冻结 V1.2。`PDF_URL` 可以为空。

`INDEX_ONLY`、`PROGRAM_ONLY`、冲突身份、元数据不足和无法证明的变体都留在 Open_Issues。只有共享稳定论文身份才能自动解释真正的 TITLE_VARIANT；仅靠唯一标题匹配还必须有匹配的完整作者列表。只合并身份、标题、作者均一致的 conference program 重复 event；OAI/ECVA 重复身份不当作 event 消除。差异记录数不代表缺失论文数。

`REVIEW_REQUIRED` / `FAILED` 不会产生新的正式筛选；查看该年度 `Open_Issues.csv`、对账表、错误报告和源快照。不要手工把状态改成 VERIFIED 或把 unresolved_count 清零。

V1.2 整组完成后，`screening\Run_Manifest.json` 为 `SCREENED_CSV_READY`，记录 corpus/rules hash、候选计数及每个输出 CSV 的哈希。重新执行会验证已有输出，完整一致则报告 ALREADY_COMPLETE；不完整、旧格式无法验证或被修改的 screening 一律保留并报错，不覆盖。

```text
screening/KD_Screening.csv
screening/RULE_KEEP.csv
screening/RULE_MAYBE.csv
screening/RULE_AMBIGUOUS.csv
screening/SAFE_DROP.csv
screening/Needs_Secondary_Review.csv
screening/SAFE_DROP_Audit_Sample.csv
screening/Run_Manifest.json
```

候选池位置例：`D:\_Knowledge Distillation\Paper Library\ICLR\2025\screening\Needs_Secondary_Review.csv`。
内容是 KEEP + MAYBE + AMBIGUOUS，状态为待人工二次复核，不是最终确认的 KD 论文。

## 7. 保护与限制

- 正式根目录的 ICML2024 被入口直接保护，连锁/日志都不写入；可在 worktree output 的合成库测试同年接口。
- CVPR2025/2026 不在新入口支持范围。TPAMI 有实验入口，但未获生产认证。
- 冻结 YAML SHA256：`4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514`。
- 缺少官方正式出版、无法访问或年份尚未公开时失败关闭，不以录用名单或声明数量冒充正式全集。
- AAAI 纳入 Technical Track，以及可明确识别的 Social Impact / Alignment 正式 Special Track；其他特殊名称为 UNCERTAIN 需复核。排除附属活动、学生摘要、workshop 等已识别非目标章节。
- 本轮进行了开发目录中的全年验收，没有下载 PDF、人工二筛或修改正式库。

## 8. 离线测试与有限烟测

在当前 worktree 执行下列离线测试。每次使用新的 basetemp 名称，保留已有测试输出。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
D:\Anaconda\python.exe -m pytest -q -p no:cacheprovider --basetemp=output/manual_tests_next tests/test_allvenues_adapters.py tests/test_iclr_adapter.py tests/test_metadata_transport.py tests/test_ojs_adapter.py tests/test_springer_adapter.py tests/test_manual_screening.py tests/test_venue_pipeline.py tests/test_pipeline_enumeration.py
```

测试禁止 DNS/网络，实际测试数量以本轮报告为准。可人工执行 `D:\Anaconda\python.exe -X utf8 -B smoke_venues.py` 做有限烟测：目录固定为 worktree `output\smoke`，每平台 1 篇详情、最多 6 个新 URL 请求（每请求最多 3 次有界 HTTP 尝试），不运行年度 pipeline。已有快照会被复用，`requests=0` 表示缓存复核，不是再次联网成功；原始请求证据在 Fetch_Manifest 中。

## 9. 显式 provisional 一筛

正式 `screen_verified.py` Gate 不变。仅当年度真实状态是 REVIEW_REQUIRED、没有 Formal Corpus，而且 PROVISIONAL 文件具有可验证的唯一身份和可靠字段时，人工显式运行辅助入口：

```text
D:\Anaconda\python.exe -X utf8 -B "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\screen_provisional.py" ECCV 2024 --library-root "D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004\output\production_acceptance\attempts\eccv_recovery_02" --write
```

去掉 `--write` 只验证和计算，不发布文件。输出放在该年度独立的 `screening_provisional`，不会冒充正式 `screening`。Manifest 明确记录 `provisional_screening=true`、真实 `corpus_audit_status`、`unresolved_count`、输入和输出哈希。各 CSV 也带有 provisional 标记。

候选池为 `screening_provisional/Needs_Secondary_Review.csv`，仍仅是 KEEP + MAYBE + AMBIGUOUS 待人工复核集合。缺少 Abstract 等必需元数据的记录保留在 `Metadata_Excluded_From_Screening.csv`，不编造内容、不参与一筛。重复身份或范围错误拒绝整次发布。已有输出不覆盖。

`accept_production.py` 的新 attempt 可只读引用旧 acceptance 快照，每次使用都重新校验字节哈希，并在自己的 registry 和 Fetch_Manifest 记录来源；新请求仍由原有 FetchCache 保存。正式生产 FetchCache 和锁机制未改变。
