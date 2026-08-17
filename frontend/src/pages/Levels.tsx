import { useAnalysis } from '../api/queries';
import { StatusPanel } from '../components/StatusPanel';
import type { Analysis,Level } from '../domain/types';
import './Levels.css';

const label=(types:string[])=>types.map(x=>x.replaceAll('_',' ')).join(' · ');
const category=(level:Level)=>{const value=level.types.join(' ').toUpperCase();if(/VWAP|EMA/.test(value))return 'Dynamic reference';if(/HOD|LOD|PRIOR|OPENING/.test(value))return 'Session structure';if(/SUPPORT|RESISTANCE|STRUCT/.test(value))return 'Structural support / resistance';return 'Other backend levels'};
const same=(left:number|null|undefined,right:number)=>left!=null&&Math.abs(left-right)<0.000001;

function guidance(level:Level,analysis:Analysis){
  if(same(analysis.trigger,level.price)||same(analysis.decision_zone?.price,level.price))return{role:'Decision trigger',ifText:`Price interacts with the mapped trigger at ${level.price.toFixed(2)}.`,thenText:`Backend action: ${analysis.next_action}`,detail:analysis.missing_confirmation.map(item=>`Still required: ${item}`)};
  if(same(analysis.invalidation,level.price))return{role:'Invalidation',ifText:`Price reaches the mapped invalidation at ${level.price.toFixed(2)}.`,thenText:'The current thesis is invalid.',detail:analysis.what_changes_my_mind.map(item=>`Backend condition: ${item}`)};
  if(same(analysis.target_1,level.price))return{role:'Target 1',ifText:`Price reaches mapped Target 1 at ${level.price.toFixed(2)}.`,thenText:'The first backend-mapped target has been reached.',detail:[]};
  if(same(analysis.target_2,level.price))return{role:'Target 2',ifText:`Price reaches mapped Target 2 at ${level.price.toFixed(2)}.`,thenText:'The second backend-mapped target has been reached.',detail:[]};
  return null;
}

export function Levels({symbol}:{symbol:string}){
  const query=useAnalysis(symbol);
  if(query.isLoading)return <StatusPanel title="Loading structural levels"/>;
  if(query.isError)return <StatusPanel kind="error" title="Levels unavailable">{query.error.message}</StatusPanel>;
  const analysis=query.data!.data;
  const zone=analysis.decision_zone?(analysis.decision_zone.lower_bound!=null&&analysis.decision_zone.upper_bound!=null?`${analysis.decision_zone.lower_bound.toFixed(2)} – ${analysis.decision_zone.upper_bound.toFixed(2)}`:analysis.decision_zone.price.toFixed(2)):analysis.trigger?.toFixed(2);
  const watch=[...new Set([...analysis.waiting_for,...analysis.missing_confirmation])];
  const plan=[analysis.decision_zone&&{name:'Decision zone',value:zone!},analysis.invalidation!=null&&{name:'Invalidation',value:analysis.invalidation.toFixed(2)},analysis.target_1!=null&&{name:'Target 1',value:analysis.target_1.toFixed(2)},analysis.target_2!=null&&{name:'Target 2',value:analysis.target_2.toFixed(2)}].filter(Boolean) as {name:string;value:string}[];
  const sorted=[...analysis.full_levels].sort((x,y)=>(Math.abs(x.distance_pct??Infinity)-Math.abs(y.distance_pct??Infinity))||(y.price-x.price));
  const names=[...new Set(sorted.map(category))];
  return <>
    <header className="page-head"><div><small>DECISION PLAYBOOK</small><h2>{symbol} level map</h2><p>Expand a level for its backend-mapped role. Structural references never create an action in the browser.</p></div><button className="secondary" onClick={()=>query.reanalyze()} disabled={query.isReanalyzing}>{query.isReanalyzing?'Re-analyzing…':'Re-analyze levels'}</button></header>
    {query.refreshError&&<StatusPanel kind="error" title="Re-analysis failed">The prior snapshot remains visible for context. Retry before acting.</StatusPanel>}
    <section className="levels-playbook" aria-labelledby="levels-playbook-title"><div className="levels-playbook-head"><small>IF THIS → THEN THAT</small><h3 id="levels-playbook-title">Conditional decision playbook</h3></div><div className="levels-playbook-grid"><article><small>IF PRICE</small><strong>{zone?`Interacts with decision zone ${zone}`:'No backend decision zone is available.'}</strong></article><article><small>THEN</small><strong>{analysis.next_action}</strong></article><article><small>WATCH</small>{watch.length?<ul>{watch.map(x=><li key={x}>{x}</li>)}</ul>:<p>No backend-authored watch condition is available.</p>}</article><article className="invalid"><small>INVALID IF</small>{analysis.what_changes_my_mind.length?<ul>{analysis.what_changes_my_mind.map(x=><li key={x}>{x}</li>)}</ul>:<p>No backend-authored invalidation condition is available.</p>}{analysis.invalidation!=null&&<b>Mapped invalidation {analysis.invalidation.toFixed(2)}</b>}</article></div></section>
    <section className="levels-reference" aria-labelledby="levels-reference-title"><div className="levels-reference-head"><small>STRUCTURAL REFERENCE</small><h3 id="levels-reference-title">Raw categorized level map</h3><p>Select any row to inspect its meaning and IF/THEN guidance.</p></div><section className="plan-levels" aria-label="Trade plan levels">{plan.map(x=><div key={x.name}><small>{x.name}</small><strong>{x.value}</strong></div>)}</section>{sorted.length?names.map(name=><section className="level-group" key={name}><h3>{name}</h3><div className="level-list">{sorted.filter(level=>category(level)===name).map((level,index)=>{const action=guidance(level,analysis);return <details className="level-row" key={`${level.price}-${index}`}><summary><strong>{level.price.toFixed(2)}</strong><span>{label(level.types)}</span><span>{level.distance_pct==null?'Distance unavailable':`${Math.abs(level.distance_pct).toFixed(2)}% ${level.position??''}`}</span><em>{action?.role??level.importance}</em></summary><div className="level-detail"><dl><div><dt>WHAT IS THIS?</dt><dd>Backend types: {label(level.types)||'structural level'}</dd></div><div><dt>SETUP ROLE</dt><dd>{action?.role??'No setup-specific role is mapped to this price.'}</dd></div></dl>{action?<div className="level-action-flow"><div><small>IF THIS HAPPENS</small><p>{action.ifText}</p></div><div className={action.role==='Invalidation'?'stop':''}><small>THEN</small><strong>{action.thenText}</strong>{action.detail.length>0&&<ul>{action.detail.map(item=><li key={item}>{item}</li>)}</ul>}</div></div>:<p className="no-action">No backend-authored IF/THEN action is attached to this structural reference.</p>}</div></details>})}</div></section>):<StatusPanel title="Additional levels unavailable">The current response did not include a full structural map.</StatusPanel>}</section>
  </>;
}
