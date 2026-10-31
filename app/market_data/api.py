"""Authenticated internal ETF resources; no upstream-controlled destinations."""
import hmac
import ipaddress
import os
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.market_data.budget import configured_budget, BudgetUnavailable
from app.market_data.instruments import instrument
from app.market_data.massive import MassiveMarketDataProvider
from app.market_data.service import MarketService

router=APIRouter()
_service=None


def configuration():
    token=os.getenv('MARKET_DATA_SERVICE_TOKEN','')
    profile=os.getenv('MARKET_DATA_DEPLOYMENT_PROFILE','')
    enabled=os.getenv('MARKET_DATA_UNIFIED_ENABLED') == 'true'
    url=urlsplit(os.getenv('MARKET_DATA_SERVICE_URL',''))
    loopback=False
    try:
        loopback=ipaddress.ip_address(url.hostname or '').is_loopback
    except ValueError:
        pass
    valid_url=(url.scheme=='https' or (url.scheme=='http' and loopback and os.getenv('APP_STORAGE_MODE')=='local' and not os.getenv('VERCEL'))) and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment and url.path in ('','/')
    valid=enabled and valid_url and len(token.encode())>=32 and all(33<=ord(c)<=126 for c in token) and profile != 'UNCONFIGURED' and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}',profile)) and bool(os.getenv('MASSIVE_API_KEY')) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}',os.getenv('MASSIVE_ACCOUNT_BUDGET_SCOPE','')))
    try:
        from app.market_data.budget import validate_configuration
        validate_configuration()
    except BudgetUnavailable:
        valid=False
    return valid,token,profile or 'UNCONFIGURED'


@router.api_route('/api/v1/market-data/{resource}',methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'])
async def market_data(resource: str, request: Request):
    global _service
    valid,token,profile=configuration()
    fallback=MarketService(None,profile)
    def error(code,status,selection=None):
        status,body=fallback.error(resource,code,status,selection)
        return JSONResponse(body,status_code=status,headers={'Cache-Control':'no-store'})
    if resource not in ('capabilities','snapshot','bars','options','intelligence'):
        return error('UNKNOWN_RESOURCE',404)
    if request.method!='GET':
        return error('METHOD_NOT_ALLOWED',405)
    authorization=request.headers.get('authorization','')
    if not token or not hmac.compare_digest(authorization, 'Bearer '+token):
        return error('SERVICE_AUTHENTICATION_REQUIRED',401)
    if request.headers.get('x-tradehub-audience')!='market-data-v1':
        return error('SERVICE_AUDIENCE_INVALID',403)
    if not valid:
        return error('CONFIGURATION_UNAVAILABLE',503)
    if _service is None:
        _service=MarketService(MassiveMarketDataProvider(os.environ['MASSIVE_API_KEY']),profile)
    allowed={'snapshot':{'symbol'},'bars':{'symbol','interval','from','to','adjusted'}}.get(resource,set())
    pairs=list(request.query_params.multi_items())
    if any(k not in allowed for k,v in pairs) or len({k for k,v in pairs})!=len(pairs):
        return error('INVALID_QUERY',400)
    params=dict(pairs)
    selection=None
    if resource in ('snapshot','bars'):
        try:
            selection=instrument(params.get('symbol'))
        except ValueError:
            return error('UNSUPPORTED_INSTRUMENT',400)
    if resource=='bars':
        if params.get('interval')!='1m':
            return error('UNSUPPORTED_INTERVAL',400,selection)
        try:
            if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}',params.get(key,'')) for key in ('from','to')):
                raise ValueError()
            start,end=date.fromisoformat(params['from']),date.fromisoformat(params['to'])
            if start>end or (end-start).days>20 or params.get('adjusted','true') not in ('true','false'):
                raise ValueError()
            count=0; day=start
            while day<=end:
                window=_service.sessions.window(day)
                if window is None:
                    return error('SCHEDULE_UNKNOWN',503,selection)
                count+=bool(window)
                day+=timedelta(days=1)
            if count>10:
                raise ValueError()
        except ValueError:
            return error('INVALID_QUERY',400,selection)
    if resource in ('options','intelligence'):
        return error('FEATURE_NOT_ENABLED',503)
    if resource=='capabilities':
        body=_service.envelope(resource)
        body.update(availability='AVAILABLE',data={'records':_service.capabilities.records(profile,datetime.now(timezone.utc))})
        body['quality']['coverage']='COMPLETE'
        return JSONResponse(body,headers={'Cache-Control':'no-store'})
    status,body=await _service.request(resource,params)
    return JSONResponse(body,status_code=status,headers={'Cache-Control':'no-store'})
