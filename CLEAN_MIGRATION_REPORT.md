# 人工执行代码环境迁移报告

日期：2026-10-04。阶段：仅代码提取及离线验收，已结束；没有开展论文抓取。

## 1. Worktree 与边界

- 新目录：`D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`。
- 从稳定仓库 `D:\_Knowledge Distillation\Paper search tool` 的当前 main 建立。
- 基线：`8148669c8cdea1525efc7b89fa04f8f92ae57a69`（8148669）。采用 detached HEAD，迁移文件保留在工作区，未提交。
- 使用 Git worktree 登记，因此 Git 必要的 worktree 管理元数据有新增；没有改动稳定仓库工作文件、main 指向或其原有 `diagnostics/` 未跟踪内容。
- 没有删除、移动旧文件，没有写入 Paper Library、旧开发目录、旧运行目录或 CVPR2025/CVPR2026 数据。
- 旧运行目录仅用于读取六份指定 HTML 快照，迁移后测试使用新 worktree 自带的副本。
- 没有新增、调用或修改 Codex automation、heartbeat 或旧调度任务；本轮是代码隔离，不包含对旧任务注册状态的停用/核验。

## 2. 先分析依赖，再提取

旧代码直接依赖关系：

```text
metadata_transport -> requests + Python 标准库
venue_adapters -> bs4 + 标准库 + src.official_sources（该导入未使用）
iclr_adapter / ojs_adapter / springer_adapter -> bs4 + 标准库
screen_verified -> allvenues_runtime.*
                -> run_pmlr.table -> qualify_pmlr -> 旧运行时
                -> src.screener / src.utils / pandas
旧 adapter 测试 -> 旧运行时路径中的 snapshots
旧 transport 测试 -> src.crawler.CachedHttpClient（其中一项 CVF 缓存回归）
```

新代码直接依赖关系：

```text
metadata_transport -> requests + 标准库
四类 adapter -> bs4 + 标准库（只解析传入内容，无网络或落盘行为）
screen_verified -> src.screener.RuleEngine / build_audit_sample
                -> src.utils / pandas + 标准库
tests -> 新 worktree 的 tests/fixtures 或合成数据 + pytest
```

`src.screener` 原有导入链仍会加载 `src.corpus_audit`、`src.paths`、`src.utils`，并间接加载 `src.crawler`、`src.official_sources`、`src.parser`。这些均来自稳定基线，非 Work 调度模块；本轮没有复制旧 Work 的 `src`，没有修改冻结引擎，也没有调用这些模块的抓取入口。`src.utils` 还需要 PyYAML、openpyxl；因此只安装 requests/bs4 并不足以加载筛选入口。

使用现有 `requirements.txt` 与本机 `D:\Anaconda\python.exe`（Python 3.13.5）；依赖已可导入，本轮未安装包或新建虚拟环境。Git worktree 隔离代码，但与稳定仓库共享 Git 对象库，也不代表独立 Python 虚拟环境。

## 3. 保留的文件与理由

| 文件 | 保留的能力及迁移调整 |
| --- | --- |
| `metadata_transport.py` | 有界 HTTP 重试、429/500/502/503/504、连接异常、Retry-After、失败历史。长等待抛出异常交由人工决定；修复畸形 Retry-After 回退，拒绝非正尝试次数。没有自动续跑。 |
| `venue_adapters.py` | ICML/PMLR 目录、详情、出版元数据规范化、年份/volume/官方域名范围检查；保留官方 program 的分页/范围分离辅助函数。移除未使用的 `src.official_sources` 导入。 |
| `iclr_adapter.py` | ICLR 官方 proceedings 年份、Conference track、publisher ID、目录计数、详情出版证据；解析主体保留。 |
| `ojs_adapter.py` | AAAI/OJS issue/article、章节范围、年份/卷期、DOI、摘要与作者证据；解析主体保留。 |
| `springer_adapter.py` | ECCV/Springer book/chapter、LNCS/Part、目录声明数量、分页链接、其他卷、出版日期与会议年分离；移除未使用变量/导入，并处理异常 HTML 父节点为空。 |
| `screen_verified.py` | 重写为单 Venue-Year 人工入口：显式 library root，校验后调用原冻结 RuleEngine，输出四类决定、候选池及 SAFE_DROP 抽样。默认不写盘，`--write` 才输出，已有 screening 目录则拒绝覆盖。 |
| `tests/test_allvenues_adapters.py` | 保留 PMLR 正例、错 scope、重复身份、缺摘要保留、program 分页和冻结引擎检查；文件名保留便于追溯，内容不再依赖全 Venue 运行时。 |
| `tests/test_iclr_adapter.py` | 保留真实历史快照、错误年份/轨道/身份、漏项/重复/空摘要及同标题不同身份测试。 |
| `tests/test_ojs_adapter.py` | 保留真实历史 issue/detail、非目标/不确定轨道、卷期/身份、分页与漏 section 测试。 |
| `tests/test_metadata_transport.py` | 保留五项独立传输测试；补充耗尽 HTTP 错误、畸形 Retry-After、HTTP 日期及零尝试测试。 |
| `tests/test_springer_adapter.py` | 新增合成 HTML 测试，覆盖 book/chapter、年份、分页暴露、父卷/重复身份及缺失证据。 |
| `tests/test_manual_screening.py` | 新增单 Venue-Year 门槛、哈希、无写入默认行为、局部输出、禁止覆盖、CVPR/TPAMI 拒绝及快照完整性测试。 |
| `tests/conftest.py` | 禁止单元测试 DNS/网络连接，避免意外真实抓取。 |
| `tests/fixtures/snapshots/*.html` | 六份只读历史输入：PMLR 2024 目录及首篇详情、ICLR 2024 目录及指定详情、AAAI 2026 issue 683/article 36958。总计 4,238,086 字节。 |
| `tests/fixtures/manifest.json` | 记录各快照 URL、相对文件名、字节数和 SHA256；不携带运行时状态。 |
| `.gitattributes` | 固定冻结 V1.2 YAML 为 LF，避免 Windows Git checkout 转成 CRLF 破坏字节哈希。 |
| `MANUAL_WORKFLOW.md` | 记录人工调用边界、审计输入契约及尚待实现的抓取/审计步骤。 |

## 4. 移除或隔离的调度依赖

以下“移除”仅指新代码不再依赖，并未删除旧环境文件：

- 移除 `allvenues_runtime` 通配符导入，以及通过 `run_pmlr.table` 间接引入的资格检查/运行时。
- 不再读取旧 `RUNTIME/runs/<venue>_production_qualification.json`；旧调度资格记录不能充当新环境审计证据。
- 不再使用旧 ROOT/RUNTIME 全局路径、`controller.lock`、全局锁、`runtime/state.json`、`venue_queue.csv`、`checkpoint.md`、heartbeats、scheduler、Codex automation 或自动续跑。
- 测试不再读取 `work_handoff_allvenues`；只使用随代码保存的固定快照。
- 新入口不信任审计中的旧绝对 `corpus_path`，不从另一个环境读取或输出数据。
- `get_metadata` 最终 HTTP 错误仍返回 response，保留原接口；未来抓取调用方必须调用 `raise_for_status()` 后才可将响应当作成功元数据，不能忽略 429/5xx。连接异常和长 Retry-After 直接抛出。

## 5. 未迁移的旧代码

没有迁移 `allvenues_runtime.py`、`run_pmlr.py`、`run_cvpr2024.py`、`scheduled_cvpr_audit.py`、`refresh_master.py`、`recon_ojs.py`、`recon_years.py`、`qualify_*.py`、`audit_iclr_index.py`、`finalize_*.py`、`resolve_cvpr2024.py`、`record_sample_review.py`，也没有迁移旧 Work 的 `src` 修改、调度状态/锁/队列/心跳/资格结果/自动化脚本或全局输出表。

原因：这些入口绑定旧路径、多年度循环、抓取落盘、旧资格流程或人工复核状态，不适合直接复制到新人工流程。未来需要按一次一个 Venue-Year 重写抓取及完整性审计编排。本轮并未声称这些功能已经完成。

旧 transport 测试中的 `test_cvf_failed_request_not_cached_and_success_resumes` 未迁移：它验证旧 CVF crawler 行为，超出四类 adapter 和独立传输提取范围，且不应为使其通过而带入旧 `src.crawler` 修改。稳定基线原有文件和测试继续留在 worktree，未删除、未运行全量测试。

## 6. 冻结筛选规则与新门槛

文件：`config/screening_rules_v1_2.yaml`。

```text
SHA256 = 4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514
```

稳定仓库、旧 Work 规则及 Git `8148669` 原始 blob 均为该值。新 worktree 首次 checkout 被 Git 转为 CRLF，测试正确报错；随后直接恢复该提交原始 3,562 字节，并添加 LF 属性。最终哈希完全一致，`git diff 8148669 -- config/screening_rules_v1_2.yaml src` 无差异。

筛选前要求：同一 Venue-Year、`VERIFIED`、显式 `unresolved_count=0`、强制 `corpus_sha256` 匹配、publisher_count 匹配、非空语料、唯一 Paper_ID/Official_URL、必需元数据非空以及规则 SHA256 匹配。全部使用显式异常，不能被 `python -O` 绕过。候选池仅包括 KEEP/MAYBE/AMBIGUOUS，标记待人工二次复核；不会把规则保留自动视作人工确认。

该入口消费审计结果，不负责证明审计本身正确。尚未实现的新人工全年审计器必须先独立建立完整性证据，不能人工伪造 VERIFIED 以绕过审计。

## 7. 离线验收结果

最终结果：**76 passed in 10.45s**。

执行环境变量：`PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=output/manual_test_run2 tests/test_allvenues_adapters.py tests/test_iclr_adapter.py tests/test_metadata_transport.py tests/test_ojs_adapter.py tests/test_springer_adapter.py tests/test_manual_screening.py
```

所有写入型筛选测试使用新 worktree 下 `output/manual_test_run2` 的合成数据；未将 Paper Library 作为测试路径。网络连接由 autouse fixture 禁止。测试没有运行任何生产抓取、PDF 下载或全年审计。

首次测试的失败项为 checkout 换行哈希、Springer 缺父节点以及测试临时父目录不存在；均已修复并对同一有限测试集重跑。源代码静态搜索未发现旧调度路径/模块依赖，`git diff --check` 通过。未将离线通过解释为网站当前可访问或所有年度可生产执行。

## 8. Adapter 成熟度

| Adapter | 本轮已验证 | 当前限制与成熟度 |
| --- | --- | --- |
| ICML/PMLR | 2024 历史目录/详情正例，年份/volume/域名范围，重复身份，program 分页；元数据规范化 | 四者中最适合先接人工流程；仍缺独立的新单年度抓取/审计入口和 metadata/index/program 全量对账，不宣称全年 VERIFIED。 |
| ICLR | 2024 历史目录/详情及计数、轨道、身份、缺项负例 | 解析级可用；页面结构依赖较强，其他年份与新的独立完整性对账尚待验收。 |
| AAAI/OJS | 2026 一个 issue/detail 的历史快照与 section/DOI/卷期负例 | issue/article 解析级可用；全年的 issue 枚举、issue 间去重、独立目录对账未建立；未知 section 保持 UNCERTAIN。 |
| ECCV/Springer | 本轮合成 book/chapter 契约测试，分页暴露、会议年与出版年分离 | 初步解析；本轮没有使用 Springer 实际快照验收，没有多卷/多页全年汇总与独立完整性对账。Declared_Chapter_Count 不代表当前页已收齐。 |
| TPAMI | 无 | **尚未实现**。新入口拒绝 TPAMI，不能从基线中的 IEEE 辅助函数推断存在 TPAMI adapter。 |

## 9. 推荐下一步（本轮不执行）

建议先处理 **ICML 2024 / PMLR v235**：已有目录、详情、结构化元数据解析和官方 program 辅助逻辑，单 volume 的范围较清晰，适合建立一次一个 Venue-Year 的人工流程。

下一轮先实现独立的 ICML2024 抓取/完整性审计入口，并在沙盒验证目录、元数据与独立 program 三方对账；只有未来经人工发起的正式运行才能写入 `D:\_Knowledge Distillation\Paper Library\ICML\2024`，且先处理已有文件的保留策略。其后以 VERIFIED 语料调用冻结 V1.2。不可因本报告推荐而自动开跑。

本轮到此停止。
