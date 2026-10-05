# AAAI 年度验收 checkpoint

工程状态：**PRODUCTION_READY**（2026-10-05）。开发目录为 `D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`，分支 manual-multivenue-next。没有写正式 Paper Library。

实际成果保存在 `output/production_acceptance/attempts/annual_sets_02/AAAI/<year>/`。本次 continuation 只读验证既有 Corpus、Gate 和 screening 文件哈希，没有重新抓取 AAAI2024，也没有重复运行已完成的2025/2026。

| 年度 | 正式语料/唯一论文 | 年度 OAI 分组 | OAI 全部记录 | 非目标记录 | Audit | unresolved | KEEP / MAYBE / AMBIGUOUS / SAFE_DROP | 待二筛 |
|---|---:|---:|---:|---:|---|---:|---|---:|
|2024|2517|48|2865|348|VERIFIED|0|75 / 30 / 41 / 2371|146|
|2025|3182|53|3486|304|VERIFIED|0|73 / 51 / 51 / 3007|175|
|2026|4417|64|4920|503|VERIFIED|0|104 / 75 / 83 / 4155|262|

三个年度的 OAI 分组分页链均 COMPLETE，目标分组重复证据计数为0。出版社目录与独立年度 OAI 集合逐项 EXACT。之前2025的两条未闭合证据来自全站 OAI 导出失败，年度分组完整枚举已解决该问题；不是删去差异行。历史失败尝试仍保留。

一筛类型均为 **VERIFIED**，`screening/Run_Manifest.json` 为 SCREENED_CSV_READY；已通过 `completed_screening` 对 Corpus Gate、冻结规则、全部 screening 输出哈希及文件集合的只读复核。Needs_Secondary_Review 是机器候选池，不是最终科研分类。

冻结规则 SHA256：`4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514`。

continuation 全套离线测试：285 passed（包含 CVPR/ICML/ICLR），命令 `python -B -m pytest tests -q -p no:cacheprovider --basetemp=output/continuation_baseline_20261005_01`。所有网络均由测试 fixture 禁止。

生产入口仍是 `run_venue.py --venue AAAI --years 2024 2025 2026 --library-root <用户明确选择的根目录>`。开发验收入口是 `accept_production.py --venue AAAI --years ... --attempt <新的尝试名称>`；`--offline` 只复用已验证快照，缺缓存会失败，不联网。已完成开发成果无需重复运行。
