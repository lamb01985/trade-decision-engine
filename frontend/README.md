# React decision cockpit

This is the parity-first React foundation. The existing `app/static` cockpit remains the production FastAPI root.

```sh
npm install
npm run dev
npm test
npm run build
```

Vite proxies `/api` to `http://127.0.0.1:8765` in development. Set `VITE_API_BASE_URL` for a separately hosted standard HTTP API. Production behavior always uses live API responses by default.

To inspect layouts without a backend, copy `.env.example` to `.env.local` and set `VITE_USE_DEV_FIXTURES=true`. The app then shows a persistent yellow fixture banner and every fixture-backed surface carries a `FIXTURE` badge. Fixture mode must not be enabled in a production build.
