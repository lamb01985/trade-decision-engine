import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MarketIntelligence, reportResultSchema } from '../pages/MarketIntelligence';
const result={freshness:'STALE',retrieved_at:'2026-10-25T12:00:00Z',freshness_policy:'Older than 24 hours',report:{schema_version:'market-intelligence-v1',report_id:'test',report_date:'2026-10-26',generated_at:'2026-10-25T12:00:00Z',observation_cutoff:'2026-10-23T20:00:00Z',market_regime:{label:'MIXED'},daily_features:{QQQ:{symbol:'QQQ',source_timestamp:'2026-10-23T20:00:00Z',close:500}},intraday_features:{},intraday_playbook:[],swing_playbook:[],options_intelligence:[],options_flow_intelligence:[],data_quality:{warnings:['Missing baselines']},data_errors:[]}};
afterEach(()=>vi.unstubAllGlobals());
it('validates report contract and rejects invalid timestamps',()=>{expect(reportResultSchema.safeParse(result).success).toBe(true);expect(reportResultSchema.safeParse({...result,report:{...result.report,observation_cutoff:'invalid'}}).success).toBe(false)});
it('shows stale timestamps, missing benchmarks, views and refresh',async()=>{
 const fetch=vi.fn().mockImplementation((url:string)=>Promise.resolve({ok:true,json:async()=>url.includes('history')?{reports:[result]}:result}));vi.stubGlobal('fetch',fetch);
 render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MarketIntelligence/></QueryClientProvider>);
 expect(await screen.findByText('STALE — historical evidence')).toBeInTheDocument();
 expect(screen.getAllByText('Unavailable — no report observations')).toHaveLength(3);
 fireEvent.click(screen.getByRole('button',{name:'Options Intelligence'}));expect(screen.getByText('Options intelligence unavailable')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Intraday Playbook'}));expect(screen.getByText('No playbook observations available')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Swing Opportunities'}));
 const before=fetch.mock.calls.length;fireEvent.click(screen.getByRole('button',{name:'Refresh reports'}));await waitFor(()=>expect(fetch.mock.calls.length).toBeGreaterThan(before));
});
