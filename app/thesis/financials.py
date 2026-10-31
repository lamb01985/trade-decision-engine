"""Annual US-GAAP screen. No price forecasts, AI arithmetic or trade signals."""
from datetime import date, datetime, timezone
import math
import re
import uuid

POLICY = 'thesis-annual-financial-v1'
CONCEPTS = {
    'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'),
    'net_income': ('NetIncomeLoss', 'ProfitLoss'),
    'cfo': ('NetCashProvidedByUsedInOperatingActivities',),
    'capex': ('PaymentsToAcquirePropertyPlantAndEquipment',),
    'cash': ('CashAndCashEquivalentsAtCarryingValue',),
    'assets': ('Assets',), 'liabilities': ('Liabilities',),
    'equity': ('StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),
    'current_assets': ('AssetsCurrent',), 'current_liabilities': ('LiabilitiesCurrent',),
    'debt_current': ('LongTermDebtCurrent', 'LongTermDebtAndCapitalLeaseObligationsCurrent'),
    'debt_noncurrent': ('LongTermDebtNoncurrent',), 'eps': ('EarningsPerShareDiluted',),
}
DURATION = {'revenue', 'net_income', 'cfo', 'capex', 'eps'}
SIGNED = {'net_income', 'cfo', 'equity', 'eps'}


def number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('INVALID_DATE')
    return date.fromisoformat(value)


def eligible(row, as_of):
    try:
        return (row.get('form') in ('10-K', '10-K/A') and day(row['filed']) <= as_of
                and day(row['end']) <= as_of and number(row['val'])
                and bool(re.fullmatch(r'\d{10}-\d{2}-\d{6}', row.get('accn', ''))))
    except (ValueError, KeyError, TypeError):
        return False


def annual(row):
    try:
        return 330 <= (day(row['end']) - day(row['start'])).days <= 380
    except (ValueError, KeyError, TypeError):
        return False


def extract(payload, key, start, end, as_of, cik, fixture=False):
    unit = 'USD/shares' if key == 'eps' else 'USD'
    candidates = []
    for alias in CONCEPTS[key]:
        rows = payload.get('facts', {}).get('us-gaap', {}).get(alias, {}).get('units', {}).get(unit, [])
        for row in rows:
            if not eligible(row, as_of) or row['end'] != end:
                continue
            if key in DURATION and (row.get('start') != start or not annual(row)):
                continue
            if key not in DURATION and row.get('start') is not None:
                continue
            if key not in SIGNED and row['val'] < 0:
                continue
            candidates.append((row['filed'], row['accn'], alias, row))
    if not candidates:
        return None
    rank = max((filed, accession) for filed, accession, _, _ in candidates)
    latest = [(alias, row) for filed, accession, alias, row in candidates if (filed, accession) == rank]
    if len({row['val'] for _, row in latest}) != 1:
        return None  # Do not hide inconsistent same-rank facts behind alias priority.
    alias, row = min(latest, key=lambda pair: CONCEPTS[key].index(pair[0]))
    accession = row['accn']
    source = 'fixture://thesis-demo' if fixture else f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{accession}-index.html'
    return dict(value=row['val'], unit=unit, concept=alias, start=row.get('start'), end=end,
                filed=row['filed'], accession=accession, source_url=source)


def metric(value=None, unit='USD', formula='Reported annual fact', inputs=()):
    return dict(value=round(value, 8) if number(value) else None, unit=unit, formula=formula,
                inputs=[item for item in inputs if item is not None])


def assessment(state, reason, assumption, counterargument):
    return dict(state=state, reason=reason, assumptions=[assumption], counterarguments=[counterargument])


def company_report(payload, issuer, as_of, fixture=False):
    if not re.fullmatch(r'\d{10}', issuer.get('cik', '')) or str(payload.get('cik', '')).zfill(10) != issuer['cik']:
        raise ValueError('COMPANY_IDENTITY_MISMATCH')
    as_of = day(as_of) if isinstance(as_of, str) else as_of
    periods = set()
    for alias in CONCEPTS['revenue']:
        for row in payload.get('facts', {}).get('us-gaap', {}).get(alias, {}).get('units', {}).get('USD', []):
            if eligible(row, as_of) and annual(row):
                periods.add((row['start'], row['end']))
    # Different annual starts for the same end are ambiguous; never choose arbitrarily.
    ends = sorted({end for _, end in periods}, reverse=True)[:4]
    records = []
    for end in ends:
        starts = {start for start, finish in periods if finish == end}
        if len(starts) != 1:
            if end == ends[0]:
                records = []
                break
            continue
        start = next(iter(starts))
        records.append(dict(start=start, end=end, facts={key: extract(payload, key, start, end, as_of, issuer['cik'], fixture) for key in CONCEPTS}))
    latest = records[0]['facts'] if records else {}
    def fact(key, index=0):
        return records[index]['facts'].get(key) if index < len(records) else None
    def val(key, index=0):
        return (fact(key, index) or {}).get('value')
    def ratio(numerator, denominator, scale=1):
        try:
            value = numerator / denominator * scale if number(numerator) and number(denominator) and denominator > 0 else None
            return value if number(value) else None
        except (OverflowError, ZeroDivisionError):
            return None
    metrics = {key: metric(val(key), 'USD/shares' if key == 'eps' else 'USD', inputs=[fact(key)]) for key in CONCEPTS}
    def computed(key, value, unit, formula, evidence):
        metrics[key] = metric(value, unit, formula, evidence)
    computed('fcf', val('cfo') - val('capex') if val('cfo') is not None and val('capex') is not None else None,
             'USD', 'Operating cash flow - purchases of property, plant and equipment', [fact('cfo'), fact('capex')])
    debt = val('debt_current') + val('debt_noncurrent') if val('debt_current') is not None and val('debt_noncurrent') is not None else None
    computed('debt', debt, 'USD', 'Observed current + noncurrent long-term debt (limited debt proxy)', [fact('debt_current'), fact('debt_noncurrent')])
    computed('net_margin', ratio(val('net_income'), val('revenue'), 100), 'percent', 'Net income / revenue × 100', [fact('net_income'), fact('revenue')])
    computed('cash_conversion', ratio(val('cfo'), val('net_income')), 'ratio', 'Operating cash flow / positive net income', [fact('cfo'), fact('net_income')])
    computed('current_ratio', ratio(val('current_assets'), val('current_liabilities')), 'ratio', 'Current assets / positive current liabilities', [fact('current_assets'), fact('current_liabilities')])
    computed('debt_assets', ratio(debt, val('assets')), 'ratio', 'Limited debt proxy / positive assets', [fact('debt_current'), fact('debt_noncurrent'), fact('assets')])
    def adjacent(i):
        return i+1 < len(records) and 330 <= (day(records[i]['end'])-day(records[i+1]['end'])).days <= 400
    growth = (ratio(val('revenue'), val('revenue', 1), 100) - 100) if adjacent(0) and val('revenue') is not None and ratio(val('revenue'), val('revenue', 1)) is not None else None
    prior = (ratio(val('revenue', 1), val('revenue', 2), 100) - 100) if adjacent(1) and val('revenue', 1) is not None and ratio(val('revenue', 1), val('revenue', 2)) is not None else None
    acceleration = growth - prior if growth is not None and prior is not None else None
    prior_margin = ratio(val('net_income', 1), val('revenue', 1), 100) if adjacent(0) else None
    margin_change = metrics['net_margin']['value'] - prior_margin if metrics['net_margin']['value'] is not None and prior_margin is not None else None
    computed('revenue_yoy', growth, 'percent', 'Consecutive annual revenue / prior annual revenue - 1, × 100', [fact('revenue'), fact('revenue', 1)])
    computed('prior_revenue_yoy', prior, 'percent', 'Prior consecutive annual revenue growth', [fact('revenue', 1), fact('revenue', 2)])
    computed('growth_acceleration', acceleration, 'percentage_points', 'Revenue YoY - prior revenue YoY', [fact('revenue'), fact('revenue', 1), fact('revenue', 2)])
    computed('margin_change', margin_change, 'percentage_points', 'Net margin - prior consecutive annual net margin', [fact('net_income'), fact('revenue'), fact('net_income', 1), fact('revenue', 1)])
    unknown = lambda reason: assessment('UNKNOWN', reason, 'No missing inputs are treated as zero.', 'Additional filings or context can change this assessment.')
    assessments = {key: unknown('Required annual evidence is incomplete.') for key in ('quality','profitability','growth','financial_health','valuation','entry')}
    income, conversion, margin = val('net_income'), metrics['cash_conversion']['value'], metrics['net_margin']['value']
    if income is not None and val('cfo') is not None and (income <= 0 or conversion is not None):
        state = 'RED' if income <= 0 else 'GREEN' if conversion >= 1 else 'YELLOW' if conversion >= .7 else 'RED'
        assessments['quality'] = assessment(state, 'Financial-quality proxy: earnings cash conversion, not a moat assessment.', 'Positive earnings and annual operating cash flow; no qualitative quality inference.', 'Working-capital timing and one-time items can distort cash conversion.')
    if margin is not None:
        assessments['profitability'] = assessment('GREEN' if margin >= 10 else 'YELLOW' if margin > 0 else 'RED', f'Annual net margin {margin:.2f}%.', 'Net income and revenue use the same fiscal period and USD.', 'Unusual gains or expenses can distort reported margins.')
    if growth is not None and acceleration is not None:
        assessments['growth'] = assessment('GREEN' if growth >= 10 and acceleration >= 0 else 'YELLOW' if growth > 0 else 'RED', f'Revenue growth {growth:.2f}%; acceleration {acceleration:.2f} points.', 'Three consecutive annual periods; no quarterly or segment forecast.', 'Acquisitions, currency and fiscal changes can explain growth.')
    current, leverage = metrics['current_ratio']['value'], metrics['debt_assets']['value']
    if current is not None and leverage is not None and val('equity') is not None:
        state = 'RED' if current < 1 or val('equity') <= 0 else 'GREEN' if current >= 1.5 and leverage <= .5 else 'YELLOW'
        assessments['financial_health'] = assessment(state, 'Current ratio, observed debt/assets and equity screen.', 'Limited reported debt proxy; off-balance-sheet commitments not assessed.', 'Liquidity timing, refinancing and industry structure require filing-note review.')
    assessments['valuation'] = unknown('Verified market price, forecasts and a valuation model are not supplied.')
    assessments['entry'] = unknown('Options/stock liquidity, borrow and technical entry evidence are not supplied.')
    stale = bool(records and (as_of-day(records[0]['end'])).days > 550)
    sic = issuer.get('sic')
    unsupported = sic is not None and 6000 <= int(sic) < 6800
    if stale or unsupported:
        for key in ('quality','profitability','growth','financial_health'):
            assessments[key] = unknown('Annual evidence is stale.' if stale else 'Financial-sector/REIT accounting requires a separate model.')
    dims = [assessments[key]['state'] for key in ('quality','profitability','growth','financial_health')]
    known = sum(state != 'UNKNOWN' for state in dims)
    long_score = sum({'GREEN':25,'YELLOW':12.5,'RED':0}[state] for state in dims) if known == 4 else None
    conditions = [(growth < 0 if growth is not None else None,30), (acceleration < -5 if acceleration is not None else None,20),
                  (margin_change < -2 if margin_change is not None else None,20), (metrics['fcf']['value'] < 0 if metrics['fcf']['value'] is not None else None,15),
                  (assessments['financial_health']['state']=='RED' if assessments['financial_health']['state']!='UNKNOWN' else None,15)]
    coverage = sum(value is not None for value,_ in conditions)
    short_score = sum(weight for value,weight in conditions if value) if coverage == 5 and not stale and not unsupported else None
    warnings = ['Annual statements only; interim results, valuation, management quality and execution are not assessed.', 'Financial screen scores are unvalidated research heuristics, not expected returns or bankruptcy probabilities.']
    if sic is None:
        warnings.append('Sector is not classified; this generic model may not apply.')
    if fixture:
        warnings.append('Synthetic development fixture. Not actual company financial data.')
    if stale:
        warnings.append('Latest annual period exceeds the 550-day analysis-date limit.')
    return dict(schema_version='thesis-company-v1',request_id=str(uuid.uuid4()),profile='',generated_at=datetime.now(timezone.utc).isoformat(),as_of=as_of.isoformat(),
                source_type='DEVELOPMENT_FIXTURE' if fixture else 'SEC_EDGAR',issuer=issuer,periods=records,metrics=metrics,assessments=assessments,
                scores=dict(long_financial=long_score,short_deterioration=short_score,long_coverage=known/4,short_coverage=coverage/5,policy_version=POLICY),
                quality=dict(freshness='STALE' if stale else 'ANNUAL_ONLY' if records else 'INSUFFICIENT',confidence='MEDIUM' if known==4 and sic is not None else 'LOW',warnings=warnings),readiness='NOT_EVALUATED')
