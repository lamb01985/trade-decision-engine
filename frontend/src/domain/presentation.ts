export type PresentationState='READY'|'WAIT'|'MONITOR'|'INVALID'|'PASS';

export function presentSetupState(raw:string|null|undefined, actionable:boolean):PresentationState{
  const state=(raw||'MONITOR').toUpperCase();
  if(state==='INVALID')return 'INVALID';
  if(state==='PASS')return 'PASS';
  if(!actionable&&['READY','CONFIRMED','TRIGGERED'].includes(state))return 'WAIT';
  if(actionable&&['READY','CONFIRMED','TRIGGERED'].includes(state))return 'READY';
  if(['WAIT','FORMING'].includes(state))return 'WAIT';
  return 'MONITOR';
}

export const friendlyState=(value:string|null|undefined)=>(value||'Unavailable').replaceAll('_',' ');

export const friendlyCondition=(value:string)=>value==='new mapped structure and a qualifying setup sequence'?'new qualifying structure and setup sequence':value;
