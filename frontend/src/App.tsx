import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { lazy, Suspense, useState } from 'react';
import { api } from './api/client';
import { Overview } from './pages/Overview';
import { Opportunities } from './pages/Opportunities';
import { Levels } from './pages/Levels';
import { Futures } from './pages/Futures';
import { StatusPanel } from './components/StatusPanel';

const ChartPage=lazy(()=>import('./pages/ChartPage').then(m=>({default:m.ChartPage})));
const Options=lazy(()=>import('./pages/Options').then(m=>({default:m.Options})));
const Evidence=lazy(()=>import('./pages/Evidence').then(m=>({default:m.Evidence})));
const nav=[['Overview','/overview'],['Watchlist','/watchlist'],['Best setups','/best-setups'],['Live radar','/live-radar'],['Chart','/chart'],['Levels','/levels'],['Options','/options'],['Futures data','/futures'],['Research / Evidence','/evidence']];

export function App(){
  const [symbol,setSymbol]=useState('QQQ');
  const [watchlist,setWatchlist]=useState<string[]>(()=>{try{const saved=JSON.parse(localStorage.getItem('tde-watchlist')||'null');return Array.isArray(saved)&&saved.length?saved:['QQQ','SPY','IWM','SMH','NVDA','AMD','AMZN','META','AAPL','MSFT','AVGO','TSLA','GOOGL']}catch{return['QQQ','SPY','IWM','SMH','NVDA','AMD','AMZN','META','AAPL','MSFT','AVGO','TSLA','GOOGL']}});
  const navigate=useNavigate();
  const selectSymbol=(value:string)=>{setSymbol(value);navigate('/overview')};
  const updateWatchlist=(symbols:string[])=>{setWatchlist(symbols);localStorage.setItem('tde-watchlist',JSON.stringify(symbols))};
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to decision cockpit</a>
    {api.fixturesEnabled&&<div className="fixture-banner" role="status">DEVELOPMENT FIXTURE DATA · NOT LIVE · NOT FOR TRADING</div>}
    <header className="topbar"><div className="brand"><span className="brand-mark" aria-hidden="true">TD</span><div><small>TRADE DECISION ENGINE <b>PREVIEW</b></small><h1>Decision support, not trade execution.</h1></div></div><form className="symbol-search" onSubmit={e=>{e.preventDefault();const value=new FormData(e.currentTarget).get('symbol')?.toString().trim().toUpperCase();if(value)selectSymbol(value)}}><label className="sr-only" htmlFor="symbol">Ticker symbol</label><input id="symbol" name="symbol" value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} pattern="[A-Za-z][A-Za-z0-9.-]{0,9}" maxLength={10}/><button>Analyze</button></form></header>
    <nav aria-label="Primary navigation">{nav.map(([label,to])=><NavLink key={to} to={to}>{label}</NavLink>)}</nav>
    <div className="safety"><span>Decision support · Current analysis</span><em>READ-ONLY</em></div>
    <main id="main" tabIndex={-1}><Suspense fallback={<StatusPanel title="Loading view">Preparing this cockpit view…</StatusPanel>}><Routes><Route path="/overview" element={<Overview symbol={symbol}/>}/><Route path="/watchlist" element={<Opportunities mode="watchlist" symbols={watchlist} onWatchlistChange={updateWatchlist} onSelect={selectSymbol}/>}/><Route path="/best-setups" element={<Opportunities mode="best" symbols={watchlist} onSelect={selectSymbol}/>}/><Route path="/live-radar" element={<Opportunities mode="radar" symbols={watchlist} onSelect={selectSymbol}/>}/><Route path="/chart" element={<ChartPage symbol={symbol}/>}/><Route path="/levels" element={<Levels symbol={symbol}/>}/><Route path="/options" element={<Options symbol={symbol}/>}/><Route path="/futures" element={<Futures/>}/><Route path="/evidence" element={<Evidence/>}/><Route path="*" element={<Navigate to="/overview" replace/>}/></Routes></Suspense></main>
    <footer>No automated execution · No brokerage integration · Decision support only</footer>
  </div>
}
