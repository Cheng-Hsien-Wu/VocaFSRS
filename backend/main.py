import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.constants import APP_NAME
from app.database import AsyncSessionLocal
from app.routers import placement, import_csv, study, review_data, maintenance, llm_settings
from app.services.notifications import notification_worker_loop, notifications_configured


allowed_origins_raw = settings.allowed_origins
allowed_origins = [origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.vocab_env == "production":
        if "*" in allowed_origins:
            raise RuntimeError("ALLOWED_ORIGINS must not contain '*' in production.")
    notification_worker = None
    if settings.vocab_env != "test" and notifications_configured():
        notification_worker = asyncio.create_task(notification_worker_loop(AsyncSessionLocal))
    try:
        yield
    finally:
        if notification_worker:
            notification_worker.cancel()
            try:
                await notification_worker
            except asyncio.CancelledError:
                pass


app = FastAPI(title=f"{APP_NAME} API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


app.include_router(placement.router)
app.include_router(import_csv.router)
app.include_router(study.router)
app.include_router(review_data.router)
app.include_router(maintenance.router)
app.include_router(llm_settings.router)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok"}


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def unknown_api_route(path: str):
    raise HTTPException(status_code=404, detail="Not found")


frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
no_cache_headers = {"Cache-Control": "no-cache"}
if frontend_dist.is_dir():
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def serve_frontend(path: str, request: Request):
        if request.method not in ("GET", "HEAD"):
            raise HTTPException(status_code=404, detail="Not found")

        requested_file = (frontend_dist / path).resolve()
        if requested_file.is_relative_to(frontend_dist) and requested_file.is_file():
            if requested_file.name == "index.html":
                return FileResponse(requested_file, headers=no_cache_headers)
            return FileResponse(requested_file)
        return FileResponse(frontend_dist / "index.html", headers=no_cache_headers)
