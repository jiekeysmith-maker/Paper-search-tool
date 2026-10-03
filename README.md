# 论文搜索工具（Paper Search Tool）V1

这是一个面向科研文献初筛的本地 Python 工具。当前V1.2程序先建立并核验“该Venue-Year最终正式发表的Main Conference Proceedings论文全集”，再使用完全确定性的 Python 规则进行 Knowledge Distillation（KD）高召回初筛，并管理二次筛选后的官方 PDF。

程序工程与论文数据严格分离：

- `D:\_Knowledge Distillation\Paper search tool`：程序源码、配置和测试。
- `D:\_Knowledge Distillation\Paper Library`：默认论文库根目录，保存论文数据与检索成果。

本轮默认正式检索范围仅为 **CVPR Main Conference**。Findings 适配代码完整保留，但在 `config/source_config.yaml` 中默认关闭，只作为以后可选的补充池；启用时必须依据 `Track（论文轨道）` 单独统计和复核，不与 Main Conference 核心统计混算。Workshop 同样默认关闭。

本工具不调用 OpenAI API、其他 LLM API 或本地大语言模型；也不进行 P0/P1/P2/P3、Related Work、Research Map 或 Research Gap 判断。

## 环境与安装（Windows CMD）

推荐 Python 3.10+。在项目根目录运行：

```bat
cd /d "D:\_Knowledge Distillation\Paper search tool"
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 默认论文库结构

```text
D:\_Knowledge Distillation\Paper Library\
├── CVPR\
│   ├── 2024\
│   ├── 2025\
│   └── 2026\
├── AAAI\
│   ├── 2024\
│   ├── 2025\
│   └── 2026\
├── ICLR\
├── ICML\
├── ECCV\
└── TPAMI\
```

每个新Venue-Year使用`<Paper Library>\<Venue>\<Year>`，例如`D:\_Knowledge Distillation\Paper Library\CVPR\2025`。内部目录按实际阶段创建：

```text
<Year>\
├── raw\
├── screening\
├── secondary_screening\
├── PDFs\
├── assignments\
├── manifests\
├── reports\
└── logs\
```

不传参数时，`--library-root`默认为`D:\_Knowledge Distillation\Paper Library`。测试或临时运行可显式覆盖：

```bat
python main.py report --venue CVPR --year 2025 --library-root "D:\Temp\Paper Library"
```

## 推荐正式流程

1. `crawl`：保存CVF Open Access原始抓取事实
2. `audit-corpus`：用其他官方来源做Formal Proceedings完整性核验
3. 仅当Formal Proceedings Corpus为`VERIFIED`时执行`screen`（默认冻结的V1.2规则）
4. 主题总控对RULE_KEEP、RULE_MAYBE、RULE_AMBIGUOUS做Title+Abstract二筛：KD Method-Centric / Other KD / Exclude
5. 主题总控生成权威KD REVIEW Assignment CSV
6. `validate-assignment`
7. `download-secondary`直接下载到Secondary Class / Assistant最终目录
8. 五个论文助手全文阅读；允许KD Method-Centric与Other KD相互修正
9. 大量修正时使用`apply-reclassification`，少量修正可人工移动
10. 主题总控生成一份最终Research Map Excel；`report`快速检查状态与异常

## 命令

小样本抓取（默认仅 Main Conference）：

```bat
python main.py crawl --venue CVPR --year 2026 --limit 20
```

正式全量抓取（默认仅 Main Conference；只建立原始论文池，不下载 PDF）：

```bat
python main.py crawl --venue CVPR --year 2026
```

如果官方Proceedings列表在首次抓取后补充或更新，只刷新CVF listing：

```bat
python main.py crawl --venue CVPR --year 2026 --refresh-listing
```

`--refresh-listing`会强制重新请求所有已启用轨道的listing，但不会重新请求本地已有的`SUCCESS`论文详情；它只抓新发现或此前`FAILED/PARTIAL`的详情，保留listing中暂时缺失的旧成功记录，并将本次新增项写入`raw\CVF_Listing_New_Entries.csv`。默认正式范围仍只有CVPR Main Conference，Findings和Workshop不会因此启用。

CVPR Main Conference语料不再假定`?day=all`能够完整枚举Proceedings。Crawler会从官方页面动态发现当前年份的具体日期页，并以`day=all ∪ all discovered daily pages`构造listing，按`Official_URL`去重。日期页失败、日期发现失败或“相同标题但不同Official URL”都会进入抓取异常队列；程序不会硬编码会议日期、论文总数或预期差额。

抓取后先执行正式Proceedings完整性审计：

```bat
python main.py audit-corpus --venue CVPR --year 2026
```

CVPR审计优先读取官方`AcceptedPapers`页面；该路由不存在时读取同一官方站点的Main Conference program，并明确排除Findings。Accepted/Program只用于发现CVF可能遗漏或标题变化的候选，不能单独证明正式出版，也不会被直接并入母集。正式身份必须由CVF论文页与BibTeX，或可正常公开核验的IEEE Xplore正式记录确认；无法确认时保持`UNRESOLVED`，审计状态为`REVIEW_REQUIRED`。

对于自动流程仍未定位正式记录的`UNRESOLVED`论文，可选文件`raw\Formal_Verification_Resolutions.csv`用于提供人工发现的官方候选URL。最小字段为`Accepted_Title`、`Accepted_Authors`、`Resolution_Source`、`Resolution_URL`、`Resolution_Note`；当前`Resolution_Source`仅接受`CVF_DIRECT`。该文件只提供定位线索，不能直接决定正式身份：`audit-corpus`会重新请求CVF页面，复用详情解析器，并核验官方host、目标年份路径、CVPR Main Proceedings BibTeX、年份、标题及作者身份。验证失败时仍保持`UNRESOLVED`；文件不存在时审计行为与此前完全兼容。

审计将四类数据保持分离：

- `raw\All_Papers.csv`：CVF原始抓取事实，不因审计而改写；
- `raw\Official_Accepted_Papers.csv`：官方Accepted/Program核验来源；
- `raw\Corpus_Completeness_Audit.csv`：标题对账、差集、正式身份与Resolution；
- `raw\Formal_Proceedings_Corpus.csv/.xlsx`：仅含正式出版身份已确认的最终母集。

标题匹配分为`EXACT_OR_NORMALIZED_MATCH`、`PROBABLE_TITLE_VARIANT`、`ACCEPTED_ONLY`和`CVF_ONLY`。模糊相似度只生成候选，绝不自动改标题、增删论文或确认身份。`reports\<Venue><Year>_Corpus_Completeness.md`是极简状态报告；`VERIFIED`要求无抓取异常、无未决差异、无未解决标题变体且无正式元数据缺失。

仅重新筛选，不访问 CVF（前提是`audit-corpus`已经生成并验证Formal Proceedings Corpus）：

```bat
python main.py screen --venue CVPR --year 2026
python main.py screen --venue CVPR --year 2026 --rules config\screening_rules_v1_2.yaml
```

历史兼容命令（deprecated，不属于推荐流程）仍可由用户显式调用：

```bat
python main.py download --venue CVPR --year 2026
```

它保留旧RULE_KEEP/RULE_MAYBE下载行为，但`all`不会调用它。新的正常流程不会自动创建`PDFs\RULE_KEEP`或`PDFs\RULE_MAYBE`。

主题总控对机器一筛前三类的并集进行二筛，正式生成：

- `secondary_screening\Secondary_Screening.xlsx`
- `secondary_screening\KD_METHOD_CENTRIC.csv`
- `secondary_screening\OTHER_KD.csv`
- `secondary_screening\EXCLUDE.csv`

前两类全部进入全文阅读，EXCLUDE不下载。Python不自动判断二筛类别，也不自动决定助手分组。下载前，主题总控必须生成权威文件：

```text
assignments\<Venue><Year>_KD_REVIEW_Assignment.csv
```

例如`assignments\CVPR2025_KD_REVIEW_Assignment.csv`。正式Schema为：

| 列 | 要求 |
|---|---|
| `Paper_ID（论文编号）` | 非空、唯一，与两个需阅读二筛CSV的并集严格一一对应 |
| `Title（论文标题）` | 与对应二筛CSV标题一致 |
| `Year（年份）` | 与命令年份一致 |
| `Venue（会议/期刊）` | 与命令Venue一致 |
| `PDF_URL（PDF链接）` | 非空，与对应二筛CSV一致 |
| `Secondary_Class（二筛分类）` | 只能是`KD_Method_Centric`或`Other_KD` |
| `Assistant_Group（助手分组）` | 只能是`Assistant_1`～`Assistant_5` |
| `Assistant_Order（助手内顺序）` | 正整数，同一Class/Assistant内不可重复 |
| `Batch_ID（批次编号）` | 非空 |

Assignment是需全文阅读PDF物理组织的唯一权威来源。Python不会重新分类、分组、重排或按技术方向聚类。可先离线验证：

```bat
python main.py validate-assignment --venue CVPR --year 2025
```

该命令检查两类并集、Missing、Extra、Duplicate、Title、URL、Secondary Class、Assistant、Order、Batch和Venue-Year，且不下载。两个Secondary Class分别检查五组数量；`max - min <= 1`视为balanced，超过时只给WARNING，绝不修改Assignment。

验证后下载二筛需全文阅读论文：

```bat
python main.py download-secondary --venue CVPR --year 2025
```

`download-secondary`会再次强制执行相同验证，任何错误均在联网前停止。程序严格根据`Secondary_Class`和`Assistant_Group`直接写入最终目录：

```text
PDFs\
├── KD_Method_Centric\
│   └── Assistant_1～Assistant_5\
└── Other_KD\
    └── Assistant_1～Assistant_5\
```

只创建实际有论文的目录；不会创建FULL_READ、Read_First、Read_Normal、RESERVE、RULE_KEEP或RULE_MAYBE PDF目录，也不会下载EXCLUDE。

`download-reserve`仅为历史兼容（legacy/deprecated），不属于新Venue-Year正式流程：

```bat
python main.py download-reserve --venue CVPR --year 2026
python main.py download-reserve --venue CVPR --year 2026 --retry-failed
```

只有用户显式处理旧RESERVE数据时才会创建`PDFs\RESERVE`。新流程没有RESERVE概念，EXCLUDE默认不下载，也不会创建`PDFs\EXCLUDE`。

以下旧整理命令仅为历史CVPR2026兼容（legacy/deprecated），未来正式工作流不再使用：

```bat
python main.py download-secondary --venue CVPR --year 2026 --organize-existing
```

只重试 Manifest 中当前状态为 `FAILED` 的项目：

```bat
python main.py download-secondary --venue CVPR --year 2026 --retry-failed
```

失败重试严格沿用Manifest中的`Secondary_Class`、`Assistant_Group`、`Saved_Filename`和`Local_PDF_Path`，不会重新下载成功项、重新分类或产生根目录副本。

全文阅读后若分类修正较多，可先dry-run再执行本地重分类：

```bat
python main.py apply-reclassification --venue CVPR --year 2026 --csv "D:\path\reclassification.csv" --dry-run
python main.py apply-reclassification --venue CVPR --year 2026 --csv "D:\path\reclassification.csv" --execute
```

未指定`--execute`时默认dry-run。CSV至少包含`Paper_ID`、`Title`、`Final_KD_Centrality_Class`、`Target_Category_Folder`、`Target_Assistant_Folder`、`Target_Directory`。程序在移动前验证唯一源PDF、目标路径范围、分类、助手、重复与覆盖风险；任何missing、ambiguous或collision都会停止。执行不联网、不修改PDF内容，只移动并按统一Title规则重命名。

历史`organize-read-first`命令也只保留兼容性：

```bat
python main.py organize-read-first --venue CVPR --year 2026
```

以上两个organizer只读取历史FULL_READ / Read_Tier数据；新Venue-Year不依赖任何下载后移动步骤。

生成或刷新极简人类可读Status/QC报告：

```bat
python main.py report --venue CVPR --year 2026
```

`reports`只汇总数量、失败、缺失、重复和必要的异常Paper_ID；traceback、HTTP错误、retry过程和逐篇详情只进入`logs`。完全正常时，报告只有几行。异常时以`# WARNING`开头，并给出需要处理的少量条目。

报告输出为`reports\<Venue><Year>_Status.md`，不会复制完整论文列表或运行日志。

兼容命令`all`执行`crawl → audit-corpus`，只有审计为`VERIFIED`才继续`screen`，随后停止并提示`Secondary screening is required`。审计为`REVIEW_REQUIRED`或`ERROR`时停在审计阶段。它不会判断KD Method-Centric / Other KD / Exclude，不生成PDF，也不会绕过人工Gate：

```bat
python main.py all --venue CVPR --year 2026
```

受控元数据与筛选Smoke Test：

```bat
python main.py all --venue CVPR --year 2026 --limit 8
```

如需定向验证 KD 正例，可先从官方列表本地过滤标题；它仍使用真实 CVF 列表和详情页：

```bat
python main.py crawl --venue CVPR --year 2026 --title-query "Knowledge Distillation" --limit 1
python main.py screen --venue CVPR --year 2026
python main.py report --venue CVPR --year 2026
```

`--force` 会同时强制刷新listing和全部详情页，下载阶段会覆盖同名 PDF；它与只刷新listing的`--refresh-listing`含义不同。默认会复用缓存、保留已有成功记录并跳过已下载文件。

## PDF命名与KD REVIEW Manifest

正常PDF文件名优先使用完整论文Title，不附加Paper_ID。Windows非法字符`< > : " / \ | ? *`与控制字符替换为`-`，并处理末尾空格/点以及`CON`、`PRN`、`AUX`、`NUL`、`COM1`～`COM9`、`LPT1`～`LPT9`。

只有完整路径过长、Windows特殊限制或清洗后发生文件名冲突时，才缩短标题并追加`__<Paper_ID>`。冲突论文的Manifest字段`Filename_Collision`记为`TRUE`，不会静默覆盖。

两个需阅读类别统一使用一份`manifests\<Venue><Year>_KD_REVIEW_PDF_Manifest.csv`。它保留：

- Paper_ID、Title、PDF_URL；
- Secondary_Class、Assistant_Group、Assistant_Order、Batch_ID；
- Original_Title、Saved_Filename、Filename_Collision；
- Local_PDF_Path、Download_Status、File_Size、Failure_Reason。

下载器继续使用有限retry/backoff、`.part`临时文件、`%PDF-`文件头校验、原子重命名和合法PDF跳过机制；异常文件不会被静默覆盖。

## 后续Research Map数据约定

Paper Search Tool不判断论文内容。主题总控最终只维护一份`<Venue><Year>_KD_Research_Map.xlsx`，保存到该Venue-Year的`PDFs`根目录。工作簿正式使用`KD_Method_Centric`和`Other_KD`两个主Sheet，不再维护Read_First、Read_Normal或FULL_READ Sheet。

Research Map至少保留`KD_Centrality_Class`、`Centrality_Evidence`、`KD_Core_Direction`、`Assistant_Folder`和`PDF_Location`，确保能直接定位PDF。全文阅读后允许KD Method-Centric与Other KD相互修正；Research Map内容与Representative判断仍由主题总控和论文阅读助手完成。

## 设计要点

- 官方来源入口在 `config/source_config.yaml`：Main Conference 默认开启，Findings 和 Workshop 默认关闭。
- Findings 仅作为可选补充池。需要时应使用独立的来源配置运行，并始终按 `Track（论文轨道）` 与 Main Conference 分开统计、分开复核。
- 列表页和论文详情页分别缓存到`<Paper Library>\<Venue>\<Year>\raw\cache`。
- 每篇失败独立记录，异常不会中断同批其他论文。
- CVF原始论文池、官方Accepted/Program列表、完整性审计表与Formal Proceedings Corpus彼此独立，来源事实不会被覆盖。
- `screen`只读取状态与文件哈希均通过的`Formal_Proceedings_Corpus.csv`；母集缺失、未验证或审计后被改写都会停止。
- `config/screening_rules_v1_2.yaml`保存当前V1.2词表、权重、阈值、冲突规则和审计随机种子。
- `screen`默认使用冻结的V1.2，并在筛选输出、日志和compact report中记录`Rules Version: V1.2`；除非用户显式指定，不回退V1.1，也不自行创建V1.3。
- SAFE_DROP 不删除原始记录；四类输出都保存标题、摘要、命中规则、理由和官方链接。
- CSV 使用 `utf-8-sig`；XLSX 冻结表头、启用筛选、设置列宽并自动换行长摘要。

## 自动化测试

```bat
python -m pytest -q
```

测试全部使用`tmp_path`和本地假响应，不联网、不触碰默认Paper Library。覆盖CVF Parser、V1.2规则、两类二筛并集、Assignment完整性与分类内均衡检查、Title文件名、Windows路径、Class/Assistant直达下载、Manifest、retry、`.part`、reclassification dry-run/execute、compact report与legacy CLI兼容。

## 已知限制

- V1 只实现 CVF/CVPR；其他 Venue 需要新增来源适配器。
- HTML 结构改变时 Parser 测试可发现问题，但仍需更新选择器。
- 规则只读取 Title + Abstract，无法替代全文科研判断。
- 裸 `KD` 只作为弱信号；只有与 Teacher/Student/Distillation/Knowledge/Feature/Logit/Response/Compression 等上下文组合时才提高候选等级。`KD-tree`、`KD tree` 和 `k-d tree` 会被排除出 KD 缩写证据。
- 官方Accepted/Program页面或IEEE公开访问不可用时会保守标为`REVIEW_REQUIRED/UNRESOLVED`；程序不会绕过登录、验证码或反爬限制。
