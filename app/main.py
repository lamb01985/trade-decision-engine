import logging
from time import monotonic
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.market_data.base import MarketDataError
from app.market_data.massive import MassiveMarketDataProvider
from app.service import AnalysisService
from app.models import ContractFitCriteria, MarketScanRequest, NotificationPreferenceUpdate, OptionsPlanCriteria
from app.engines.alerts import ScannerPolicy
from app.notifications import NotificationService, PushoverProvider
from app.futures_probe import load_nq_probe
from app.options_intelligence import load_contract_shortlist, load_options_intelligence, load_options_plan
from app.universe import UniverseSelection
from app.api_contracts import (ApiErrorCode, ErrorDetail, ErrorResponse, HealthResponse,
    NotificationStatus, OptionsPlan, ReadinessResponse, AnalysisResponse, ScannerResponse,
    analysis_response, options_plan_response, scanner_response)

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # Never log provider URLs containing API credentials.
logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.massive_api_key:
        provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
        app.state.service = AnalysisService(provider)
    else:
        app.state.service = None
        provider = None
        logger.warning("MASSIVE_API_KEY is not configured; analysis endpoint will report configuration required")
    app.state.market_data_provider = provider
    push_provider = (PushoverProvider(settings.pushover_user_key, settings.pushover_app_token,
                                      settings.pushover_api_url)
                     if settings.pushover_user_key and settings.pushover_app_token else None)
    app.state.notification_service = NotificationService(
        push_provider, settings.notification_state_path, settings.public_app_base_url,
        priorities={"RADAR": settings.pushover_priority_radar,
                    "READY": settings.pushover_priority_ready,
                    "INVALIDATED": settings.pushover_priority_invalidated,
                    "TOO_LATE": settings.pushover_priority_too_late},
    )
    yield
    if provider:
        await provider.close()
    await app.state.notification_service.close()


app = FastAPI(title="Trade Decision Engine", version="2.0.0", lifespan=lifespan)
if settings.cors_allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def api_latency(request: Request, call_next):
    started = monotonic()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        logger.info("api_request method=%s path=%s status=%s duration_ms=%.1f",
                    request.method, request.url.path, response.status_code,
                    (monotonic() - started) * 1000)
    return response


@app.exception_handler(MarketDataError)
async def market_data_error(_: Request, exc: MarketDataError) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=ApiErrorCode.PROVIDER_ERROR,
        message="Market-data provider request failed.", retryable=True),
        detail="Market-data provider request failed.")
    return JSONResponse(status_code=502, content=body.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=ApiErrorCode.INVALID_REQUEST,
        message="Request validation failed."), detail="Request validation failed.")
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_api_error path=%s type=%s", request.url.path, type(exc).__name__)
    body = ErrorResponse(error=ErrorDetail(code=ApiErrorCode.INTERNAL_ERROR,
        message="The request could not be completed."), detail="The request could not be completed.")
    return JSONResponse(status_code=500, content=body.model_dump(mode="json"))


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "market_data_configured": bool(settings.massive_api_key),
            "notifications_configured": bool(settings.pushover_user_key and settings.pushover_app_token),
            "execution_enabled": False}


@app.get("/api/health", response_model=HealthResponse)
@app.get("/api/v1/health", response_model=HealthResponse)
async def api_health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/readiness", response_model=ReadinessResponse)
@app.get("/api/v1/readiness", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    signal_store = bool(getattr(getattr(request.app.state, "service", None), "signal_logger", None))
    notification_store = bool(getattr(request.app.state, "notification_service", None))
    massive = bool(settings.massive_api_key)
    return ReadinessResponse(ready=massive and signal_store and notification_store,
        massive_configured=massive,
        pushover_configured=bool(settings.pushover_user_key and settings.pushover_app_token),
        signal_store_available=signal_store, notification_store_available=notification_store)


@app.get("/api/analysis")
async def analysis(request: Request):
    service = request.app.state.service
    if service is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    try:
        return await service.analyze()
    except MarketDataError:
        raise
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail="Analysis could not be completed") from exc


def get_service(request: Request) -> AnalysisService:
    service = request.app.state.service
    if service is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    return service


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


@app.get("/api/notifications/settings")
async def notification_settings(request: Request):
    return get_notification_service(request).status()


@app.get("/api/v1/notifications/status", response_model=NotificationStatus)
async def notification_status_v1(request: Request) -> NotificationStatus:
    return NotificationStatus(**get_notification_service(request).status())


@app.put("/api/notifications/settings")
async def update_notification_settings(payload: NotificationPreferenceUpdate, request: Request):
    values = payload.model_dump(exclude_none=True)
    get_notification_service(request).update_preferences(values)
    return get_notification_service(request).status()


@app.post("/api/notifications/test")
async def test_notification(request: Request):
    return await get_notification_service(request).send_test()


@app.get("/api/options-plan/{symbol}")
async def options_plan(symbol: str, request: Request,
                       min_days_to_expiry: int = Query(3, ge=0, le=365),
                       max_days_to_expiry: int = Query(10, ge=0, le=365),
                       min_abs_delta: float = Query(0.35, ge=0.0, le=1.0),
                       max_abs_delta: float = Query(0.65, ge=0.0, le=1.0),
                       min_open_interest: int = Query(100, ge=0),
                       max_spread_pct: float = Query(10.0, gt=0.0, le=100.0),
                       strike_window_pct: float = Query(10.0, ge=1.0, le=50.0),
                       max_expirations_loaded: int = Query(3, ge=1, le=8),
                       refresh: bool = False):
    provider = request.app.state.market_data_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    try:
        criteria = OptionsPlanCriteria(
            min_days_to_expiry=min_days_to_expiry, max_days_to_expiry=max_days_to_expiry,
            min_abs_delta=min_abs_delta, max_abs_delta=max_abs_delta,
            min_open_interest=min_open_interest, max_spread_pct=max_spread_pct,
            strike_window_pct=strike_window_pct, max_expirations_loaded=max_expirations_loaded,
        )
        # Options must never inherit a cached underlying decision. The public
        # refresh flag remains API-compatible, but the safety boundary always
        # revalidates the underlying with Massive before contract evaluation.
        frozen_analysis = await get_service(request).analyze_symbol(symbol, include_cross_market=False, force=True)
        return await load_options_plan(provider, frozen_analysis, criteria)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/options-plan/{symbol}", response_model=OptionsPlan)
async def options_plan_v1(symbol: str, request: Request,
                          min_days_to_expiry: int = Query(3, ge=0, le=365),
                          max_days_to_expiry: int = Query(10, ge=0, le=365),
                          min_abs_delta: float = Query(0.35, ge=0.0, le=1.0),
                          max_abs_delta: float = Query(0.65, ge=0.0, le=1.0),
                          min_open_interest: int = Query(100, ge=0),
                          max_spread_pct: float = Query(10.0, gt=0.0, le=100.0),
                          strike_window_pct: float = Query(10.0, ge=1.0, le=50.0),
                          max_expirations_loaded: int = Query(3, ge=1, le=8)) -> OptionsPlan:
    provider = request.app.state.market_data_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="Market data is not configured.")
    criteria = OptionsPlanCriteria(min_days_to_expiry=min_days_to_expiry,
        max_days_to_expiry=max_days_to_expiry, min_abs_delta=min_abs_delta,
        max_abs_delta=max_abs_delta, min_open_interest=min_open_interest,
        max_spread_pct=max_spread_pct, strike_window_pct=strike_window_pct,
        max_expirations_loaded=max_expirations_loaded)
    frozen = await get_service(request).analyze_symbol(symbol, include_cross_market=False, force=True)
    return options_plan_response(await load_options_plan(provider, frozen, criteria))


@app.get("/api/options-shortlist/{symbol}")
async def options_shortlist(symbol: str, request: Request, contract_type: str = Query(...),
                            min_days_to_expiry: int = Query(3, ge=0, le=365),
                            max_days_to_expiry: int = Query(10, ge=0, le=365),
                            min_abs_delta: float = Query(0.35, ge=0.0, le=1.0),
                            max_abs_delta: float = Query(0.65, ge=0.0, le=1.0),
                            min_open_interest: int = Query(100, ge=0),
                            max_spread_pct: float = Query(10.0, gt=0.0, le=100.0),
                            strike_window_pct: float = Query(10.0, ge=1.0, le=50.0),
                            max_expirations_loaded: int = Query(3, ge=1, le=8)):
    provider = request.app.state.market_data_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    try:
        criteria = ContractFitCriteria(
            contract_type=contract_type.strip().upper(), min_days_to_expiry=min_days_to_expiry,
            max_days_to_expiry=max_days_to_expiry, min_abs_delta=min_abs_delta,
            max_abs_delta=max_abs_delta, min_open_interest=min_open_interest,
            max_spread_pct=max_spread_pct, strike_window_pct=strike_window_pct,
            max_expirations_loaded=max_expirations_loaded,
        )
        return await load_contract_shortlist(provider, symbol, criteria)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/options-intelligence/{symbol}")
async def options_intelligence(symbol: str, request: Request, expiration: str | None = None,
                               strike_window_pct: float = Query(10.0, ge=1.0, le=50.0)):
    provider = request.app.state.market_data_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    try:
        return await load_options_intelligence(provider, symbol, expiration, strike_window_pct)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/futures-probe/nq")
async def futures_nq_probe(request: Request):
    provider = request.app.state.market_data_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="MASSIVE_API_KEY is not configured. Copy .env.example to .env and add your key.")
    return await load_nq_probe(provider)


@app.get("/api/analyze/{symbol}")
async def analyze_symbol(symbol: str, request: Request, cross_market: bool = True, refresh: bool = False):
    try:
        return await get_service(request).analyze_symbol(symbol, include_cross_market=cross_market, force=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MarketDataError:
        raise
    except Exception as exc:
        logger.exception("Symbol analysis failed")
        raise HTTPException(status_code=500, detail="Analysis could not be completed") from exc


@app.get("/api/v1/analysis/{symbol}", response_model=AnalysisResponse,
         responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def analysis_v1(symbol: str, request: Request, refresh: bool = False) -> AnalysisResponse:
    try:
        result = await get_service(request).analyze_symbol(symbol, force=refresh)
        return analysis_response(result)
    except ValueError as exc:
        body = ErrorResponse(error=ErrorDetail(code=ApiErrorCode.UNSUPPORTED_SYMBOL,
            message=str(exc)), detail=str(exc))
        raise HTTPException(status_code=400, detail=body.model_dump(mode="json")) from exc


@app.get("/api/scanner")
async def scanner(request: Request, symbols: str = Query(..., min_length=1), refresh: bool = False):
    try:
        requested = [x for x in symbols.split(",") if x.strip()]
        if len(requested) > 30:
            raise ValueError("Scanner supports up to 30 symbols per request")
        return {"rows": await get_service(request).scan(requested, force=refresh)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/market-scanner")
async def market_scanner(payload: MarketScanRequest, request: Request):
    try:
        selection = UniverseSelection(watchlist=tuple(payload.watchlist),
            include_nasdaq_100=payload.include_nasdaq_100, include_sp_500=payload.include_sp_500,
            additional=tuple(payload.additional_symbols))
        policy = ScannerPolicy(minimum_price=payload.minimum_price or settings.scanner_min_price,
            minimum_average_dollar_volume=payload.minimum_average_dollar_volume or settings.scanner_min_average_dollar_volume,
            minimum_score=payload.minimum_score or settings.scanner_min_score)
        result = await get_service(request).market_scan(selection, force=payload.refresh, policy=policy)
        notifications = get_notification_service(request)
        try:
            sent = await notifications.process(result.alerts, result.data_status, result.market_session)
            notification_status = notifications.status()["status"]
        except Exception:
            # Notification persistence/delivery is downstream of scanning and
            # must never turn a valid market scan into a failed scan.
            logger.exception("Notification processing failed; scanner result remains available")
            sent, notification_status = 0, "ERROR"
        return result.model_copy(update={"notification_status": notification_status,
                                         "notifications_sent": sent})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/scanner", response_model=ScannerResponse)
async def market_scanner_v1(payload: MarketScanRequest, request: Request) -> ScannerResponse:
    legacy = await market_scanner(payload, request)
    return scanner_response(legacy)
