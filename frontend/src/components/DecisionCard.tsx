import type { Analysis } from '../domain/types';
import { friendlyState, presentSetupState } from '../domain/presentation';

const money=(n:number|null|undefined)=>n==null?'Unavailable':n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const unique=(values:string[])=>[...new Set(values.filter(Boolean))];

export function DecisionCard({analysis}:{analysis:Analysis}){
  const state=presentSetupState(analysis.setup_state,analysis.market_status.actionable);
  const conditions=unique([...analysis.waiting_for,...analysis.missing_confirmation]);
  const hasSetup=Boolean(analysis.setup_family);
  return <section className="decision-stack" aria-label={`Decision state: ${state}`}>
    <header className={`decision-header state-${state.toLowerCase()}`}>
      <div className="decision-identity"><small>PRIMARY DECISION</small><div className="decision-line"><strong>{state}</strong>{hasSetup&&<><span aria-hidden="true">·</span><h2>{friendlyState(analysis.setup_family)}</h2>{analysis.direction&&<b className={`direction ${analysis.direction.toLowerCase()}`}>{analysis.direction}</b>}</>}</div><p>{analysis.next_action}</p></div>
      <div className="current-quote"><small>CURRENT / LAST</small><strong>{money(analysis.current_or_last_price)}</strong></div>
    </header>

    <section className="trade-plan" aria-labelledby="trade-plan-title">
      <div className="section-title"><div><small>THE TRADE</small><h3 id="trade-plan-title">Backend-authored structure</h3></div>{analysis.quality&&<span className="quality">Quality {analysis.quality}</span>}</div>
      <div className="plan-strip">
        <div className="plan-cell entry"><span>Decision / entry zone</span><strong>{analysis.decision_zone?`${money(analysis.decision_zone.lower_bound??analysis.decision_zone.price)} – ${money(analysis.decision_zone.upper_bound??analysis.decision_zone.price)}`:'Unavailable'}</strong></div>
        <div className="plan-cell invalidation"><span>Invalidation</span><strong>{money(analysis.invalidation)}</strong></div>
        <div className="plan-cell"><span>Target 1</span><strong>{money(analysis.target_1)}</strong></div>
        <div className="plan-cell"><span>Target 2</span><strong>{money(analysis.target_2)}</strong></div>
        <div className="plan-cell"><span><abbr title="Potential reward divided by defined structural risk">Structural R:R</abbr></span><strong>{analysis.structural_risk_reward?.toFixed(2)??'Unavailable'}</strong></div>
      </div>
    </section>

    <div className="decision-support">
      <section className="requirements" aria-labelledby="requirements-title"><div className="section-title"><div><small>NEXT</small><h3 id="requirements-title">What needs to happen</h3></div><span>{conditions.length} open</span></div>{conditions.length?<ul className="condition-list">{conditions.map(x=><li key={x}><span aria-hidden="true">○</span>{x}</li>)}</ul>:<p className="empty-copy">No open conditions reported by the backend.</p>}</section>
      <section className="invalidation-panel" aria-labelledby="invalidation-title"><small>WHERE THE THESIS IS WRONG</small><h3 id="invalidation-title">{money(analysis.invalidation)}</h3>{analysis.what_changes_my_mind.length?<ul>{unique(analysis.what_changes_my_mind).map(x=><li key={x}>{x}</li>)}</ul>:<p>Explicit invalidation detail unavailable.</p>}</section>
    </div>

    <section className="evidence-rail" aria-labelledby="opportunity-title"><div><small>OPPORTUNITY / EVIDENCE</small><h3 id="opportunity-title">Does this deserve attention?</h3></div><div className="evidence-mini"><span><b>Evidence</b>{friendlyState(analysis.evidence_status)}</span><span><b>Sample</b>Unavailable</span><span><b>Median MFE / MAE</b>Unavailable</span><span><b>1R / 2R reach</b>Unavailable</span><span><b>Room to run</b>Unavailable</span></div><p>Population metrics are shown only when supplied by a compatible backend evidence contract.</p></section>
  </section>
}
