"""Fixture-only localhost preview. It mounts no live market or trading services."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.thesis.api import router,fixture_mode,close_service

@asynccontextmanager
async def lifespan(app):
    if not fixture_mode() or os.getenv('THESIS_ENABLED')!='true':raise RuntimeError('EXPLICIT_DEVELOPMENT_FIXTURES_REQUIRED')
    try:yield
    finally:await close_service()

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.include_router(router)

@app.get('/health')
async def health():return {'status':'ok','source':'DEVELOPMENT_FIXTURE'}
