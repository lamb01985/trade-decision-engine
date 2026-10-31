"""Private Thesis company resource, independent of Massive quote configuration."""
import hmac
import os
import re
from datetime import date,datetime,timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.thesis.financials import day
from app.thesis.sec import SecSource,ThesisService,ThesisError

router=APIRouter()
_service=None
_configuration=None


def fixture_mode():
    requested=os.getenv('THESIS_DEV_FIXTURES')=='true'
    if requested and not (os.getenv('NODE_ENV')=='development' and os.getenv('APP_STORAGE_MODE')=='local' and not os.getenv('VERCEL')):
        raise ThesisError('FIXTURE_MODE_FORBIDDEN')
    return requested


@router.get('/api/v1/thesis/company')
async def company(request:Request):
    global _service,_configuration
    try:
        token=os.getenv('MARKET_DATA_SERVICE_TOKEN','')
        profile=os.getenv('MARKET_DATA_DEPLOYMENT_PROFILE','')
        if len(token.encode())<32 or not re.fullmatch(r'[\x21-\x7e]+',token) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',profile) or profile=='UNCONFIGURED':raise ThesisError('THESIS_UNCONFIGURED')
        if not hmac.compare_digest(request.headers.get('authorization','').encode(),('Bearer '+token).encode()):raise ThesisError('SERVICE_AUTH_REQUIRED',401)
        if request.headers.get('x-tradehub-audience')!='thesis-v1':raise ThesisError('SERVICE_AUDIENCE_INVALID',403)
        if os.getenv('THESIS_ENABLED')!='true':raise ThesisError('THESIS_DISABLED')
        pairs=list(request.query_params.multi_items())
        if any(k not in ('symbol','as_of') for k,_ in pairs) or len({k for k,_ in pairs})!=len(pairs):raise ThesisError('INVALID_QUERY',400)
        params=dict(pairs);symbol=params.get('symbol','')
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}',symbol):raise ThesisError('INVALID_SYMBOL',400)
        try:as_of=day(params.get('as_of',datetime.now(timezone.utc).date().isoformat()))
        except ValueError:raise ThesisError('INVALID_DATE',400) from None
        if as_of>datetime.now(timezone.utc).date():raise ThesisError('INVALID_DATE',400)
        fixture=fixture_mode()
        if not fixture and os.getenv('THESIS_SEC_FETCH_ENABLED')!='true':raise ThesisError('SEC_NOT_ENABLED')
        config=(fixture,os.getenv('SEC_USER_AGENT'),profile)
        if _service is None or _configuration!=config:
            if _service:await _service.close()
            _service=ThesisService(None if fixture else SecSource(os.getenv('SEC_USER_AGENT')),fixture)
            _configuration=config
        report=await _service.company(symbol,as_of)
        report=dict(report,profile=profile)
        return JSONResponse(report,headers={'Cache-Control':'private, no-store'})
    except ThesisError as error:
        return JSONResponse({'error':{'code':error.code,'retryable':error.status in (502,503,504)}},status_code=error.status,headers={'Cache-Control':'private, no-store'})


async def close_service():
    global _service,_configuration
    if _service:await _service.close()
    _service=None;_configuration=None
