# 知识库测评工具包（客服 / 结算 / 运营 三岗位）

三份岗位操作手册 + 48 题标准测试集 + 全流程脚本。方法论文档：**[测评方案.md](./测评方案.md)**。

## 目录结构

```
evaluation/
├── 测评方案.md              # 指标定义、执行步骤、访谈提纲、报告模板（先读这个）
├── manuals/                 # 三份操作手册（合成演示版，可替换为访谈真实产出）
│   ├── 01-客服-理赔SOP操作手册.md
│   ├── 02-结算-运费合同结算手册.md
│   └── 03-运营-站点操作手册.md
├── golden_set.jsonl         # 48 题标准测试集（L1事实/L2规则/L3计算/L4多跳/L5拒答）
├── upload_manuals.py        # Step 1  发布手册并等待索引
├── run_eval.py              # Step 3  批量检索+问答+计时
├── judge_and_report.py      # Step 4  LLM/关键词评审 + 指标计算 + report.md
├── baseline_template.csv    # Step 2  人工基线计时表（可选，用于实测节省时间）
└── results/                 # 运行产物
```

## 快速开始

```bash
# 前置：服务栈与应用已在 :3000 运行；.env 里
#   DOCUMENT_REQUIRE_APPROVAL=false   DASHSCOPE_API_KEY=sk-xxx
cd evaluation

python upload_manuals.py                                        # ① 导入语料（等索引就绪）
# ②（可选）员工按 baseline_template.csv 实测人工查档时间
python run_eval.py                                              # ③ 采集 48 题结果
DASHSCOPE_API_KEY=sk-xxx python judge_and_report.py             # ④ 评审出报告 → results/report.md
```

冒烟（3 分钟验证链路）：

```bash
python upload_manuals.py && python run_eval.py --only CS-L1-01,ST-L3-01,OP-L5-01 \
  && DASHSCOPE_API_KEY=sk-xxx python judge_and_report.py
```

环境变量：`BASE_URL`（默认 http://localhost:3000）、`EVAL_USERNAME`/`EVAL_PASSWORD`（默认 user/123456）、`JUDGE_MODEL`（默认 qwen-plus）。
