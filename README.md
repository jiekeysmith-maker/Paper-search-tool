# 论文搜索工具（Paper Search Tool）V1

这是一个面向科研文献初筛的本地 Python 工具。V1 从 CVF Open Access 获取 CVPR Main Conference 与 Findings 的官方论文元数据，使用完全确定性的 Python 规则进行 Knowledge Distillation（KD）高召回初筛，并生成可追溯表格、人工复核队列、SAFE_DROP 审计样本、官方 PDF Manifest 和中文报告。

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

## 命令

小样本抓取（`--limit` 是所有启用轨道合计上限，按 Main/Findings 轮转取样）：

```bat
python main.py crawl --venue CVPR --year 2026 --limit 20
```

仅重新筛选，不访问 CVF：

```bat
python main.py screen --venue CVPR --year 2026
python main.py screen --venue CVPR --year 2026 --rules config\screening_rules_v2.yaml
```

下载 KEEP/MAYBE 官方 PDF：

```bat
python main.py download --venue CVPR --year 2026
```

仅生成/刷新报告：

```bat
python main.py report --venue CVPR --year 2026
```

正式全流程（会抓取全量并下载全部候选，请确认后再运行）：

```bat
python main.py all --venue CVPR --year 2026
```

受控端到端 Smoke Test：

```bat
python main.py all --venue CVPR --year 2026 --limit 8 --pdf-limit 1
```

如需定向验证 KD 正例，可先从官方列表本地过滤标题；它仍使用真实 CVF 列表和详情页：

```bat
python main.py crawl --venue CVPR --year 2026 --title-query "Knowledge Distillation" --limit 1
python main.py screen --venue CVPR --year 2026
python main.py download --venue CVPR --year 2026 --limit 1
python main.py report --venue CVPR --year 2026
```

`--force` 会忽略 HTML 缓存并重新请求，下载阶段会覆盖同名 PDF。默认会复用缓存、保留已有成功记录并跳过已下载文件。

## 设计要点

- 官方来源入口在 `config/source_config.yaml`，Workshop 默认关闭。
- 列表页和论文详情页分别缓存到 `output/CVPR_2026/raw/cache`。
- 每篇失败独立记录，异常不会中断同批其他论文。
- 原始论文池永远独立于筛选结果，修改 YAML 后可零网络重筛。
- `config/screening_rules_v1.yaml` 保存全部词表、权重、阈值、冲突规则、下载策略和审计随机种子。
- SAFE_DROP 不删除原始记录；四类输出都保存标题、摘要、命中规则、理由和官方链接。
- CSV 使用 `utf-8-sig`；XLSX 冻结表头、启用筛选、设置列宽并自动换行长摘要。

## 自动化测试

```bat
python -m pytest -q
```

测试覆盖 CVF Parser、六类核心筛选情形、缺失摘要、Windows 文件名、中文 CSV/XLSX、缓存复用和 PDF 写入/签名校验。

## 已知限制

- V1 只实现 CVF/CVPR；其他 Venue 需要新增来源适配器。
- HTML 结构改变时 Parser 测试可发现问题，但仍需更新选择器。
- 规则只读取 Title + Abstract，无法替代全文科研判断。
- `KD` 是高召回弱保护信号，可能增加误报；SAFE_DROP 审计和规则版本迭代用于控制漏检风险。
- 抓取是否完整依赖 CVF 当时公开的官方列表；临时网络失败会进入异常队列而非被视为论文不存在。

