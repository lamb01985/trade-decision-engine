import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';
import { api, request } from '../api/client';
import { StatusPanel } from '../components/StatusPanel';
import './MarketIntelligence.css';

const object = z.record(z.string(), z.unknown());
const evidence = object.and(z.object({symbol:z.string(),source_timestamp:z.iso.datetime({offset:true})}));
export const reportResultSchema = z.object({freshness:z.enum(['RECENT','STALE','FUTURE_TIMESTAMP']), retrieved_at:z.iso.datetime({offset:true}), freshness_policy:z.string(), report:z.object({
  report_id:z.string(), schema_version:z.literal('market-intelligence-v1'), report_date:z.string(), generated_at:z.iso.datetime({offset:true}), observation_cutoff:z.iso.datetime({offset:true}),
  market_regime:object, daily_features:z.record(z.string(),evidence), intraday_features:object,
  intraday_playbook:z.array(evidence), swing_playbook:z.array(evidence), options_intelligence:z.array(object),
  options_flow_intelligence:z.array(object.and(z.object({symbol:z.string(),source_cutoff:z.iso.datetime({offset:true}),positioning:object,unusual_activity:object,trade_flow:object,volatility:object,gamma:object}))), data_quality:object,data_errors:z.array(z.unknown())
})});
const views=['Market Map','Options Intelligence','Intraday Playbook','Swing Opportunities'] as const;
function SourceAge({timestamp}:{timestamp:unknown}){const age=typeof timestamp==='string'?(Date.now()-Date.parse(timestamp))/3600000:NaN;return <p className="mi-missing">{!Number.isFinite(age)?'Source age unavailable':age<0?'Future source timestamp — verify clock':age>24?'Stale source — older than 24 hours':'Recent source snapshot — not a live quote'}</p>}
const label=(s:string)=>s.replaceAll('_',' ');
function Value({value}:{value:unknown}){
  if(value==null)return <span className="mi-missing">Unavailable</span>;
  if(Array.isArray(value))return value.length?<div className="mi-list">{value.map((v,i)=><div key={i}><Value value={v}/></div>)}</div>:<span className="mi-missing">No observations reported</span>;
  if(typeof value==='object')return <dl className="mi-values">{Object.entries(value).map(([k,v])=><div key={k}><dt>{label(k)}</dt><dd><Value value={v}/></dd></div>)}</dl>;
  return <span>{typeof value==='number'?value.toLocaleString(undefined,{maximumFractionDigits:4}):typeof value==='boolean'?(value?'Yes':'No'):String(value)}</span>;
}
export function MarketIntelligence(){
  const [view,setView]=useState<typeof views[number]>('Market Map');
  const q=useQuery({queryKey:['market-intelligence'],queryFn:async()=>reportResultSchema.parse(await request('/api/v1/market-intelligence/latest')),enabled:!api.fixturesEnabled,refetchInterval:60_000});
  const history=useQuery({queryKey:['market-intelligence-history'],queryFn:async()=>z.object({reports:z.array(reportResultSchema)}).parse(await request('/api/v1/market-intelligence/history?limit=10')),enabled:!api.fixturesEnabled});
  const r=q.data?.report;
  return <><header className="page-head"><div><small>MARKET INTELLIGENCE · PHASE 2 EVIDENCE</small><h2>{view}</h2><p>Timestamped research snapshots. No institutional identity, opening/closing intent, or verified dealer direction is inferred.</p></div><button disabled={q.isFetching||api.fixturesEnabled} onClick={()=>{void q.refetch();void history.refetch()}}> {q.isFetching?'Refreshing…':'Refresh reports'}</button></header>
    <div className="mi-tabs" role="group" aria-label="Market intelligence views">{views.map(v=><button key={v} aria-pressed={view===v} onClick={()=>setView(v)}>{v}</button>)}</div>
    <p>Refresh re-reads saved reports; report generation runs separately on the backend.</p>
    {api.fixturesEnabled?<StatusPanel title="Report unavailable in fixture mode">Use the local backend to inspect saved intelligence reports.</StatusPanel>:q.isPending?<StatusPanel title="Loading market intelligence"/>:q.isError?<StatusPanel kind="error" title="Report unavailable">{q.error.message}</StatusPanel>:null}
    {q.data&&r&&<><section className="mi-freshness" role="status"><strong>{q.data.freshness==='RECENT'?'Recent snapshot':q.data.freshness==='STALE'?'STALE — historical evidence':'FUTURE TIMESTAMP — verify source clock'}</strong><p>{q.data.freshness_policy}</p><p>Report date: {r.report_date} · Generated: {r.generated_at} · Observation cutoff: {r.observation_cutoff} · Retrieved: {q.data.retrieved_at}</p>{q.isError&&<strong>Refresh failed; showing previously loaded report.</strong>}</section>
    <details className="research-detail"><summary>Data quality and missing sources</summary><Value value={r.data_quality}/><Value value={r.data_errors}/></details>
    {view==='Market Map'?<><section className="evidence-verdict"><Value value={r.market_regime}/></section><div className="mi-grid">{[...new Set(['QQQ','SPY','IWM','SMH',...Object.keys(r.daily_features)])].map(symbol=><article className="mi-card" key={symbol}><h3>{symbol}</h3>{r.daily_features[symbol]?<><SourceAge timestamp={r.daily_features[symbol].source_timestamp}/><Value value={r.daily_features[symbol]}/></>:<p>Unavailable — no report observations</p>}</article>)}</div></>:
    view==='Options Intelligence'?r.options_flow_intelligence.length?<div className="mi-grid">{r.options_flow_intelligence.map((o,i)=><article className="mi-card" key={i}><h3>{o.symbol}</h3><p>Source cutoff: {o.source_cutoff}</p><SourceAge timestamp={o.source_cutoff}/>{['positioning','unusual_activity','trade_flow','volatility','gamma','stock_confirmation','data_quality','signal_policy'].map(k=><details key={k}><summary>{label(k)}</summary>{k==='trade_flow'&&<p>Bounded sample; classification estimates aggressor side only.</p>}{k==='gamma'&&<p>Explicit hypothetical inventory scenarios; dealer sign is unverified.</p>}<Value value={o[k]}/></details>)}<details><summary>Chain snapshot and source quality</summary><Value value={r.options_intelligence.filter(x=>x.symbol===o.symbol)}/></details></article>)}</div>:<StatusPanel title="Options intelligence unavailable"/>:
    <div className="mi-grid">{(view==='Intraday Playbook'?r.intraday_playbook:r.swing_playbook).map((s,i)=><article className="mi-card" key={i}><h3>{s.symbol}</h3><SourceAge timestamp={s.source_timestamp}/><Value value={s}/>{view==='Intraday Playbook'&&<details><summary>Intraday levels and structure</summary><Value value={r.intraday_features[s.symbol]}/></details>}</article>)}{!(view==='Intraday Playbook'?r.intraday_playbook:r.swing_playbook).length&&<StatusPanel title="No playbook observations available"/>}</div>}
    <details className="research-detail"><summary>Report history</summary>{history.isError?<p>History unavailable. Retry with Refresh reports.</p>:history.isPending?<p>Loading history…</p>:<Value value={history.data.reports.map(x=>({report_id:x.report.report_id,report_date:x.report.report_date,generated_at:x.report.generated_at,observation_cutoff:x.report.observation_cutoff,freshness:x.freshness}))}/>}</details></>}
  </>;
}
