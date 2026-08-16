import { useEffect,useMemo,useState } from 'react';
import { useAnalysis,useChart } from '../api/queries';
import { DecisionCard } from '../components/DecisionCard';
import { FinancialChart } from '../components/FinancialChart';
import { Freshness } from '../components/Freshness';
import { StatusPanel } from '../components/StatusPanel';
import { Link } from 'react-router-dom';

export function Overview({symbol}:{symbol:string}){
  const analysis=useAnalysis(symbol);const chart=useChart(symbol,'5m',false);const snapshot=chart.data?.data;const [enabled,setEnabled]=useState<string[]>([]);
  useEffect(()=>{if(snapshot)setEnabled(snapshot.overlays.filter(o=>o.default_visible).map(o=>o.id))},[snapshot]);
  const visible=useMemo(()=>new Set(enabled),[enabled]);
  if(analysis.isLoading)return <StatusPanel title="Loading decision">Analysis is not actionable until this request completes.</StatusPanel>;
  if(analysis.isError)return <StatusPanel kind="error" title="Backend unavailable">{analysis.error.message} Re-run analysis before making a decision.</StatusPanel>;
  const result=analysis.data!;
  return <><header className="cockpit-head"><div><small>ACTIVE SYMBOL</small><h1>{result.data.symbol}</h1></div><Freshness status={result.data.market_status}/>{result.source==='fixture'&&<span className="source-badge">FIXTURE</span>}</header>
    <div className="cockpit-layout">
      <section className="chart-workspace" aria-labelledby="workspace-chart-title"><div className="workspace-head"><div><small>PRICE CONTEXT</small><h2 id="workspace-chart-title">5 minute structure</h2></div><Link to="/chart">Open full chart</Link></div>
        {chart.isLoading?<StatusPanel title="Loading chart"/>:chart.isError||!snapshot?.candles.length?<StatusPanel title="Chart unavailable">No validated completed bars are available. No synthetic chart is shown.</StatusPanel>:<><div className="overlay-controls compact" role="group" aria-label="Overview chart overlays">{snapshot.overlays.map(o=><label key={o.id}><input type="checkbox" checked={visible.has(o.id)} onChange={e=>setEnabled(x=>e.target.checked?[...x,o.id]:x.filter(id=>id!==o.id))}/>{o.label}</label>)}</div><FinancialChart snapshot={snapshot} visible={visible}/></>}
      </section>
      <DecisionCard analysis={result.data}/>
    </div>
  </>
}
