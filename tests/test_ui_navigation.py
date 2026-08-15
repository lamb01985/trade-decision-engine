import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text()
INDEX = (ROOT / "app" / "static" / "index.html").read_text()
STYLES = (ROOT / "app" / "static" / "styles.css").read_text()
PLAYBOOK_STYLES = (ROOT / "app" / "static" / "playbook.css").read_text()


def function_source(name: str, next_name: str) -> str:
    start = APP_JS.index(f"function {name}")
    return APP_JS[start : APP_JS.index(f"function {next_name}", start)]


def open_target_source() -> str:
    start = APP_JS.index("function openTarget")
    return APP_JS[start : APP_JS.index("document.addEventListener('click'", start)]


def test_action_card_maps_existing_backend_state_without_numeric_rules():
    view = function_source("decisionView", "renderOpenedContext")
    assert "x?.action_now" in view
    assert "x?.setup_state" in view
    assert "READY — ${direction}" in view
    assert "TOO LATE — DO NOT CHASE" in view
    assert "INVALIDATED" in view
    assert "missing_confirmation" in view
    assert "minimum_" not in view and "threshold" not in view


def test_ready_bucket_requires_confirmed_actionable_backend_state():
    bucket = function_source("scannerBucket", "scannerNext")
    assert "effectiveStatus!=='LIVE'" in bucket
    assert "['LONG_TRIGGERED','SHORT_TRIGGERED'].includes(action)" in bucket
    assert "['CONFIRMED','LONG_CONFIRMED','SHORT_CONFIRMED'].includes(state)" in bucket
    assert "chase==='ACTIONABLE'" in bucket


def test_wait_bucket_is_the_fallback_for_forming_or_incomplete_setups():
    bucket = function_source("scannerBucket", "scannerNext")
    assert "return confirmed?'ready':'watch'" in bucket
    assert "Wait for required trigger and confirmation." in APP_JS


def test_stale_too_late_and_invalidated_states_cannot_be_ready():
    bucket = function_source("scannerBucket", "scannerNext")
    assert "effectiveStatus!=='LIVE'" in bucket
    assert "'TOO_LATE','INVALIDATED','NO_SETUP','NO_TRADE'" in bucket
    assert "'DO_NOT_CHASE','NO_TRADE'" in bucket
    assert "'TOO_LATE','EXTENDED'" in bucket


def test_closed_market_uses_existing_ui_as_next_session_preparation():
    view = function_source("decisionView", "renderOpenedContext")
    buckets = function_source("renderScannerBuckets", "renderBest")
    render = function_source("renderAnalysis", "levelTitle")
    assert "WATCH NEXT SESSION" in view
    assert "TOO EXTENDED — REASSESS NEXT SESSION" in view
    assert "NEXT SESSION" in buckets and "WATCH" in buckets
    assert "priceLabel=isClosed?'LAST PRICE':'CURRENT PRICE'" in render
    assert "Fresh live analysis must revalidate this plan" in view


def test_closed_to_open_boundary_forces_fresh_analysis():
    assert "setInterval(()=>{if(['dashboard','levels'].includes(active)&&currentAnalysis&&!document.hidden)analyze(true)},60000)" in APP_JS
    assert "currentAnalysis?.market_session==='REGULAR'" not in APP_JS


def test_scanner_buckets_and_empty_ready_state_are_rendered():
    assert "READY NOW" in APP_JS
    assert "FORMING / WATCH" in APP_JS
    assert "IGNORE / NO TRADE" in APP_JS
    assert "NO READY SETUPS RIGHT NOW" in APP_JS
    assert "Nothing currently meets all requirements. Wait for confirmation." in APP_JS


def test_radar_best_and_watchlist_share_cards_and_bucket_path():
    assert "renderScannerBuckets('market-candidates',data.candidates,'Live Radar',data.data_status)" in APP_JS
    assert "renderScannerBuckets('best-list',best,'Best Setups')" in APP_JS
    assert "renderScannerBuckets('watchlist-buckets',[...rows].sort(compareRecommended),'Watchlist')" in APP_JS
    assert 'class="candidate analysis-link ${bucket}"' in APP_JS
    assert 'tabindex="0" role="link"' in APP_JS
    assert "['Enter',' '].includes(event.key)" in APP_JS


def test_click_through_selects_symbol_overview_and_existing_analysis_path():
    navigation = function_source("openAnalysis", "openTarget")
    assert "selected=symbol" in navigation
    assert "$('symbol').value=selected" in navigation
    assert "switchView('dashboard')" in navigation
    assert "scrollIntoView" in navigation
    assert navigation.rstrip().endswith("analyze(true)}")
    assert "fetch(`/api/analyze/${encodeURIComponent(symbol)}?refresh=${force}`)" in APP_JS
    assert APP_JS.count("/api/analyze/") == 1


def test_scanner_context_is_preserved_and_state_change_disclosed():
    navigation = function_source("openAnalysis", "openTarget")
    context = function_source("renderOpenedContext", "renderTradeCheck")
    assert "openedContext={symbol,setup:surface.dataset.setup" in navigation
    assert "YOU OPENED" in context
    assert "SETUP CHANGED SINCE SCANNER SIGNAL" in context
    assert "CURRENT:" in context


def test_pretrade_check_uses_only_backend_decision_facts():
    check = function_source("renderTradeCheck", "renderAnalysis")
    for field in (
        "setup_family",
        "trigger",
        "invalidation",
        "target_1",
        "target_2",
        "structure_5m",
        "structure_15m",
        "structural_risk_reward",
        "setup_state",
        "action_now",
    ):
        assert field in check
    assert "threshold" not in check and "score" not in check


def test_heuristic_score_is_explicitly_not_probability():
    assert "heuristic ranking, not a probability of success" in APP_JS
    assert "HEURISTIC" in APP_JS
    assert "<strong>VALIDATED</strong>" not in APP_JS


def test_nested_controls_and_visual_accessibility():
    assert "interactive&&!interactive.matches('[data-open]')" in APP_JS
    assert ".analysis-link{cursor:pointer" in STYLES
    assert ".candidate.analysis-link:hover" in STYLES
    assert "focus-visible" in STYLES
    assert 'id="gate" class="decision-hero wait" aria-live="polite"' in INDEX


def test_strategy_and_decision_modules_are_not_frontend_dependencies():
    # The presentation consumes API state; it neither imports parameters nor
    # duplicates the engine's numeric strategy thresholds.
    assert "parameters" not in APP_JS
    assert "minimum_structural_risk_reward" not in APP_JS
    assert "chase_threshold_atr" not in APP_JS
    assert "quality_a_score" not in APP_JS


def test_shared_typography_scale_covers_primary_and_secondary_surfaces():
    for token in ("--type-meta", "--type-small", "--type-support", "--type-body", "--type-title", "--type-value"):
        assert token in PLAYBOOK_STYLES
    for surface in (
        ".decision-hero-copy p", ".plan-levels b", ".candidate-top .symbol-link.big",
        ".full-level-row > strong", ".options-decision strong", ".futures-grid strong",
        "table", ".push-settings summary span",
    ):
        assert surface in PLAYBOOK_STYLES


def test_levels_tab_exists_and_matches_finished_overview_style():
    assert '<button data-view="levels">Levels</button>' in INDEX
    assert '<section id="levels" class="view">' in INDEX
    assert 'id="full-level-ladder"' in INDEX
    assert 'id="level-detail"' in INDEX
    assert 'id="confluence-zones"' in INDEX
    assert ".full-level-row" in STYLES
    assert ".active-trade-map" in STYLES


def test_symbol_persists_across_overview_levels_and_options():
    switch = function_source("switchView", "openAnalysis")
    assert "$('options-symbol').value=selected" in switch
    assert "currentAnalysis?.instrument?.symbol===selected" in switch
    assert "renderFullLevels(currentAnalysis)" in switch
    assert "switchView('dashboard')" in APP_JS
    assert "switchView('levels')" in APP_JS


def test_full_levels_are_only_backend_levels_and_current_trade_plan_values():
    source = function_source("fullLevelItems", "itemLabel")
    assert "d.levels.filter" in source
    assert "p.trigger" in source
    assert "p.invalidation" in source
    assert "p.target_1" in source
    assert "p.target_2" in source
    assert "items.push" in source
    assert "swing" not in source.lower()
    assert "support" not in source.lower()
    assert "resistance" not in source.lower()


def test_overview_and_full_levels_share_exact_setup_values():
    overview = function_source("renderLevels", "renderFuturesProbe")
    full = function_source("renderFullLevels", "playbookCard")
    for field in ("trigger", "invalidation", "target_1", "target_2"):
        assert f"p.{field}" in overview
        assert field in full


def test_ladder_is_high_to_low_and_current_price_is_backend_price():
    items = function_source("fullLevelItems", "itemLabel")
    render = function_source("renderFullLevels", "playbookCard")
    assert "items.sort((a,b)=>b.price-a.price)" in items
    assert "d.instrument.timeframes['1m'].price" in items
    assert "types.includes('CURRENT_PRICE')" in render
    assert "full-level-row ${item.setupLabels.length?'setup':''} ${item.types.includes('CURRENT_PRICE')?'current':''}" in render


def test_setup_level_distances_are_arithmetic_from_backend_current_price():
    items = function_source("fullLevelItems", "itemLabel")
    assert "distancePct:(price-current)/current*100" in items
    assert "distancePct:(level.price-current)/current*100" in items


def test_confluence_uses_backend_grouping_without_frontend_tolerance():
    render = function_source("renderFullLevels", "playbookCard")
    assert "d.levels.filter(level=>level.confluence>1)" in render
    assert "level.types" in render
    assert "level.confluence" in render
    assert "Grouped by the backend levels engine" in render
    assert "tolerance" not in render
    assert "threshold" not in render


def test_level_actions_are_limited_to_supported_setup_semantics():
    consequence = function_source("levelConsequence", "showLevelDetail")
    assert "setup-specific confirmation" in consequence
    assert "mapped invalidation condition" in consequence
    assert "first structural target" in consequence
    assert "second structural target" in consequence
    assert "does not assign this level a setup-specific action" in consequence
    assert "take profit" not in consequence.lower()
    assert "position size" not in consequence.lower()


def run_level_action(item: dict, setup_state="WATCHING", action_now="WAIT", data_status="LIVE") -> dict:
    ready = function_source("isReadyDecision", "ageLabel")
    context = function_source("levelDecisionContext", "levelAction")
    action = function_source("levelAction", "showLevelDetail")
    script = f"""
{ready}
{context}
{action}
const item={json.dumps(item)};
const d={{data_status:{json.dumps(data_status)},current_price:100,instrument:{{timeframes:{{'1m':{{price:100}}}}}},decision:{{setup_state:{json.dumps(setup_state)},action_now:{json.dumps(action_now)}}}}};
console.log(JSON.stringify(levelAction(item,d)));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_level_detail_if_then_actions_are_conservative_and_type_specific():
    trigger = run_level_action({"price": 101, "types": [], "setupLabels": ["TRIGGER"]})
    invalidation = run_level_action({"price": 99, "types": [], "setupLabels": ["INVALIDATION"]})
    target_1 = run_level_action({"price": 103, "types": [], "setupLabels": ["TARGET_1"]})
    target_2 = run_level_action({"price": 105, "types": [], "setupLabels": ["TARGET_2"]})
    support = run_level_action({"price": 98, "types": ["5M_SWING_LOW"], "setupLabels": []})
    resistance = run_level_action({"price": 102, "types": ["15M_SWING_HIGH"], "setupLabels": []})
    session = run_level_action({"price": 100.5, "types": ["SESSION_VWAP"], "setupLabels": []})
    current = run_level_action({"price": 100, "types": ["CURRENT_PRICE"], "setupLabels": []})
    assert trigger["result"] == "SETUP MAY BECOME ACTIONABLE"
    assert "chase/extension" in trigger["detail"] and "Continue waiting" in trigger["detail"]
    assert invalidation["result"] == "THESIS INVALID" and "Do not initiate" in invalidation["detail"]
    assert target_1["result"] == "FIRST STRUCTURAL TARGET REACHED"
    assert target_2["result"] == "SECOND STRUCTURAL TARGET REACHED"
    assert support["result"] == "WATCH FOR REACTION" and "not automatically an entry" in support["detail"]
    assert resistance["result"] == "WATCH FOR REJECTION OR BREAKOUT" and "not automatically an entry or short" in resistance["detail"]
    assert session["result"] == "RE-EVALUATE CURRENT SETUP STATE" and "not a generic session-level trade rule" in session["detail"]
    assert current["result"] == "NO ACTION FROM PRICE ALONE"


def test_level_action_respects_authoritative_decision_state():
    item = {"price": 101, "types": [], "setupLabels": ["TRIGGER"]}
    ready = run_level_action(item, "CONFIRMED", "LONG_TRIGGERED")
    late = run_level_action(item, "TOO_LATE", "DO_NOT_CHASE")
    invalidated = run_level_action(item, "INVALIDATED", "NO_TRADE")
    closed = run_level_action(item, "CONFIRMED", "LONG_TRIGGERED", "MARKET_CLOSED")
    assert ready["tone"] == "ready" and "currently actionable" in ready["detail"]
    assert late["result"] == "DO NOT INITIATE THE SETUP" and "Do not chase" in late["detail"]
    assert invalidated["result"] == "DO NOT INITIATE THE SETUP" and "No entry" in invalidated["detail"]
    assert closed["tone"] == "wait" and "Fresh live analysis" in closed["detail"]


def test_level_action_panel_is_visually_prominent_without_new_layout():
    detail = function_source("showLevelDetail", "renderFullLevels")
    assert "IF THIS HAPPENS" in detail and "ACTION" in detail
    assert 'class="level-action-flow"' in detail
    assert ".level-action.ready" in STYLES
    assert ".level-action.wait" in STYLES
    assert ".level-action.stop" in STYLES


def test_view_full_levels_and_back_to_plan_preserve_selected_symbol():
    assert "$('view-full-levels').onclick=()=>switchView('levels')" in APP_JS
    assert "$('back-to-plan').onclick=()=>{switchView('dashboard')" in APP_JS
    assert "selected=" not in APP_JS[APP_JS.index("$('view-full-levels').onclick"):]


def run_price_context(current: float, trigger: float, direction: str) -> dict:
    source = function_source("priceTriggerContext", "decisionView")
    script = f"const fmt=x=>Number(x).toFixed(2);{source};console.log(JSON.stringify(priceTriggerContext({current},{trigger},{json.dumps(direction)})))"
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_current_price_is_first_class_in_hero_and_trade_plan():
    render = function_source("renderAnalysis", "levelTitle")
    assert '<span>${priceLabel}</span><b>$${fmt(currentPrice)}</b>' in render
    assert "priceLabel=isClosed?'LAST PRICE':'CURRENT PRICE'" in render
    assert '<div class="plan-levels"><div class="current-price-metric"><span>${priceLabel}</span>' in render
    assert "provider_timestamp" in render
    assert "analysis_timestamp" in render
    assert "Trigger position alone does not confirm a trade." in render


def test_long_distance_to_trigger_wording_is_directionally_correct():
    beyond = run_price_context(305.18, 304.27, "LONG")
    waiting = run_price_context(303.90, 304.27, "LONG")
    assert "+0.30% beyond trigger" in beyond["summary"]
    assert "already 0.30% beyond" in beyond["detail"]
    assert "0.12% below trigger" in waiting["summary"]
    assert "below the planned trigger" in waiting["detail"]


def test_short_distance_to_trigger_wording_is_directionally_correct():
    beyond = run_price_context(303.36, 304.27, "SHORT")
    waiting = run_price_context(304.64, 304.27, "SHORT")
    assert "+0.30% beyond trigger" in beyond["summary"]
    assert "already 0.30% beyond" in beyond["detail"]
    assert "0.12% above trigger" in waiting["summary"]
    assert "above the planned trigger" in waiting["detail"]


def test_overview_and_levels_share_backend_current_price_anchor():
    overview = function_source("renderAnalysis", "levelTitle")
    full_levels = function_source("renderFullLevels", "playbookCard")
    assert "one=q.timeframes['1m']" in overview
    assert "priceTriggerContext(currentPrice" in overview
    assert "d.current_price??d.instrument.timeframes['1m'].price" in full_levels


def run_shared_card_navigation(symbol: str, source: str) -> dict:
    open_analysis = function_source("openAnalysis", "openTarget")
    open_target = open_target_source()
    script = f"""
let selected='QQQ',openedContext=null,active='market',analyzeCalls=0,viewCalls=[];
const nodes={{symbol:{{value:''}},gate:{{scrollIntoView:()=>{{}}}}}};
const $=id=>nodes[id];
const localStorage={{values:{{}},setItem(key,value){{this.values[key]=value}}}};
const switchView=view=>{{active=view;viewCalls.push(view)}};
const analyze=force=>{{if(force!==true)throw Error('scanner click did not force revalidation');analyzeCalls++}};
const requestAnimationFrame=callback=>callback();
{open_analysis}
{open_target}
const surface={{dataset:{{open:{json.dumps(symbol)},setup:'TREND_PULLBACK',direction:'LONG',state:'CONFIRMED',source:{json.dumps(source)}}},closest:()=>surface}};
const child={{closest:selector=>selector.includes('button,a,input')?null:surface}};
const target=openTarget({{target:child}});
openAnalysis(target);
console.log(JSON.stringify({{selected,active,input:nodes.symbol.value,analyzeCalls,viewCalls,stored:localStorage.values['tde-symbol'],openedContext}}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_live_radar_card_uses_shared_overview_navigation():
    result = run_shared_card_navigation("AMGN", "Live Radar RADAR")
    assert result["selected"] == "AMGN"
    assert result["active"] == "dashboard"
    assert result["input"] == "AMGN"
    assert result["stored"] == "AMGN"
    assert result["analyzeCalls"] == 1
    assert result["openedContext"]["symbol"] == "AMGN"


def test_best_setup_card_uses_same_shared_overview_navigation():
    result = run_shared_card_navigation("VRSK", "Best Setups")
    assert result["selected"] == "VRSK"
    assert result["viewCalls"] == ["dashboard"]
    assert result["input"] == "VRSK"
    assert result["analyzeCalls"] == 1


def test_radar_transition_cards_expose_navigation_contract():
    source = function_source("marketAlertCard", "updateRadarStatus")
    assert 'class="market-alert analysis-link ${alert.alert_type.toLowerCase()}"' in source
    assert 'data-open="${esc(alert.symbol)}"' in source
    assert 'data-source="Live Radar ${esc(name(alert.alert_type))}"' in source
    assert 'tabindex="0" role="link"' in source
    assert "data.alerts.map(marketAlertCard)" in APP_JS


def test_returning_to_scanner_does_not_mutate_shared_symbol():
    switch = function_source("switchView", "openAnalysis")
    assert "if(view==='market'){startRadar(false)}" in switch
    assert "selected=" not in switch
    assert "$('options-symbol').value=selected" in switch


def test_nested_non_navigation_controls_are_ignored_behaviorally():
    open_target = open_target_source()
    script = f"""
{open_target}
const control={{matches:()=>false}};
const child={{closest:selector=>selector.includes('button,a,input')?control:{{dataset:{{open:'AMGN'}}}}}};
console.log(openTarget({{target:child}})===null?'ignored':'opened');
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "ignored"
