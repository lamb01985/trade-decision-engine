import { useQuery } from '@tanstack/react-query';import { api } from './client';
export const useAnalysis=(symbol:string)=>useQuery({queryKey:['analysis',symbol],queryFn:()=>api.analysis(symbol)});
export const useScanner=(symbols:string[])=>useQuery({queryKey:['scanner',symbols],queryFn:()=>api.scanner(symbols)});
export const useEvidence=()=>useQuery({queryKey:['evidence'],queryFn:api.evidence});
export const useChart=(symbol:string)=>useQuery({queryKey:['chart',symbol],queryFn:()=>api.chart(symbol)});
