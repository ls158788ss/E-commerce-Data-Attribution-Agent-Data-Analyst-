/**
 * 与后端一一对应的 TypeScript 类型定义。
 *
 * 对应关系：
 *   - QueryResult   ← graph/state.py 的 AgentState 输出字段（SSE final 事件的 payload）
 *   - TraceEvent    ← graph/nodes.py 各节点往 state["trace"] 里追加的事件
 *   - SemanticSummary ← api/main.py 的 GET /api/semantic/summary
 *
 * 前后端共享同一套字段契约，后端用 Pydantic/TypedDict 约束，前端用 interface 约束。
 */

/** 语义层摘要（GET /api/semantic/summary） */
export interface SemanticSummary {
  metrics: { business_name: string; definition: string; unit: string }[];
  tables: { table: string; description: string; column_count: number }[];
  glossary: { term: string; definition: string }[];
}

/**
 * Agent Trace 单条事件。
 * step 的取值（由后端各节点写入）：
 *   planner / schema_retrieval / sql_gen / drill_sql_gen / repair_N /
 *   validate / execute / analyze / drilldown_decision / answer / fail
 * 不同 step 携带不同字段，因此保留索引签名。
 */
export interface TraceEvent {
  step: string;
  [key: string]: unknown;
}

/** 图表数据（analysis/report.py 产出） */
export interface ChartSpec {
  kind: "line" | "bar";
  title: string;
  rows: Record<string, unknown>[];
}

/** 归因结论链的一步 */
export interface ChainStep {
  step: number | string;
  statement: string;
  evidence_ref?: string;
}

/** 下钻证据（每一跳） */
export interface EvidenceHop {
  hop?: number;
  dimension?: string;
  columns?: string[];
  rows?: Record<string, unknown>[];
  note?: string;
}

/** SSE final 事件里的完整结果（对应 AgentState 的输出字段） */
export interface QueryResult {
  success: boolean;
  answer?: string;
  fail_reason?: string;
  final_sql?: string;
  result_rows?: Record<string, unknown>[];
  result_columns?: string[];
  caveats?: string[];
  conclusion_chain?: ChainStep[];
  charts?: ChartSpec[];
  evidence?: EvidenceHop[];
  trace?: TraceEvent[];
  elapsed_ms?: number;
}

/** SSE 事件（POST /api/query/stream 推送的三种类型） */
export type StreamEvent =
  | { type: "node"; node: string; trace: TraceEvent[] }
  | { type: "final"; payload: QueryResult }
  | { type: "error"; message: string };

/** 聊天区一条消息 */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  detail?: QueryResult;
  error?: boolean;
}
