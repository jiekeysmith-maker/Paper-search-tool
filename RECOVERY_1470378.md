# 1470378 恢复审计

开发位置：`D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`。
恢复时 branch 为 manual-multivenue-next，HEAD 和本地 origin 跟踪引用均为 1470378；工作区 clean。未 fetch，因此不声称远端实时状态已核验。没有 reset、覆盖或删除旧提交。

基线离线测试：`pytest tests -q -p no:cacheprovider`，270 passed。初次默认发现扫描到历史 output 的权限目录，随后显式限定 tests；沙盒临时目录创建受限的运行不计为代码测试失败。最终以受授权的开发目录测试运行结果为准。冻结 V1.2 SHA256 匹配。

## 现有实现与缺口

| Venue | 已保存实现及实际证据 | 尚未完成 |
| --- | --- | --- |
| AAAI | 官方年度目录/OJS archive 双枚举、section 范围、OAI token 链、DOI/URL/卷期核验、OAI 元数据复用、限定卷期 Robotics 别名。缓存含 2024/2025/2026 年度目录；OAI 部分行 2581/3239/2136。 | 全站 OAI 在 23400/26185 处 HTTP500；错误导致已有年度元数据没有输出到 provisional。重复 XML 判定需核查尾部空白影响；完整 section 和缺项补抓尚待验收。 |
| ECCV | 多卷/Part/分页、正式 chapter、ECVA 对账；已修复双空格 Part 和登录回跳 URL 被误认作分页。2024 已保存 index2388、metadata2386、ECVA2387。 | 2024 仍860条审计问题（不等于缺失论文数），需要具体对象级解释及标题/作者结构核验。2026 旧验收锁 PID26760 仍存在，先不修改或重复启动该任务。 |
| TPAMI | 公开 IEEE/CSDL 入口、严格元数据解析、final issue year 策略、跨年度 DOI 检查、锁/缓存/正式 Gate 衔接，已有日期和 Early Access 字段。 | 实际页面是应用外壳，三年数量未知；没有可验证年度全集。最新用户要求保留首次官方发表事件并对跨年归属显式复核，不能把旧 final-issue 策略当用户科研决策。 |

main.py/src 仍是原 CVPR 与下游工作流；source_config.yaml 主要供 CVPR。新人工入口 run_venue.py 不依赖旧调度器。当前分支未发现独立 provisional screening 入口；正式 screen_verified.py 本轮保持不变。原 runbook/implementation report 描述的是 a12acf2，不代表 WIP 已认证。

后续顺序：AAAI →（AAAI验收通过后）ECCV → TPAMI → 回归和文档。保留正确 adapter 和通用接口，所有新真实验收只写开发 output；不写 Paper Library，不修改生产工作目录，不改科研规则，不 merge/push。
