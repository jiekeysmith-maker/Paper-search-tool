# 人工执行入口

代码基线为 8148669。先阅读 `CLEAN_MIGRATION_REPORT.md` 了解本轮验收范围。原 `main.py` 和 README 属于稳定基线；新四类 adapter 的人工入口以本文件为准。

目标流程：人工选择一个 Venue-Year → Python 抓取 → Python 独立完整性审计 → VERIFIED 后冻结 V1.2 → 候选池 → 人工二次复核。

2026-10-05 更新：现已提供 `run_venue.py`，按命令中指定的年份顺序执行抓取、独立审计和受门槛保护的 V1.2。正式调用说明见 `MULTIVENUE_RUNBOOK.md`，成熟度与验收边界见 `MULTIVENUE_IMPLEMENTATION_REPORT.md`。原迁移报告是历史记录，不代表当前实现状态。

每个 Venue-Year 有独立锁、可校验 HTTP 快照和进度文件；没有 scheduler、全局队列、心跳或自动续跑。AAAI/ECCV 尚未完成真实全年生产验收，不能把少量详情烟测解释为生产认证。

## 筛选的输入契约

调用方必须显式传入 library root；未来正式根目录为 `D:\_Knowledge Distillation\Paper Library`。路径自动限定为 `<root>/<Venue>/<Year>`，支持 ICML、ICLR、AAAI、ECCV。TPAMI 尚未实现，CVPR 不在新入口范围。

每个年度必须已有：

```text
<root>/<Venue>/<Year>/raw/Audit_Summary.json
<root>/<Venue>/<Year>/raw/Formal_Proceedings_Corpus.csv
```

审计 JSON 必须包含 `venue`、`year`、`status="VERIFIED"`、`unresolved_count=0`、整数 `publisher_count` 和与 CSV 原始字节匹配的 `corpus_sha256`。`corpus_path` 应省略或仅为 `Formal_Proceedings_Corpus.csv`，不能引用旧绝对路径。由完整性审计器生成这些字段，不应手工修改状态放行。

CSV 至少包含 `Paper_ID, Title, Authors, Abstract, Venue, Year, Track, Official_URL, PDF_URL, Formal_Publication_Evidence`。PDF_URL 可空；其余必需字段不可空；两个身份字段分别唯一，所有行的 Venue/Year 必须相同且匹配调用参数。

## 未来人工调用

下面仅为命令格式，迁移阶段没有对正式数据运行：

```powershell
# 默认只在内存筛选和验证，不创建输出。
python screen_verified.py ICML 2024 --library-root "<人工指定的库根目录>"

# 显式写入该年度 screening；目录已存在时拒绝覆盖。
python screen_verified.py ICML 2024 --library-root "<人工指定的库根目录>" --write
```

输出 `KD_Screening.csv`、四类规则分组 CSV、`Needs_Secondary_Review.csv`、`SAFE_DROP_Audit_Sample.csv` 和 `Run_Manifest.json`。写入时使用本年度 runtime 下的锁和临时目录，整组完成后重命名为 screening；已有 screening 拒绝覆盖。没有全局状态表、调度资格文件、跨年度写入、PDF 下载或自动人工确认。

传输调用方应先 `get_metadata(url)`，再 `response.raise_for_status()`；最终 429/5xx 不可作为成功输入。长 Retry-After 会停止并抛出 `RetryDeferred`，由人工决定后续动作。

## 重跑有限离线测试

已在 Python 3.13.5 验证；依赖见原 `requirements.txt`。应使用一个新的 basetemp 名称以保留既有测试输出：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
New-Item -ItemType Directory -Path output -Force
python -m pytest -q -p no:cacheprovider --basetemp=output/manual_test_next tests/test_allvenues_adapters.py tests/test_iclr_adapter.py tests/test_metadata_transport.py tests/test_ojs_adapter.py tests/test_springer_adapter.py tests/test_manual_screening.py
```

单元测试禁止网络，仅使用本地历史快照和合成输入。本轮通过结果不能替代任何全年完整性审计。
