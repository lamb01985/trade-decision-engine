import {render,screen} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {Levels} from '../pages/Levels';
import {analysis} from './factories';

const payload={...analysis,decision_zone:{...analysis.decision_zone!,lower_bound:581,upper_bound:582},next_action:'MONITOR — await backend confirmation',waiting_for:['Fresh regular-session data'],missing_confirmation:['Volume confirmation'],what_changes_my_mind:['Acceptance below invalidation'],invalidation:578.8,full_levels:[{price:581.5,types:['SESSION_VWAP'],importance:'HIGH',distance_pct:-.11,position:'BELOW',confluence:2}]};
const renderLevels=(body:unknown=payload)=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>body}));return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><Levels symbol="QQQ"/></MemoryRouter></QueryClientProvider>)};
afterEach(()=>vi.unstubAllGlobals());

test('puts the backend-authored conditional playbook before the raw categorized map',async()=>{
  renderLevels();
  const playbook=await screen.findByRole('heading',{name:'Conditional decision playbook'});
  const reference=screen.getByRole('heading',{name:'Complete level map'});
  expect(playbook.compareDocumentPosition(reference)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText('Interacts with decision zone 581.00 – 582.00')).toBeVisible();
  expect(screen.getByText(payload.next_action)).toBeVisible();
  expect(screen.getByText('Fresh regular-session data')).toBeVisible();
  expect(screen.getByText('Volume confirmation')).toBeVisible();
  expect(screen.getByText('Acceptance below invalidation')).toBeVisible();
  expect(screen.getByText('Invalidation 578.80')).toBeVisible();
});

test('retains the Phase 5G categorized level map as secondary reference',async()=>{
  renderLevels();
  expect(await screen.findByRole('heading',{name:'Complete level map'})).toBeVisible();
  expect(screen.getByRole('heading',{name:'Dynamic reference'})).toBeVisible();
  expect(screen.getAllByText('581.50').length).toBeGreaterThan(0);
  expect(screen.getByText('SESSION VWAP')).toBeVisible();
});

test('does not invent conditional copy when backend conditions are absent',async()=>{
  renderLevels({...payload,decision_zone:null,trigger:null,waiting_for:[],missing_confirmation:[],what_changes_my_mind:[],invalidation:null});
  expect(await screen.findByText('No decision zone is currently available.')).toBeVisible();
  expect(screen.getByText('No watch condition is currently available.')).toBeVisible();
  expect(screen.getByText('No invalidation condition is currently available.')).toBeVisible();
});

test('shows trader-facing condition copy while preserving the response value',async()=>{
  const raw='new mapped structure and a qualifying setup sequence';
  renderLevels({...payload,what_changes_my_mind:[raw]});
  expect(await screen.findByText('new qualifying structure and setup sequence')).toBeVisible();
  expect(screen.queryByText(raw)).not.toBeInTheDocument();
});
