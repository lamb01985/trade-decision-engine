"""Explicit synthetic development source facts, never used by the live SEC adapter."""
from app.thesis.financials import CONCEPTS, DURATION


def demo_company(symbol):
    if symbol not in ('DEMOA', 'DEMOB'):
        raise ValueError('COMPANY_NOT_FOUND')
    weak = symbol == 'DEMOB'
    cik = '0000000002' if weak else '0000000001'
    issuer = dict(symbol=symbol,cik=cik,name='Synthetic Deterioration Co.' if weak else 'Synthetic Compounding Co.',sic=7372)
    payload = dict(cik=int(cik),facts={'us-gaap':{}})
    revenues = [200,190,165,130] if weak else [100,120,150,195]
    for index, year in enumerate(range(2022,2026)):
        revenue = revenues[index]*1000000
        income = ([20,12,-3,-20][index]*1000000) if weak else revenue*.12
        values = dict(revenue=revenue,net_income=income,cfo=([10,5,-10,-25][index]*1000000) if weak else revenue*.15,
                      capex=8000000 if weak else revenue*.04,cash=5000000 if weak else 80000000,
                      assets=200000000,liabilities=230000000 if weak else 60000000,equity=-30000000 if weak else 140000000,
                      current_assets=20000000 if weak else 100000000,current_liabilities=50000000 if weak else 40000000,
                      debt_current=20000000 if weak else 10000000,debt_noncurrent=180000000 if weak else 20000000,eps=income/10000000)
        for key,value in values.items():
            alias=CONCEPTS[key][0];unit='USD/shares' if key=='eps' else 'USD'
            rows=payload['facts']['us-gaap'].setdefault(alias,{'units':{unit:[]}})['units'][unit]
            row=dict(val=value,end=f'{year}-12-31',filed=f'{year+1}-02-15',form='10-K',accn=f'{cik}-{str(year+1)[2:]}-000001',fy=year,fp='FY')
            if key in DURATION:row['start']=f'{year}-01-01'
            rows.append(row)
    return issuer,payload
