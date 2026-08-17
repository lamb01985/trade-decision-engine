import { friendlyCondition, presentSetupState } from '../domain/presentation';
test.each([['CONFIRMED',true,'READY'],['TRIGGERED',true,'READY'],['READY',false,'WAIT'],['CONFIRMED',false,'WAIT'],['FORMING',true,'WAIT'],['INVALID',true,'INVALID'],['PASS',true,'PASS'],['MONITOR',true,'MONITOR'],[null,true,'MONITOR']] as const)('maps raw %s with actionable=%s to %s without changing backend data',(raw,actionable,expected)=>expect(presentSetupState(raw,actionable)).toBe(expected));

test('presents mapped-structure terminology without changing the raw condition',()=>{
  const raw='new mapped structure and a qualifying setup sequence';
  expect(friendlyCondition(raw)).toBe('new qualifying structure and setup sequence');
  expect(raw).toBe('new mapped structure and a qualifying setup sequence');
  expect(friendlyCondition('Acceptance below invalidation')).toBe('Acceptance below invalidation');
});
