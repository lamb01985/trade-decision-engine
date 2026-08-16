import type { Opportunity } from '../domain/types';
import { friendlyState } from '../domain/presentation';
const value=(n:number|null|undefined)=>n==null?'Unavailable':n.toFixed(2);
export function OpportunityCard({item,index,onSelect,mode='radar',providerTimestamp}:{item:Opportunity;index:number;onSelect?:(symbol:string)=>void;mode?:'watchlist'|'best'|'radar';providerTimestamp:string}){return <article className="opportunity-row">
  <span className="row-rank" aria-label={`Rank ${index+1}`}>{index+1}</span>
  <div className="row-symbol"><strong>{item.symbol}</strong><span className={`direction ${item.direction.toLowerCase()}`}>{item.direction}</span></div>
  <div className="row-setup"><b>{friendlyState(item.setup_family)}</b><span>{friendlyState(item.lifecycle)}</span></div>
  <div><small>Decision zone</small><strong>{value(item.trigger)}</strong></div>
  <div><small>Proximity</small><strong>{value(item.distance_to_trigger_pct)}%</strong></div>
  <div><small>R:R</small><strong>{value(item.structural_risk_reward)}</strong></div>
  <div><small>Evidence</small><strong>{friendlyState(item.evidence_status)}</strong></div>
  <div><small>Room</small><strong>{friendlyState(item.room_to_run_status)}</strong></div>
  <div><small>Freshness</small><strong>{new Date(providerTimestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</strong></div>
  <details><summary>Details</summary><div className="row-detail"><span><b>Last</b>{value(item.current_or_last_price)}</span><span><b>Invalidation</b>{value(item.invalidation)}</span><span><b>Targets</b>{value(item.target_1)} / {value(item.target_2)}</span><span><b>Quality</b>{item.quality}</span></div>{item.reasons.length>0&&<ul>{item.reasons.map(x=><li key={x}>{x}</li>)}</ul>}{onSelect&&<button className="card-action" onClick={()=>onSelect(item.symbol)} aria-label={`Open ${item.symbol} decision detail`}>Open decision</button>}</details>
</article>}
