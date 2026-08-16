import { analysisSchema, chartSchema } from '../api/schemas';import { analysis } from './factories';import { chartFixture, evidenceFixture } from '../api/fixtures';
test('parses the stable analysis API contract',()=>expect(analysisSchema.parse(analysis).symbol).toBe('QQQ'));
test('rejects malformed API values',()=>expect(()=>analysisSchema.parse({...analysis,current_or_last_price:'582'})).toThrow());
test('parses ordered timestamped chart candles',()=>expect(chartSchema.parse(chartFixture).candles).toHaveLength(2));
test('rejects unordered chart candles',()=>expect(()=>chartSchema.parse({...chartFixture,candles:[...chartFixture.candles].reverse()})).toThrow(/strictly ordered/));
test('development evidence fixtures never fabricate values',()=>{expect(evidenceFixture.available).toBe(false);expect(evidenceFixture.metrics).toBeNull();expect(evidenceFixture.reason).toMatch(/no fabricated/i)});
