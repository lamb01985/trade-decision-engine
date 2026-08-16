import { analysisSchema, scannerSchema } from './schemas';
import { analysisFixture, chartFixture, evidenceFixture, scannerFixture } from './fixtures';
import type { Analysis, ChartSnapshot, EvidenceResult, ScannerResult, Sourced } from '../domain/types';

export class ApiError extends Error{constructor(message:string,public status:number,public code?:string){super(message)}}
const useFixtures=import.meta.env.VITE_USE_DEV_FIXTURES==='true';
const base=(import.meta.env.VITE_API_BASE_URL||'').replace(/\/$/,'');
async function request(path:string,init?:RequestInit):Promise<unknown>{const response=await fetch(`${base}${path}`,{headers:{Accept:'application/json',...init?.headers},...init});const body=await response.json().catch(()=>null);if(!response.ok){const message=body?.error?.message||body?.detail||`Request failed (${response.status})`;throw new ApiError(message,response.status,body?.error?.code)}return body}
export const api={
  fixturesEnabled:useFixtures,
  async analysis(symbol:string):Promise<Sourced<Analysis>>{if(useFixtures)return{source:'fixture',data:{...analysisFixture,symbol}};return{source:'live',data:analysisSchema.parse(await request(`/api/v1/analysis/${encodeURIComponent(symbol)}`))}},
  async scanner(symbols:string[]):Promise<Sourced<ScannerResult>>{if(useFixtures)return{source:'fixture',data:scannerFixture};return{source:'live',data:scannerSchema.parse(await request('/api/v1/scanner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols})}))}},
  async evidence():Promise<Sourced<EvidenceResult>>{if(useFixtures)return{source:'fixture',data:evidenceFixture};try{const raw=await request('/api/v1/research/evidence');return{source:'live',data:{available:true,metrics:mapEvidence(raw)}}}catch(error){if(error instanceof ApiError&&[404,503].includes(error.status))return{source:'live',data:{available:false,metrics:null,reason:error.message}};throw error}},
  async chart(symbol:string):Promise<Sourced<ChartSnapshot>>{if(useFixtures)return{source:'fixture',data:{...chartFixture,symbol}};return{source:'live',data:{symbol,bars:[],overlays:[],provider_timestamp:null,data_status:'DATA_ERROR'}}}
};
function mapEvidence(raw:unknown):EvidenceResult['metrics']{const artifact=raw as Record<string,unknown>;const aggregates=Array.isArray(artifact.aggregates)?artifact.aggregates:[];if(!aggregates.length)return null;const a=aggregates[0] as Record<string,unknown>;const n=(key:string)=>typeof a[key]==='number'?a[key] as number:null;return{sample_size:n('sample_size'),sample_quality_label:typeof a.sample_quality_label==='string'?a.sample_quality_label:null,outcome_distribution:null,realized_r:null,median_mfe:n('median_mfe'),median_mae:n('median_mae'),mfe_r:n('median_mfe_r'),mae_r:n('median_mae_r'),threshold_reach_rates:{},time_to_threshold_minutes:{},structural_room:n('structural_room'),room_to_run_context:null,provenance:{cohort:(artifact.filters as Record<string,string>)||{},slice:{},contract_version:typeof artifact.analytics_contract_version==='string'?artifact.analytics_contract_version:undefined}}}
