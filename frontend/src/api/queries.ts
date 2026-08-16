import { useQuery } from '@tanstack/react-query';import { api } from './client';
export const useAnalysis=(symbol:string)=>useQuery({queryKey:['analysis',symbol],queryFn:()=>api.analysis(symbol)});
export const useScanner=(symbols:string[])=>useQuery({queryKey:['scanner',symbols],queryFn:()=>api.scanner(symbols)});
export const useEvidence=()=>useQuery({queryKey:['evidence'],queryFn:api.evidence});
export const useChart=(symbol:string,timeframe:'1m'|'5m'|'15m'|'1h'|'1d',extendedHours:boolean)=>useQuery({queryKey:['chart',symbol,timeframe,extendedHours],queryFn:()=>api.chart(symbol,timeframe,extendedHours)});
