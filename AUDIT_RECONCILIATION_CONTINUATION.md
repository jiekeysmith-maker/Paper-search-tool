# 正式语料身份对账开发交接（2026-10-07）

## 边界与 Git 状态

- 开发 worktree：`D:\_Knowledge Distillation\worktrees\audit_reconcile_20261007`
- 分支：`audit-reconcile-20261007`
- 起始提交及当前 HEAD：`91c8c8e89819818ac0cc5ca6925c2676e7285252`
- 本轮未创建 commit，未 push、merge、rebase、force push。修改留在本 worktree 供人工审查、提交。
- **Paper Library 没有被本轮开发修改。** 没有运行正式生产任务，没有修改任何正式 Audit、Corpus、Research Map、筛选结果或 PDF。
- 没有修改 ECCV、TPAMI adapter 或其生产数据；没有修改其他 continuation 文件。
- 测试输出位于系统临时目录。真实 ICML/ICLR 文件只用于读取源快照和内存诊断，未调用生产 audit 写入。

开始时执行了 `git worktree list`、`git status --short --branch`、`git log --oneline -10`，并检查其余 worktree 的 status/log：

| Worktree | 分支 / 起点 | 开始时状态 |
|---|---|---|
| Paper search tool | main / 69c3ec5 | 干净，ahead 1 |
| eccv_production_20261006 | detached / f82e59a | 干净 |
| kd_allvenues_20261003 | work/kd-allvenues / 8148669 | 有修改和未跟踪文件，包括其自身 rules 文件；未触碰 |
| kd_manual_clean_20261004 | manual-multivenue-next / 91c8c8e | ahead 4；4 个 TPAMI 相关文件有未提交修改；未带入 |

独立 worktree 从最后一项的已提交 HEAD 创建。首次沙箱创建在写分支后无法写 Git worktree 元数据，经权限升级完成。另一个失败尝试产生的占位分支指向 main，已验证并安全删除；未删除任何原有分支。

## 已确认根因与修复

### Program event 与 submission

旧 `reconcile` 只有 identity、title、authors 全相同时才压缩 event。同 ID 改名或表示变化会产生多个独立候选，并反向制造 publisher 歧义。

新增 `submission_identity.py`，沿用现有字典行模型：

1. 解析可信 OpenReview `/forum?id=...`，忽略 query 顺序、附加参数、fragment 和 HTTP/HTTPS 表示差异；保留 ID 大小写。
2. 空 ID、group URL、外部域名和重复 id 参数不构成强键。
3. 官方 program 生成的 `YEAR-Oral/Poster/Spotlight-...` 占位链接不作为真实 forum ID；保留到事件证据字段。
4. 按强身份键建立连通分量，保留每个原始 event、标题、作者、来源组和证据 URL。
5. 额外使用官方 JSON 的 `related_events_ids`：必须同一来源组、关联目标唯一、标题一致、作者兼容、无 stable ID 冲突，才归并；不根据标题单独压缩 event。
6. 聚合后的 submission 与 publisher 对账；所有 observed titles 都参与唯一标题候选查找。共享强键时标题版本差异记为 TITLE_VARIANT。
7. 同 publisher 强键指向多条出版记录、不同 forum ID、同 submission 声称多个 publisher URL、完全不相容作者等继续未决。
8. Program-only 输出保留 forum URL 和完整 event 观察。多 event 不增加 independent paper count。

特别注意：旧 Official_Program.csv 缺失 related-events 字段。后续必须重新解析缓存 JSON 或刷新官方 JSON；只用旧 CSV 再跑 reconcile 不能恢复丢失的关系。

### 作者证据

- 标题匹配无共享强 ID：HTML entities（有界重复解码）、Unicode NFKD、casefold、组合 accents、空白、姓名内部 apostrophe、标点和分隔符规范化。
- 分号保留作者边界；逗号列表兼容 publisher 展示形式；明确分号边界内支持 family/given 顺序变化。
- 保留完整作者数量，要求唯一一一对应。支持明确 initials 和省略中间 initials，不进行任意昵称或姓氏模糊匹配。
- 不自动去重作者、不把完全不同作者的同名论文合并；歧义 initials 仍未决。
- 已共享可信 forum ID：作者覆盖差异、额外中间名/昵称、姓名空格和部分 Latin 字母表示差异可作为非阻断的观察差异，原始双方作者保留在审计行。全无可解释姓名对应时仍未决。
- ICLR detail 优先读取逐条 `citation_author`，把显式 `family, given` 转为完整姓名；保留 `Authors_As_Displayed`。真实缓存证明展示模板可能把重复 given/family 词缩短，结构化 metadata 则保留完整姓名。
- ICLR publisher index 的对账作者由同 URL detail 的结构化作者补充，保留 `Index_Authors_As_Displayed` 和 `Author_Evidence_URL`。未推断缺失 forum ID。

ICLR 缓存还包含确实不同的作者、重复作者和昵称差异；不能把人工“约若干格式问题”的估计当成自动消除全部未决的理由。

### PMLR 枚举

旧 parser 只接受 `div.paper`，未检查容器之外的遗漏 detail 链接，也未枚举分页；bulk metadata 重复 URL 会覆盖前一行，citeproc-only 记录只报差异而不尝试 detail 核验。

现在：

- 解析所有 `.paper` 元素，不依赖 div 标签；要求标题、作者和唯一 detail link。
- 容器外的论文链接、缺字段、重复 URL、不明分页结构均失败并留下 issue；不静默跳过。
- 遍历显式分页，限制在同官方 volume，检查 next 自环/回环和页数上限；跨页重复 URL 产生未决 issue。
- publication URL 规范化为官方 HTTPS 路径，去除 query/fragment；不用规范化 title 作为去重键。
- citeproc 逐条解析，异常带条目序号进入 issues；重复 identity 从 lookup 中移除，禁止最后一条覆盖胜出。
- citeproc-only 是候选发现来源。只有官方 detail 的年份、volume、标题及作者核验成功才补入 publisher enumeration，并记录来源与验证方式。
- 没有通过 program acceptance 自动加入 formal corpus。publisher/program 真正缺口仍未决。
- `evidence_complete` 在已有 acquisition issues 时为 false；audit 的 issues gate 仍独立阻断 VERIFIED。
- `FetchCache.get(..., evidence=True)` 使 HTML 命名的 index units 也服从显式 refresh-evidence，不误当不可刷新的 detail cache。

**PhySpec 的具体根因仍有来源证据缺口：**只读检查本地 v267 HTML 与 citeproc 快照，两者均 3330 条且无该标题；一次 fresh 官方 v267 HTML GET 也有 3330 个 paper unit，未找到该标题。Program 中只有 forum 链接，无 publisher URL。没有证据证明该论文被当前 parser 从这个 HTML 跳过。本轮未获得其具体正式 PMLR detail URL，因此不能保证重跑就会收录它；需要人工提供或后续来源发现该正式页面，按通用证据路径闭环。没有写入任何论文特例或人工总数。

### Track 与 CVPR

- ICML 已有 Conference + Position_Paper_Track accepted-group 规则继续保留；新增测试保护 Position Paper 有 publisher 对应可确认，无对应仍 PROGRAM_ONLY / REVIEW_REQUIRED。
- track 名称本身不产生正式发表证据。其他已有 venue scope 规则未放宽。
- CVPR production code 未改。`CachedHttpClient` 在 HTTP 成功后才写 cache；404 不缓存。direct-resolution 每次重新核验，已有 `force` 可刷新成功缓存，listing refresh 可发现并抓取新条目。
- 新增 CVPR 404 → 后续正式 detail 出现的离线测试，确认不会永久锁死；已有 listing refresh、直接核验和保守审计测试通过。
- CVPR2026 的真实 accepted-only 差异继续交给正式证据重跑，不硬改 NOT_FORMALLY_PUBLISHED 或正式收录。

## 修改文件清单

| 文件 | 作用 |
|---|---|
| submission_identity.py | 强身份键、事件聚合、作者兼容与冲突建模 |
| venue_audit.py | submission-level reconciliation、observed title、重复 publisher 防护、证据输出 |
| venue_pipelines.py | 保留 related event、排除 event 占位 ID；PMLR 分页/bulk/detail 证据链；ICLR 结构化作者 |
| venue_adapters.py | PMLR parser 完整性、URL 与分页检查 |
| venue_runtime.py | 显式 index evidence cache refresh |
| iclr_adapter.py | 完整 citation_author 与展示作者分别保留 |
| tests/test_audit_reconciliation.py | 身份、改名、冲突、作者、track、event 关系、真实 absence、无 fuzzy 自动确认 |
| tests/test_pmlr_enumeration.py | 边界容器、分页、异常、碰撞、detail 补充、fresh/cache 等价 |
| tests/test_iclr_adapter.py | 重复 given/family name 的结构化恢复 |
| tests/test_corpus_audit.py | CVPR candidate 404 后恢复 |
| AUDIT_RECONCILIATION_CONTINUATION.md | 本交接文件 |

没有修改或新增正式来源 fixture；新增测试使用 synthetic inline fixtures，不含人工目标论文名单。

## 测试与验证记录

所有 pytest 用 `python -B -m pytest ... -q -p no:cacheprovider --basetemp="$env:TEMP\唯一目录"` 执行。已有 autouse fixture 禁止真实网络。

已执行命令组（当前 worktree）：

```powershell
python -B -m pytest tests/test_venue_pipeline.py tests/test_pipeline_enumeration.py tests/test_allvenues_adapters.py -q -p no:cacheprovider --basetemp='D:\_Knowledge Distillation\worktrees\audit_reconcile_20261007\.test-tmp\baseline'
python -B -m pytest tests/test_audit_reconciliation.py tests/test_venue_pipeline.py tests/test_pipeline_enumeration.py tests/test_allvenues_adapters.py -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-targeted-1"
python -B -m pytest tests/test_pmlr_enumeration.py -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-pmlr-1"
python -B -m pytest tests/test_audit_reconciliation.py tests/test_pmlr_enumeration.py tests/test_venue_pipeline.py tests/test_pipeline_enumeration.py tests/test_allvenues_adapters.py -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-targeted-2"
python -B -m pytest tests -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-full-1"
python -B -m pytest tests -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-full-2"
python -B -m pytest tests/test_audit_reconciliation.py tests/test_pmlr_enumeration.py tests/test_iclr_adapter.py tests/test_allvenues_adapters.py tests/test_pipeline_enumeration.py tests/test_venue_pipeline.py tests/test_corpus_audit.py tests/test_refresh_listing.py tests/test_eccv_identity.py tests/test_eccv_evidence.py tests/test_eccv_recovery.py tests/test_next_aaai.py tests/test_aaai_oai_sets.py tests/test_ojs_adapter.py tests/test_springer_adapter.py tests/test_tpami.py tests/test_tpami_csdl_detail.py tests/test_tpami_rendered.py tests/test_tpami_recovery.py tests/test_tpami_public_metadata.py tests/test_tpami_public_pages.py -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-final-1"
python -B -m pytest tests -q -p no:cacheprovider --basetemp="$env:TEMP\kd-audit-reconcile-final-full"
git diff --check
Get-FileHash config/screening_rules_v1_2.yaml -Algorithm SHA256
```

首轮测试临时目录父目录缺失：17 passed / 40 setup errors，随后更换系统临时目录。其后 targeted 82 passed、PMLR 14 passed、combined 96 passed；相关 venue/audit/parser/identity 回归套件 355 passed。

全套第一次 479 passed / 1 failed，第二次 486 passed / 1 failed；**最终代码全套 488 passed / 1 failed（27.94 秒）**。全部新增测试及相关 venue/audit/identity/parser 测试通过；唯一失败如下。

唯一全套既有失败：`tests/test_next_real_structures.py::test_real_fixtures_integrity`。未改文件 `tests/fixtures/next/eccv_part_xviii.html`：

- 工作树 bytes 和起始 HEAD Git blob SHA256 相同：`83681a7ce3a0d57a786c7eedbeff05375842d2bb47053a49b04fdbe72521951b`
- provenance 期待：`6ba2df141f8b1239159b118064193b9853fa3b223e590aa4aec92caf796f809a`
- 文件无 CRLF，不是本轮 checkout 换行问题。未更新哈希、未忽略该测试、未改 ECCV fixture；需要另行核实其原始 provenance。

KD V1.2 文件未改，SHA256 复核：
`4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514`

## 网络与只读诊断

- 一次 web 搜索 PMLR 上 PhySpec：无结果。
- 首次 Python HTTPS GET 被沙箱 socket 权限阻止；允许后一次 GET `https://proceedings.mlr.press/v267/` 成功。仅内存解析，不写正式 cache，不下载 PDF。
- 其他诊断为读取现有 ICML/ICLR CSV/JSON/HTML cache，并在内存调用 parser/reconcile。未运行生产 audit、screen 或真实 TPAMI acceptance。
- 中间版本 cache reparse 显示 ICML2026 PROGRAM_ONLY 2、ICLR2025 5、ICLR2026 2；它们不是正式新 Audit，也不是代码中的常量。作者未决仍存在，且后续又补充了 entity 与 citation-author 修复，不能把中间诊断当作最终验收数字。

## 风险、未执行事项与人工下一步

1. 人工审查全部 diff（包括新文件），提交并 push，再由 ChatGPT 审查实际提交 diff。本轮不提交或推送。
2. 先在新的 isolated acceptance 输出目录，用重新解析的缓存 source JSON 验证 event 关系和 citation 作者；不要只重用旧 Official_Program.csv / Publisher_Index.csv。
3. 人工刷新 PMLR index/citeproc/program 并检查 issues。PhySpec 需要找到可核验的具体 publisher 页面；如果两种 enumeration 都没有它，应继续真实未决，不能按已知数量补齐。
4. ICLR 未有共享 forum ID 的记录只能依赖唯一 title + 保守完整作者兼容。真实作者替换、重复作者、昵称、无法判定的表示差异继续人工核验。
5. 最终人工安排正式 ICML/ICLR 重跑；不要与 ECCV/TPAMI 活跃生产目录并发写入。新 worktree 不修改原任务锁。
6. `run_venue.py --venue ICML --years 2025 2026 --library-root <新的隔离目录> --refresh-evidence`，ICLR 同理使用 2024/2025/2026。这是后续人工命令模板，本轮未执行。已有 formal/screening 的目录会触发既有保护/ALREADY_COMPLETE，不要删除 gate 来强行覆盖。
7. CVPR 后续人工使用既有 listing refresh 与 audit force/direct-resolution 流程。新增正式证据前保持 REVIEW_REQUIRED；不改当前 27 条结论。
8. 单独核实既有 ECCV fixture/provenance 不一致，不在本修复中改变真实性校验基准。

完成判断应看通用数据模型、证据闭合与自动化保护，不看各年度是否全部 VERIFIED。
