"""Small, single-owner SEC source. No browser URLs, credentials or Massive calls."""
import asyncio
from collections import OrderedDict
import json
import re
import time
import httpx
from app.thesis.financials import company_report


class ThesisError(Exception):
    def __init__(self, code, status=503):
        self.code,self.status=code,status
        super().__init__(code)


class SecSource:
    REQUEST_TIMEOUT = 10
    def __init__(self, user_agent, client=None, interval=1.0):
        if not isinstance(user_agent,str) or not re.fullmatch(r'[\x20-\x7e]{8,256}',user_agent) or '@' not in user_agent:
            raise ThesisError('SEC_CONTACT_UNCONFIGURED')
        self.user_agent=user_agent
        self.client=client or httpx.AsyncClient(follow_redirects=False)
        self.interval=interval
        self.lock=asyncio.Lock()
        self.last_start=0.0
        self.cache=OrderedDict()

    async def get(self,url):
        if not re.fullmatch(r'https://(?:www\.sec\.gov/files/company_tickers\.json|data\.sec\.gov/submissions/CIK[0-9]{10}\.json|data\.sec\.gov/api/xbrl/companyfacts/CIK[0-9]{10}\.json)',url):
            raise ThesisError('INVALID_SEC_RESOURCE',400)
        cached=self.cache.get(url)
        if cached and cached[0]>time.monotonic():return cached[1]
        async with self.lock:
            cached=self.cache.get(url)
            if cached and cached[0]>time.monotonic():return cached[1]
            await asyncio.sleep(max(0,self.interval-(time.monotonic()-self.last_start)))
            async def read():
                self.last_start=time.monotonic()
                async with self.client.stream('GET',url,headers={'User-Agent':self.user_agent,'Accept':'application/json'},timeout=10) as response:
                    if response.status_code!=200:raise ThesisError('SEC_UNAVAILABLE')
                    content=bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content)>8*1024*1024:raise ThesisError('SEC_RESPONSE_TOO_LARGE',502)
                    try:data=json.loads(content)
                    except (ValueError,UnicodeError):raise ThesisError('INVALID_SEC_RESPONSE',502) from None
                    if not isinstance(data,dict):raise ThesisError('INVALID_SEC_RESPONSE',502)
                    return data
            try:data=await asyncio.wait_for(read(),self.REQUEST_TIMEOUT)
            except asyncio.TimeoutError:raise ThesisError('SEC_DEADLINE',504) from None
            except httpx.HTTPError:raise ThesisError('SEC_UNAVAILABLE') from None
            self.cache[url]=(time.monotonic()+(86400 if 'company_tickers' in url else 300),data)
            self.cache.move_to_end(url)
            while len(self.cache)>8:self.cache.popitem(last=False)
            return data

    async def close(self):await self.client.aclose()


class ThesisService:
    COMPANY_TIMEOUT = 25
    def __init__(self,source=None,fixture=False):
        self.source,self.fixture=source,fixture
        self.pending={}

    async def company(self,symbol,as_of):
        key=(symbol,as_of.isoformat())
        if key not in self.pending:
            if len(self.pending)>=4:raise ThesisError('THESIS_BUSY')
            task=asyncio.create_task(asyncio.wait_for(self._company(symbol,as_of),self.COMPANY_TIMEOUT))
            self.pending[key]=task
            task.add_done_callback(lambda done: self.pending.pop(key,None) if self.pending.get(key) is done else None)
        try:return await asyncio.shield(self.pending[key])
        except asyncio.TimeoutError:raise ThesisError('SEC_DEADLINE',504) from None

    async def _company(self,symbol,as_of):
        if self.fixture:
            from app.thesis.fixtures import demo_company
            try:issuer,payload=demo_company(symbol)
            except ValueError:raise ThesisError('COMPANY_NOT_FOUND',404) from None
        else:
            index=await self.source.get('https://www.sec.gov/files/company_tickers.json')
            matches=[row for row in index.values() if isinstance(row,dict) and row.get('ticker')==symbol]
            if len(matches)!=1:raise ThesisError('COMPANY_NOT_FOUND',404)
            raw_cik=matches[0].get('cik_str')
            if isinstance(raw_cik,bool) or not str(raw_cik).isdigit() or not 0<int(raw_cik)<10000000000:raise ThesisError('INVALID_SEC_RESPONSE',502)
            cik=str(raw_cik).zfill(10)
            meta=await self.source.get(f'https://data.sec.gov/submissions/CIK{cik}.json')
            if str(meta.get('cik','')).zfill(10)!=cik:raise ThesisError('COMPANY_IDENTITY_MISMATCH',502)
            payload=await self.source.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json')
            sic=meta.get('sic');sic=int(sic) if isinstance(sic,(str,int)) and not isinstance(sic,bool) and re.fullmatch(r'\d{4}',str(sic)) else None
            issuer=dict(symbol=symbol,cik=cik,name=str(meta.get('name') or matches[0].get('title') or symbol)[:256],sic=sic)
        try:return company_report(payload,issuer,as_of,self.fixture)
        except (ValueError,KeyError,TypeError,OverflowError):raise ThesisError('INVALID_SEC_RESPONSE',502) from None

    async def close(self):
        tasks=list(self.pending.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.source:await self.source.close()
