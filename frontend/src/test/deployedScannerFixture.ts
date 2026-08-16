// Captured from POST /api/v1/scanner with the deployed 13-symbol watchlist on 2026-08-16.
export const deployedScannerResponse={
  engine_version:'2.0.0',parameter_version:'1.0.0',setup_rules_version:'1.0.0',
  market_status:{data_status:'MARKET_CLOSED_LAST_SESSION',session:'CLOSED',provider_timestamp:'2026-08-14T23:57:12.068971Z',analysis_timestamp:'2026-08-16T18:38:07.908665Z',data_age_seconds:153655,actionable:false},
  candidates:[{signal_id:'sig_2a7632c5075e1d33a183bb2a',symbol:'SMH',setup_family:'TREND_PULLBACK',direction:'LONG',lifecycle:'CONFIRMED',quality:'A',score:86,current_or_last_price:587.76,trigger:587.555,distance_to_trigger_pct:-0.0348781815707161,invalidation:586.7846820841341,target_1:589.07,target_2:590.1966666666667,structural_risk_reward:1.9667204524216568,chase_state:'ACTIONABLE',reasons:['Aligned trend pulls to EMA9/structure, holds, then resumes with volume.'],decision_intelligence:null}],
  universe_size:13,symbols_attempted:13,symbols_successful:13,symbols_skipped:[],filtered_illiquid:0,scan_duration_seconds:3.343,notifications_sent:0,
} as const;
