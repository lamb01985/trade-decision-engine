-- Shared Node/Python atomic account admission. Policy is provisioned separately.
local n,r=tonumber(ARGV[1]),tonumber(ARGV[2])
if redis.call('HGET',KEYS[1],'N')~=ARGV[1] or redis.call('HGET',KEYS[1],'R')~=ARGV[2] or redis.call('HGET',KEYS[1],'policy_version')~=ARGV[3] then return {0,'',1,'POLICY_MISMATCH'} end
local t=redis.call('TIME'); local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now-60000)
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now-60000)
redis.call('ZREMRANGEBYSCORE',KEYS[4],'-inf',now)
if redis.call('ZSCORE',KEYS[2],ARGV[4]) then return {0,'',1,'DUPLICATE_ATTEMPT'} end
local wait=0; local reason='GRANTED'
local function block(key,offset,label)
 local row=redis.call('ZRANGE',key,0,0,'WITHSCORES')
 local delay=math.max(1,math.ceil((tonumber(row[2])+offset-now)/1000))
 if delay>wait then wait=delay;reason=label end
end
if redis.call('ZCARD',KEYS[2])>=n then block(KEYS[2],60000,'ACCOUNT_LIMIT') end
if ARGV[5]=='new' and redis.call('ZCARD',KEYS[3])>=n-r then block(KEYS[3],60000,'NEW_LIMIT') end
if redis.call('ZCARD',KEYS[4])>=4 then block(KEYS[4],0,'CONCURRENCY_LIMIT') end
if wait>0 then return {0,'',wait,reason} end
redis.call('ZADD',KEYS[2],now,ARGV[4])
if ARGV[5]=='new' then redis.call('ZADD',KEYS[3],now,ARGV[4]) end
redis.call('ZADD',KEYS[4],now+tonumber(ARGV[6])+5000,ARGV[4])
return {1,ARGV[4],0,'GRANTED'}
