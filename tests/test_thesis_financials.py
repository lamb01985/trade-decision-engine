from copy import deepcopy
from datetime import date
import pytest
from app.thesis.financials import company_report,CONCEPTS
from app.thesis.fixtures import demo_company


def report(symbol='DEMOA',change=None,as_of='2026-10-31'):
    issuer,data=demo_company(symbol)
    if change:change(issuer,data)
    return company_report(data,issuer,as_of,True)


def rows(data,key):return data['facts']['us-gaap'][CONCEPTS[key][0]]['units']['USD/shares' if key=='eps' else 'USD']


def test_two_sided_deterministic_scores_and_explicit_unavailable_execution():
    healthy,weak=report(),report('DEMOB')
    assert healthy['scores']['long_financial']==100 and healthy['scores']['short_deterioration']==0
    assert weak['scores']['long_financial']==0 and weak['scores']['short_deterioration']==100
    for result in (healthy,weak):
        assert result['assessments']['valuation']['state']=='UNKNOWN'
        assert result['assessments']['entry']['state']=='UNKNOWN'
        assert result['readiness']=='NOT_EVALUATED' and result['source_type']=='DEVELOPMENT_FIXTURE'
        assert result['metrics']['revenue']['inputs'][0]['source_url'].startswith('fixture:')


def test_fcf_uses_capital_purchases_not_all_investing_cash_flow():
    def change(_,data):data['facts']['us-gaap']['NetCashProvidedByUsedInInvestingActivities']={'units':{'USD':[{'val':-999999999}]}}
    result=report(change=change)
    assert result['metrics']['fcf']['value']==21450000
    assert len(result['metrics']['fcf']['inputs'])==2


@pytest.mark.parametrize('value',[None,True,float('nan'),float('inf'),-10])
def test_invalid_capex_is_unknown_and_missing_coverage_never_rescaled(value):
    def change(_,data):rows(data,'capex')[-1]['val']=value
    result=report(change=change)
    assert result['metrics']['fcf']['value'] is None
    assert result['scores']['short_deterioration'] is None
    assert result['scores']['short_coverage']==.8


def test_missing_health_blocks_long_score_without_renormalizing():
    def change(_,data):data['facts']['us-gaap'].pop('LongTermDebtCurrent')
    result=report(change=change)
    assert result['scores']['long_financial'] is None and result['scores']['long_coverage']==.75


def test_filing_asof_restated_and_future_evidence():
    def change(_,data):
        row=deepcopy(rows(data,'revenue')[-1]);row.update(val=999999999,filed='2027-02-01',form='10-K/A',accn='0000000001-27-000001');rows(data,'revenue').append(row)
    assert report(change=change)['metrics']['revenue']['value']==195000000
    historical=report(as_of='2025-06-01')
    assert historical['periods'][0]['end']=='2024-12-31'
    assert all(item['filed']<='2025-06-01' for p in historical['periods'] for item in p['facts'].values() if item)


def test_newest_eligible_amendment_wins():
    def change(_,data):
        row=deepcopy(rows(data,'revenue')[-1]);row.update(val=200000000,filed='2026-03-01',form='10-K/A',accn='0000000001-26-000002');rows(data,'revenue').append(row)
    assert report(change=change)['metrics']['revenue']['value']==200000000


def test_same_rank_alias_disagreement_is_not_silently_selected():
    def change(_,data):
        row=deepcopy(rows(data,'revenue')[-1]);row['val']+=1
        data['facts']['us-gaap']['Revenues']={'units':{'USD':[row]}}
    result=report(change=change)
    assert result['metrics']['revenue']['value'] is None
    assert result['scores']['long_financial'] is None


def test_basic_eps_never_masquerades_as_diluted():
    def change(_,data):data['facts']['us-gaap']['EarningsPerShareBasic']=data['facts']['us-gaap'].pop('EarningsPerShareDiluted')
    assert report(change=change)['metrics']['eps']['value'] is None


def test_unit_mismatch_and_quarterly_duration_are_rejected():
    def change(_,data):
        fact=data['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']
        fact['units']['EUR']=fact['units'].pop('USD')
    assert report(change=change)['quality']['freshness']=='INSUFFICIENT'
    def quarter(_,data):rows(data,'revenue')[-1]['start']='2025-10-01'
    assert report(change=quarter)['periods'][0]['end']=='2024-12-31'


def test_nonconsecutive_years_block_growth_instead_of_compressing_missing_periods():
    def change(_,data):rows(data,'revenue').pop(2)
    assert report(change=change)['metrics']['revenue_yoy']['value'] is None


def test_negative_equity_precedes_green_health_thresholds():
    def change(_,data):rows(data,'equity')[-1]['val']=-1
    assert report(change=change)['assessments']['financial_health']['state']=='RED'


@pytest.mark.parametrize('income',[0,-1])
def test_nonpositive_income_has_no_ratio_but_red_quality(income):
    def change(_,data):rows(data,'net_income')[-1]['val']=income
    result=report(change=change)
    assert result['metrics']['cash_conversion']['value'] is None
    assert result['assessments']['quality']['state']=='RED'


def test_zero_liability_is_not_infinite_liquidity():
    def change(_,data):rows(data,'current_liabilities')[-1]['val']=0
    assert report(change=change)['metrics']['current_ratio']['value'] is None


def test_staleness_uses_analysis_date_and_sector_gate():
    assert report(as_of='2028-10-31')['scores']['long_financial'] is None
    assert report(as_of='2025-06-01')['quality']['freshness']=='ANNUAL_ONLY'
    def bank(issuer,_):issuer['sic']=6021
    assert report(change=bank)['scores']['long_financial'] is None


def test_identity_and_finite_value_boundaries():
    issuer,data=demo_company('DEMOA');data['cik']=2
    with pytest.raises(ValueError,match='IDENTITY'):company_report(data,issuer,date(2026,10,31))
