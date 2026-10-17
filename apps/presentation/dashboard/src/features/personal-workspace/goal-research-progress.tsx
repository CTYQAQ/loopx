import type {TrajectoryAssessment, TrajectoryInput, TrajectoryMetric, TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import "./goal-research-progress.css";

// Presentation of benchmark-toolkit facts and counts. No counter reduction,
// episode classification, score extraction or scientific comparison happens here.
export type ResearchProgressPack = {pack_id: string; title: string; scope: TrajectoryInput["scope"]; selection: TrajectoryInput["selection"];
  episode_count: number; result?: TrajectoryAssessment | null; applicability?: "current" | "historical";
  solver_usage?: {artifact: string; artifact_sha256: string; projection: TrajectorySolverUsage; applicability: "current" | "historical"; reason_code: string | null}};
const labels: Record<TrajectoryMetric, [string, string]> = {
  goal_alignment: ["关联原目标", "Addresses the objective"],
  benefit_test: ["已做收益检验", "Utility test performed"],
  discrimination: ["区分竞争解释", "Distinguishes explanations"],
  information_gain: ["产生决策新信息", "Decision-relevant new information"],
  evidence_adoption: ["后续行动采用证据", "Evidence used in later action"],
  redundancy: ["发现无益重复", "Avoidable repetition found"],
};

export function ResearchProgress({pack: p, packs, selectedId, error, zh, onSelect}: {pack: ResearchProgressPack | null; packs: Pick<ResearchProgressPack, "pack_id" | "title">[]; selectedId: string; error: string; zh: boolean; onSelect: (id: string) => void}) {
  const measured = p?.solver_usage, usage = measured?.projection;
  const number = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumFractionDigits: 1}).format(n);
  const duration = (s: number) => `${number(s / 60)} ${zh ? "分钟" : "min"}`;
  const stamp = (s: string) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(s));
  return <section className="research-progress" aria-label={zh ? "长程效果与用量" : "Long-horizon results and usage"}>
    <header><h3>{zh ? "效果与用量" : "Results and usage"}</h3>{packs.length > 1 ? <label>{zh ? "评估范围" : "Evaluation scope"}<select value={selectedId} onChange={e => onSelect(e.target.value)}>{packs.map(pack => <option key={pack.pack_id} value={pack.pack_id}>{pack.title}</option>)}</select></label> : null}</header>
    {error ? <p className="research-progress-warning" role="alert">{zh ? "无法核对本次来源；已显示的值仅为上次读回。" : "The source could not be refreshed. Displayed values are from the last readback."} {error}</p> : null}
    {p ? <>
    <p className="research-progress-scope">{p.title} · {p.selection.exhaustive ? (zh ? "仅覆盖声明的固定范围" : "Declared fixed scope only") : (zh ? "目的抽样，不代表全程" : "Purposive sample, not the whole run")} · {zh ? "按所选轨迹统计，与下方 Explore 历史分别读取。" : "Selected trajectory; read separately from the Explore history below."}</p>
    <dl className="research-progress-values">
      <div><dt>{zh ? "原生收益" : "Native utility"}</dt><dd>—</dd><small>{zh ? "此范围未接入原生评分" : "Native scores are not connected for this scope"}</small></div>
      <div><dt>{zh ? "捕获窗口跨度" : "Captured elapsed time"}</dt><dd>{usage ? duration(usage.elapsed_seconds) : "—"}</dd><small>{usage ? `${number(usage.completed_turns)} ${zh ? "轮完整记录，不是全程总成本" : "complete Turns, not total study cost"}` : (zh ? "尚无完整用量捕获" : "No complete usage capture")}</small></div>
      <div><dt>{zh ? "未缓存输入" : "Uncached input"}</dt><dd>{usage ? number(usage.uncached_input_tokens) : "—"}</dd><small>{usage ? "tokens" : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
      <div><dt>{zh ? "输出" : "Output"}</dt><dd>{usage ? number(usage.output_tokens) : "—"}</dd><small>{usage ? (zh ? "tokens，已含推理" : "tokens, includes reasoning") : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
    </dl>
    {usage ? <p className="research-progress-window"><time dateTime={usage.start}>{stamp(usage.start)}</time> — <time dateTime={usage.end}>{stamp(usage.end)}</time> UTC · {zh ? "只读捕获，与下方研究记录及分析片段窗口可能不同。" : "Read-only capture; its window can differ from the history and analyzed episode."}</p> : null}
    {measured?.applicability === "historical" ? <p className="research-progress-warning" role="status">{zh ? "用量来源已变化或不可读；以上保留历史捕获值，尚未核验当前用量。" : "The usage source changed or is unavailable. Values above are historical; current usage is unverified."}</p> : null}
    <details className="research-progress-basis"><summary>{zh ? "来源、时间与成本口径" : "Sources, time and cost basis"}</summary>
      <dl><dt>{zh ? "题目 / 运行" : "Case / run"}</dt><dd>{p.scope.case_id}<br/>{p.scope.run_id}</dd><dt>{zh ? "证据窗口" : "Evidence window"}</dt><dd>{p.scope.window}</dd><dt>{zh ? "取样规则" : "Selection rule"}</dt><dd>{p.selection.rule}</dd><dt>{zh ? "总体研究片段数" : "Population of episodes"}</dt><dd>{p.selection.population_episodes == null ? (zh ? "未知" : "Unknown") : number(p.selection.population_episodes)}</dd>
        {usage && measured ? <><dt>{zh ? "轮内时间合计" : "Time within Turns"}</dt><dd>{duration(usage.turn_elapsed_seconds)} · {zh ? "含工具和编排；不等于CPU、生成时间或可节省时间。" : "Includes tools and orchestration; not CPU, generation or avoidable time."}</dd><dt>{zh ? "输入总量" : "Total input"}</dt><dd>{number(usage.input_tokens)} tokens · {zh ? "其中缓存" : "including cached"} {number(usage.cached_input_tokens)}</dd><dt>{zh ? "用量来源" : "Usage source"}</dt><dd>{measured.artifact}<br/><code>{measured.artifact_sha256}</code><br/>{usage.method}</dd><dt>{zh ? "覆盖边界" : "Coverage limits"}</dt><dd>{zh ? "仅本次完整捕获；继承前缀、设置、原生评测、操作者及后续未完成轮的成本未包含。金额和全研究总成本未知。" : "This complete capture only. Inherited prefixes, setup, native evaluation, operator work and later incomplete Turns are excluded. Dollar and total study costs are unknown."}</dd></> : null}
        <dt>{zh ? "结果指标缺口" : "Outcome metric gaps"}</dt><dd>{zh ? "原生起点、保留最佳、最后有效值、达标时间/成本、增益面积与后期边际产出，尚无此包的同源可核算投影。不能从局部诊断或模型解释补算。" : "This pack has no source-bound projection for the native incumbent, retained best, final valid value, time/cost to threshold, gain area or late marginal gain. Diagnostics and model prose cannot supply them."}</dd><dt>{zh ? "来源版本" : "Source revision"}</dt><dd><code>{p.scope.revision}</code></dd>
      </dl>
    </details>
    {p.result ? <details className="research-process-counts"><summary>{zh ? "研究过程判读" : "Research-process judgments"} · {zh ? "未校准，仅所选样本" : "Uncalibrated, selected sample only"}{p.applicability === "historical" ? (zh ? " · 历史结果" : " · Historical result") : ""}</summary>
      <p>{zh ? "收益检验可以得到负结果；有价值的诊断不自动算作收益检验。各行分别判断，不合成研究质量分。" : "A utility test can be negative; a useful diagnosis alone is not a utility test. Rows are separate judgments, not an overall quality score."}</p>
      <table><thead><tr><th scope="col">{zh ? "判断" : "Judgment"}</th><th scope="col">{zh ? "是 / 适用数" : "Yes / eligible"}</th><th scope="col">{zh ? "否" : "No"}</th><th scope="col">{zh ? "未知" : "Unknown"}</th></tr></thead><tbody>{p.result.metrics.map(row => <tr key={row.metric}><th scope="row">{labels[row.metric][zh ? 0 : 1]}</th><td>{row.eligible ? `${number(row.yes)} / ${number(row.eligible)}` : (zh ? "不适用" : "N/A")}</td><td>{number(row.no)}</td><td>{number(row.unknown)}</td></tr>)}</tbody></table>
      <p>{zh ? "数值来自同一版本的 Agent 逐项判读；不是独立科学验证或全运行比例。适用数为零时不显示百分比。" : "Counts come from the same versioned Agent judgments, not independent scientific validation or a whole-run rate. Zero eligible cases are not a percentage."}</p>
    </details> : null}
    </> : <p role="status">{zh ? "正在核对此范围的来源与用量…" : "Checking sources and usage for this scope…"}</p>}
  </section>;
}
