import { analysisSchema } from '../api/schemas';import { analysis } from './factories';import { evidenceFixture } from '../api/fixtures';
test('parses the stable analysis API contract',()=>expect(analysisSchema.parse(analysis).symbol).toBe('QQQ'));
test('rejects malformed API values',()=>expect(()=>analysisSchema.parse({...analysis,current_or_last_price:'582'})).toThrow());
test('development evidence fixtures never fabricate values',()=>{expect(evidenceFixture.available).toBe(false);expect(evidenceFixture.metrics).toBeNull();expect(evidenceFixture.reason).toMatch(/no fabricated/i)});
