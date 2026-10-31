"""Single-owner read-only ETF context coordinator."""
import asyncio
import heapq
import itertools
import time
import uuid
from datetime import datetime, timedelta, timezone
from app.market_data.capabilities import Capabilities
from app.market_data.instruments import instrument
from app.market_data.quality import freshness, number, quality, timestamp
from app.market_data.sessions import Sessions


class MarketService:
    def __init__(self, provider, profile, sessions=None, capabilities=None):
        self.provider, self.profile = provider, profile
        self.sessions, self.capabilities = sessions or Sessions(), capabilities or Capabilities()
        self.pending, self.queue = {}, []
        self.counter = itertools.count()
        self.worker = None

    async def request(self, resource, params):
        key = (resource, tuple(sorted(params.items())))
        if key in self.pending:
            return await asyncio.shield(self.pending[key])
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        heapq.heappush(self.queue, (0 if resource=='snapshot' else 1, next(self.counter), time.monotonic()+30, key, future))
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run())
        return await asyncio.shield(future)

    async def _run(self):
        while self.queue:
            _, _, deadline, key, future = heapq.heappop(self.queue)
            resource, pairs = key
            try:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    result = self.error(resource, 'REFRESH_DEADLINE', 504, instrument(dict(pairs)['symbol']))
                else:
                    result = await asyncio.wait_for(self.fetch(resource, dict(pairs), deadline), remaining)
                future.set_result(result)
            except asyncio.TimeoutError:
                future.set_result(self.error(resource, 'REFRESH_DEADLINE', 504, instrument(dict(pairs)['symbol'])))
            except Exception:
                future.set_result(self.error(resource, 'UPSTREAM_FAILURE', 502, instrument(dict(pairs)['symbol'])))
            finally:
                self.pending.pop(key, None)

    def envelope(self, resource, selection=None, now=None):
        now = now or datetime.now(timezone.utc)
        return dict(schema_version='market-data-envelope-v1', request_id=str(uuid.uuid4()), deployment_profile=self.profile,
                    generated_at=now.isoformat().replace('+00:00','Z'), instrument=selection, source=dict(provider='Massive', transport='REST', endpoint_family=resource),
                    availability='UNAVAILABLE', data=None, quality=quality(),
                    coverage_details=dict(requested_window=None,returned_window=None,pages_read=0,pagination_complete=None,observations=0,minimum_required=None,missing_intervals=[]), error=None)

    def error(self, resource, code, status, selection=None):
        result = self.envelope(resource, selection)
        result['error'] = dict(code=code,retryable=status in (429,502,503,504),retry_after_seconds=1 if status==429 else None)
        result['quality']['reasons']=[code]
        return status, result

    async def fetch(self, resource, params, deadline):
        now = datetime.now(timezone.utc)
        selection = instrument(params['symbol'])
        symbol = params['symbol']
        result = self.envelope(resource, selection, now)
        if resource=='snapshot':
            path=f'/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}'
            batch=await self.provider.get_bounded(path,{},deadline)
        else:
            path=f"/v2/aggs/ticker/{symbol}/range/1/minute/{params['from']}/{params['to']}"
            batch=await self.provider.get_bounded(path,dict(adjusted=params.get('adjusted','true'),sort='asc',limit=50000),deadline,True)
        now=datetime.now(timezone.utc)
        result['generated_at']=now.isoformat().replace('+00:00','Z')
        if batch['error']:
            code=batch['error']
            if code=='PROVIDER_NOT_ENTITLED':
                self.capabilities.observe(symbol,resource,[],now,True)
            return self.error(resource,code,{'PROVIDER_NOT_ENTITLED':403,'BUDGET_EXHAUSTED':429,'DEPENDENCY_UNAVAILABLE':503,'REFRESH_DEADLINE':504}.get(code,502),selection)
        result['coverage_details'].update(pages_read=batch['pages_read'],pagination_complete=batch['pagination_complete'])
        feed=self.capabilities.feed(symbol,now)
        if resource=='snapshot':
            raw=batch['items'][0]
            if raw.get('ticker') != symbol:
                return self.error(resource,'IDENTITY_MISMATCH',502,selection)
            data=dict(last_trade=None,bid=None,ask=None,bid_size=None,ask_size=None)
            missing,invalid=[],[]
            for name,parent,key,kind in [('last_trade','lastTrade','p','TRADE'),('bid','lastQuote','p','BID'),('ask','lastQuote','P','ASK')]:
                row=raw.get(parent) or {}
                if key not in row:
                    missing.append(name)
                    continue
                if not number(row[key],positive=True):
                    invalid.append(name)
                    continue
                try:
                    stamp=timestamp(row['t'],'NS') if row.get('t') is not None else None
                except ValueError:
                    invalid.append(name)
                    continue
                state,age,reasons=freshness(stamp,now,self.sessions,feed)
                data[name]=dict(instrument_id=selection['canonical_id'],endpoint_family='snapshot',value=row[key],price_kind=kind,
                               source_time=stamp.isoformat().replace('+00:00','Z') if stamp else None,raw_time=str(row['t']) if stamp else None,timestamp_unit='NS' if stamp else 'UNKNOWN',feed_timeframe=feed,
                               field_quality=dict(valid=True,freshness=state,reasons=reasons))
            quote=raw.get('lastQuote') or {}
            for name,key in [('bid_size','s'),('ask_size','S')]:
                value=quote.get(key)
                if isinstance(value,int) and not isinstance(value,bool) and value>=0:
                    data[name]=value
                elif value is not None:
                    invalid.append(name)
            bid,ask=data['bid'],data['ask']
            state,age,reasons='UNKNOWN',None,[]
            if not bid or not ask:
                reasons=['INVALID_QUOTE' if any(x in invalid for x in ('bid','ask')) else 'MISSING_REQUIRED_QUOTE']
            elif bid['value']>ask['value']:
                invalid.extend(['bid','ask']); reasons=['INVALID_QUOTE']
                for side in (bid,ask):
                    side['field_quality']=dict(valid=False,freshness='UNKNOWN',reasons=['INVALID_QUOTE'])
            else:
                ages=[(now-datetime.fromisoformat(side['source_time'].replace('Z','+00:00'))).total_seconds() if side['source_time'] else None for side in (bid,ask)]
                states=[side['field_quality']['freshness'] for side in (bid,ask)]
                if all(a is not None for a in ages):
                    age=max(ages)
                if age is not None and abs(ages[0]-ages[1])>5:
                    reasons=['QUOTE_TIME_SKEW']
                elif states[0]==states[1]:
                    state=states[0]
                else:
                    state=next((s for s in ('INVALID_CLOCK','STALE','DELAYED','UNKNOWN') if s in states),'UNKNOWN')
                reasons += list(dict.fromkeys(r for side in (bid,ask) for r in side['field_quality']['reasons']))
            result['data']=data
            result['quality']=quality(state,'PARTIAL' if missing or invalid else 'COMPLETE',age,missing,invalid,reasons)
            result['coverage_details']['observations']=sum(data[k] is not None for k in ('last_trade','bid','ask'))
            self.capabilities.observe(symbol,resource,[k for k in ('last_trade','bid','ask') if data[k]],now)
        else:
            adjusted=params.get('adjusted','true')=='true'
            bars=[]; expected=[]
            start=datetime.fromisoformat(params['from']).date(); end=datetime.fromisoformat(params['to']).date()
            day=start
            while day<=end:
                window=self.sessions.window(day)
                if window is None:
                    return self.error(resource,'SCHEDULE_UNKNOWN',503,selection)
                if window:
                    moment=window[0]
                    while moment+timedelta(minutes=1)<=min(window[1],now):
                        expected.append(moment)
                        moment+=timedelta(minutes=1)
                day+=timedelta(days=1)
            previous=None
            for row in batch['items']:
                stamp=timestamp(row.get('t'),'MS')
                if previous and stamp<=previous:
                    return self.error(resource,'INVALID_BAR_ORDER',502,selection)
                previous=stamp
                if not start<=stamp.date()<=end or stamp.second or stamp.microsecond:
                    return self.error(resource,'INVALID_BAR_WINDOW',502,selection)
                if not self.sessions.at(stamp):
                    continue
                if any(not number(row.get(k),True) for k in ('o','h','l','c')) or not row['l']<=min(row['o'],row['c'])<=max(row['o'],row['c'])<=row['h'] or (row.get('v') is not None and not number(row['v'])):
                    return self.error(resource,'INVALID_BAR',502,selection)
                finish=stamp+timedelta(minutes=1)
                bars.append(dict(instrument_id=selection['canonical_id'],start=stamp.isoformat().replace('+00:00','Z'),end=finish.isoformat().replace('+00:00','Z'),completed=finish<=now,
                                 open=row['o'],high=row['h'],low=row['l'],close=row['c'],volume=row.get('v'),source_time=stamp.isoformat().replace('+00:00','Z'),raw_time=str(row['t']),timestamp_unit='MS'))
            observed={datetime.fromisoformat(b['start'].replace('Z','+00:00')) for b in bars if b['completed']}
            gaps=[t.isoformat().replace('+00:00','Z') for t in expected if t not in observed]
            state,age,reasons='UNKNOWN',None,[]
            if expected and not gaps:
                state,age,reasons=freshness(expected[-1]+timedelta(minutes=1),now,self.sessions,feed,threshold=90,session_time=expected[-1])
            result['data']=dict(interval='1m',adjusted=adjusted,bars=bars)
            result['quality']=quality(state,'PARTIAL' if gaps else 'COMPLETE',age,reasons=reasons+(['MISSING_INTERVALS'] if gaps else []))
            window=dict(**{'from':datetime.combine(start,datetime.min.time(),timezone.utc).isoformat().replace('+00:00','Z'),'to':datetime.combine(end+timedelta(days=1),datetime.min.time(),timezone.utc).isoformat().replace('+00:00','Z')},interval='1m',adjusted=adjusted)
            result['coverage_details'].update(requested_window=window,returned_window=dict(**{'from':bars[0]['start'],'to':bars[-1]['end']},interval='1m',adjusted=adjusted) if bars else None,
                observations=len(bars),minimum_required=len(expected),missing_intervals=gaps)
        result['availability']='AVAILABLE'
        return 200,result
