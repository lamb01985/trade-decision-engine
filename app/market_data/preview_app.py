"""Dedicated preview service: no legacy analysis routes, scanners or notifications."""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market_data import api

logging.getLogger('httpx').setLevel(logging.WARNING)

@asynccontextmanager
async def lifespan(app):
    valid, _, _ = api.configuration()
    if os.getenv('TRADEHUB_DEPLOYMENT_STAGE') != 'preview' or os.getenv('MASSIVE_BUDGET_ENFORCED') != 'true' or not valid:
        raise RuntimeError('PREVIEW_CONFIGURATION_INVALID')
    if api._service is not None:
        raise RuntimeError('PREVIEW_SINGLE_OWNER_REQUIRED')
    try:
        yield
    finally:
        if api._service is not None:
            await api._service.provider.close()
            api._service = None

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(api.router)

@app.get('/health')
async def health():
    return {'status': 'ok'}
