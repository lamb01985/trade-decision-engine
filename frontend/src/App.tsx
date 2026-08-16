import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { useState } from 'react';
import { api } from './api/client';
import { Overview } from './pages/Overview';
import { Opportunities } from './pages/Opportunities';
import { ChartPage } from './pages/ChartPage';
import { Levels } from './pages/Levels';
import { Options } from './pages/Options';
import { Futures } from './pages/Futures';
import { Evidence } from './pages/Evidence';

const nav=[['Overview','/overview'],['Watchlist','/watchlist'],['Best setups','/best-setups'],['Live radar','/live-radar'],['Chart','/chart'],['Levels','/levels'],['Options','/options'],['Futures data','/futures'],['Research / Evidence','/evidence']];
export function App(){const [symbol,setSymbol]=useState('QQQ');return <div className="app-shell"><a className="skip-link" href="#main">Skip to decision cockpit</a>{api.fixturesEnabled&&<div className="fixture-banner" role="status">DEVELOPMENT FIXTURE DATA · NOT LIVE · NOT FOR TRADING</div>}<header className="topbar"><div className="brand"><span className="brand-mark">TD</span><div><small>TRADE DECISION ENGINE <b>REACT FOUNDATION</b></small><h1>Decision support, not trade execution.</h1></div></div><form className="symbol-search" onSubmit={e=>{e.preventDefault();const value=new FormData(e.currentTarget).get('symbol')?.toString().trim().toUpperCase();if(value)setSymbol(value)}}><label className="sr-only" htmlFor="symbol">Ticker symbol</label><input id="symbol" name="symbol" defaultValue={symbol} maxLength={10}/><button>Analyze</button></form></header><nav aria-label="Primary navigation">{nav.map(([label,to])=><NavLink key={to} to={to}>{label}</NavLink>)}</nav><div className="safety"><b>SAFETY RAIL</b><span>Thesis is not a trade. Structure → confirmation → risk check.</span><em>READ-ONLY</em></div><main id="main"><Routes><Route path="/overview" element={<Overview symbol={symbol}/>}/><Route path="/watchlist" element={<Opportunities mode="watchlist"/>}/><Route path="/best-setups" element={<Opportunities mode="best"/>}/><Route path="/live-radar" element={<Opportunities mode="radar"/>}/><Route path="/chart" element={<ChartPage symbol={symbol}/>}/><Route path="/levels" element={<Levels symbol={symbol}/>}/><Route path="/options" element={<Options symbol={symbol}/>}/><Route path="/futures" element={<Futures/>}/><Route path="/evidence" element={<Evidence/>}/><Route path="*" element={<Navigate to="/overview" replace/>}/></Routes></main><footer>No automated execution · No brokerage integration · Backend decisions remain authoritative</footer></div>}
