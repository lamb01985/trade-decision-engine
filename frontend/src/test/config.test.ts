import vercel from '../../vercel.json';import client from '../api/client.ts?raw';
test('preview config provides an SPA rewrite',()=>expect(vercel.rewrites[0].destination).toBe('/index.html'));
test('frontend client uses only a public API URL and contains no server credential names',()=>{expect(client).not.toMatch(/MASSIVE_API_KEY|AWS_ACCESS_KEY|DATABASE_URL|RDS_PASSWORD/);expect(client).toContain('VITE_API_BASE_URL');expect(client).toContain("VITE_USE_DEV_FIXTURES==='true'")});
