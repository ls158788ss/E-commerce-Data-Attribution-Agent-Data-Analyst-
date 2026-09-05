基于 LangGraph 构建的 Data Analyst Agent：自然语言问数 → 语义层 → Schema/Join Graph → Text2SQL → SQL 沙盒 → 异常发现 → 动态下钻归因 → 可解释分析报告。

## ✨ 项目说明

| 能力 | 说明 |
|---|---|
| 🔀 确定性多角色工作流 | LangGraph StateGraph 编排 Planner / SQL / Validator / Analyst / Reporter 角色，节点不自由互调 |
| 📖 语义层 | 指标口径（退款率/GMV/客单价）、业务术语（华东=沪苏浙皖…）、业务规则全部 YAML 化，模型不再靠猜 |
| 🕸️ Schema/Join Graph | 外键图 + BFS `find_join_path()`，多表 Join 由图搜索给出路径，而非让 LLM 猜 |
| 🔎 混合检索 | BM25(jieba) + 向量(bge-small-zh，可选) RRF 融合检索表/列/术语/SQL 示例 |
| 🛡️ SQL 沙盒 | sqlglot AST 白名单 + 强制 LIMIT + 只读连接 + 超时/行数上限 + 审计日志 |
| 🔧 自修复循环 | 执行失败自动带错误回灌重写 SQL（≤3 次），全程留痕 |
| 🕵️ 动态下钻 | 异常检测 → 假设生成 → 维度下钻策略化推进（≤3 跳），直到定位根因 |
| 📏 Agent Evaluation | 自建 100 题评测集：Execution Accuracy / Schema Recall/Precision / Join Accuracy / 轨迹评估 |

## 🎯 Demo（实测输出）

> 提问：**红色女装最近退款率为什么上升？**

Agent 自动执行的完整下钻链路：

```text
第0跳  按天退款率时序（覆盖基线期+异常窗口）→ 确认波动上升趋势
   ↓ 决策：下钻 product 维度（信息增益判断）
第1跳  发现退款集中在「法式碎花连衣裙 31.9%」「复古泡泡袖雪纺裙 30.2%」
   ↓ 决策：下钻 size 维度
第2跳  XL 28.9% / XXL 21.4%，远高于 M/L（<2%）
   ↓ 决策：下钻 refund_reason 维度
第3跳  XL 码退款中「尺码不合适」占绝对大头
   ↓ 达到最大深度 3 → 终止下钻
报告   三步结论链：总体异常 → 定位维度 → 最终归因（XL/XXL 尺码适配问题）
```

每一步的意图解析、Schema 检索命中、JOIN 建议、SQL、校验结果、执行耗时都会在 **Agent Trace** 面板展示。

## 📏 实测数据

| 验证项 | 结果 |
|---|---|
| 简单问数（7月销售额） | Agent 回答与独立口径 Golden 值完全一致 |
| 多表 Join（华东女装销售额） | 正确走 orders+order_items+products，数字一致 |
| SQL 沙盒单测 | DROP/DELETE/INSERT/UPDATE/文件读取 全拦截 |
| 旗舰问题归因 | 命中埋入故事：XL/XXL + 尺码不合适占比 ~60% |

## 🚀 快速开始

```bash
# 1. 创建环境（Python 3.12）
conda create -n data-agent python=3.12 -y
conda activate data-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 .env（参考 .env.example，填入 OpenRouter API Key）

# 4. 生成模拟数据（含异常故事埋点）
python data/generate_data.py

# 5a. Web UI（三栏界面）
streamlit run ui/streamlit_app.py
# 5b. API 服务
uvicorn api.main:app --port 8000

## 🔒 安全设计

- Agent 全程只读：DuckDB 以 `read_only=True` 打开，物理不可写
- SQL 必须过沙盒：AST 白名单（仅 SELECT）、危险节点全树扫描、文件路径访问防御、JOIN 上限、强制 LIMIT、EXPLAIN 预检、超时熔断
- 每次尝试（含被拦截的）写入审计日志（`logs/sql_audit.jsonl`），可回溯可追责

## 🗄️ 多数据库后端切换（已实测）

## 📄 License

本项目代码仅作为学习参考之用，不构成任何形式的授权或开源许可。
