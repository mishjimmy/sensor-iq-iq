"""SensorIQ panel monitor: read-only dashboard + Grist live circuit status."""
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .history import History
from .poller import Monitor
from .registry import Registry
from .routes import router
from .settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log every 5 s poll


class _SkipHealthz(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SkipHealthz())  # docker HEALTHCHECK every 30 s
log = logging.getLogger("sensoriq")
STATIC = Path(__file__).parent / "static"


def check_writable(label: str, path: str) -> None:
    """Fail at startup with a readable message instead of a traceback."""
    p = Path(path).resolve()
    target = p if p.exists() else p.parent
    if not os.access(target, os.W_OK):
        raise SystemExit(
            f"{label} is not writable: {p} (running as uid {os.getuid() if hasattr(os, 'getuid') else '?'}).\n"
            f"In Docker/Dokploy, leave PANELS_FILE and HISTORY_DB unset so they use /data, and mount a "
            f"named volume at /data. If /data is a host bind mount, chown it to uid 10001.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_writable("PANELS_FILE", settings.panels_file)
    check_writable("HISTORY_DB", settings.history_db)
    registry = Registry(settings.panels_file)
    if registry.load_error:
        log.error("panels file error: %s", registry.load_error)
    history = History(settings.history_db, settings.history_days)
    monitor = Monitor(settings, registry, history)
    app.state.settings = settings
    app.state.monitor = monitor
    log.info("monitoring %d panel(s); grist sync %s", len(registry.panels),
             "enabled" if settings.grist_enabled else "disabled (set GRIST_API_KEY and GRIST_SERVER)")
    monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="SensorIQ Monitor", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """Pages and /static must revalidate every load, so a redeploy shows up immediately.

    Without this, Cloudflare (and browsers) cache app.js/styles.css and serve the old UI.
    ETag/Last-Modified still make unchanged files a cheap 304.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/panels", "/stats"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_ASSET = re.compile(r'(/static/[\w.-]+\.(?:css|js))(?=["\'])')


@lru_cache
def _asset_version(path: str) -> str:
    return hashlib.sha1((STATIC / path.removeprefix("/static/")).read_bytes()).hexdigest()[:10]


@lru_cache
def _page(name: str) -> str:
    """HTML with ?v=<content hash> on every /static asset, so each deploy gets fresh URLs
    and no cache (Cloudflare edge or browser) can serve an old app.js / styles.css."""
    html = (STATIC / name).read_text(encoding="utf-8")
    return _ASSET.sub(lambda m: f"{m.group(1)}?v={_asset_version(m.group(1))}", html)


@app.get("/", include_in_schema=False)
async def index():
    return HTMLResponse(_page("index.html"))


@app.get("/panels", include_in_schema=False)
async def panels_page():
    return HTMLResponse(_page("panels.html"))


@app.get("/stats", include_in_schema=False)
async def stats_page():
    return HTMLResponse(_page("stats.html"))


@app.get("/healthz", include_in_schema=False)
async def healthz():
    mon = app.state.monitor
    states = list(mon.states.values())
    return {"ok": True, "panels": len(states), "online": sum(1 for s in states if s.online),
            "updated_at": mon.updated_at}


def run() -> None:
    import uvicorn
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port)


if __name__ == "__main__":
    run()
