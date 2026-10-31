"""Security verification only: no production lifespan or live handler execution."""
from fastapi import FastAPI
from app.main import app as full_app
from app.security import EngineSecurityBoundary

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.router.routes = list(full_app.router.routes)
app.add_middleware(EngineSecurityBoundary, preview=True)
