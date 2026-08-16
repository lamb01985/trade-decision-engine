import {render,screen,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {scannerSchema} from '../api/schemas';
import {Opportunities} from '../pages/Opportunities';
import {deployedScannerResponse} from './deployedScannerFixture';

const response=(body:unknown)=>({ok:true,status:200,json:async()=>body});
const renderPage=(mode:'best'|'radar')=>render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><Opportunities mode={mode}/></MemoryRouter></QueryClientProvider>);
const withStatus=(changes:Record<string,unknown>)=>({...deployedScannerResponse,market_status:{...deployedScannerResponse.market_status,...changes}});
afterEach(()=>vi.unstubAllGlobals());

test('parses the real deployed 13-symbol scanner response and preserves its provider timestamp',()=>{
  const parsed=scannerSchema.parse(deployedScannerResponse);
  expect(parsed.market_status.provider_timestamp).toBe('2026-08-14T23:57:12.068971Z');
  expect(parsed.candidates[0]).toMatchObject({symbol:'SMH',distance_to_trigger_pct:-0.0348781815707161});
});

test.each([['best','Best setups'],['radar','Live setup radar']] as const)('renders populated %s results from the authoritative contract',async(mode,title)=>{
  const fetchMock=vi.fn().mockResolvedValue(response(deployedScannerResponse));vi.stubGlobal('fetch',fetchMock);renderPage(mode);
  expect(await screen.findByRole('heading',{name:title})).toBeVisible();
  expect(await screen.findByText('SMH')).toBeVisible();
  expect(screen.getByText('Proximity').parentElement).toHaveTextContent('-0.03%');
  expect(screen.queryByText(/Provider timestamp unavailable/i)).not.toBeInTheDocument();
  await waitFor(()=>expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({watchlist:['QQQ','SPY','IWM','SMH','NVDA','AMD','AMZN','META','AAPL','MSFT','AVGO','TSLA','GOOGL']}));
});

test('renders a valid zero-opportunity result only after a fresh authoritative scan',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({...deployedScannerResponse,candidates:[]})));renderPage('best');
  expect(await screen.findByText('No current opportunities')).toBeVisible();
  expect(screen.getByText(/authoritative scan completed successfully/i)).toBeVisible();
});

test('shows stale scanner state separately while retaining backend-ranked candidates',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(withStatus({data_status:'LIVE_STALE',session:'REGULAR'}))));renderPage('radar');
  expect(await screen.findByText('Scanner response is stale')).toBeVisible();
  expect(screen.getByText('SMH')).toBeVisible();
});

test('does not present backend DATA_ERROR as zero opportunities',async()=>{
  const body={...deployedScannerResponse,candidates:[],market_status:{...deployedScannerResponse.market_status,data_status:'DATA_ERROR',provider_timestamp:null,data_age_seconds:null}};
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(body)));renderPage('best');
  expect(await screen.findByText('Scanner data error')).toBeVisible();
  expect(screen.queryByText(/No current opportunities/i)).not.toBeInTheDocument();
});

test('does not fabricate freshness when an otherwise valid response lacks it',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(withStatus({provider_timestamp:null}))));renderPage('radar');
  expect(await screen.findByText('Scanner freshness unavailable')).toBeVisible();
  expect(screen.queryByText(/Provider /)).not.toBeInTheDocument();
  expect(screen.queryByText(/No current opportunities/i)).not.toBeInTheDocument();
});

test('does not present malformed scanner payloads as zero opportunities',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({...deployedScannerResponse,market_status:{data_status:'LIVE_CURRENT'}})));renderPage('best');
  expect(await screen.findByText('Scanner response invalid')).toBeVisible();
  expect(screen.queryByText(/No current opportunities/i)).not.toBeInTheDocument();
});

test('shows a backend request failure separately from malformed and empty responses',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:503,json:async()=>({})}));renderPage('radar');
  expect(await screen.findByText('Scanner unavailable')).toBeVisible();
  expect(screen.queryByText(/Scanner response invalid/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/No current opportunities/i)).not.toBeInTheDocument();
});
