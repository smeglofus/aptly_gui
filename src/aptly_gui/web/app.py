from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..aptly import AptlyClient
from ..config import Settings
from .state import StateCache

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = AptlyClient(
            settings.aptly_url,
            socket_path=settings.aptly_socket,
            timeout=settings.request_timeout,
        )
        app.state.cache = StateCache(client, refresh_seconds=settings.refresh_seconds)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="aptly-gui", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    templates.env.filters["age"] = _age
    templates.env.filters["gib"] = _gib

    async def render(request: Request, template: str, **context: Any) -> HTMLResponse:
        cached = await request.app.state.cache.get(force=_force_refresh(request))
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={"cached": cached, "s": cached.state, "now": datetime.now(UTC), **context},
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return await render(request, "dashboard.html", page="dashboard")

    @app.get("/mirrors", response_class=HTMLResponse)
    async def mirrors(request: Request) -> HTMLResponse:
        return await render(request, "mirrors.html", page="mirrors")

    @app.get("/snapshots", response_class=HTMLResponse)
    async def snapshots(request: Request) -> HTMLResponse:
        return await render(request, "snapshots.html", page="snapshots")

    @app.get("/publications", response_class=HTMLResponse)
    async def publications(request: Request) -> HTMLResponse:
        return await render(request, "publications.html", page="publications")

    @app.get("/partials/status", response_class=HTMLResponse)
    async def status_partial(request: Request) -> HTMLResponse:
        return await render(request, "_status.html")

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        cached = await request.app.state.cache.get()
        return JSONResponse(
            {
                "aptly_reachable": not cached.stale,
                "aptly_version": cached.state.version,
                "last_read": cached.state.fetched_at.isoformat(),
                "error": cached.error,
            },
            status_code=200 if not cached.stale else 503,
        )

    return app


def _force_refresh(request: Request) -> bool:
    return request.query_params.get("refresh") == "1"


def _age(value: datetime | None) -> str:
    if value is None:
        return "nikdy"
    seconds = (datetime.now(UTC) - value).total_seconds()
    if seconds < 90:
        return "před chvílí"
    minutes = seconds / 60
    if minutes < 90:
        return f"před {minutes:.0f} min"
    hours = minutes / 60
    if hours < 48:
        return f"před {hours:.0f} h"
    return f"před {hours / 24:.0f} dny"


def _gib(megabytes: int | None) -> str:
    if megabytes is None:
        return "—"
    return f"{megabytes / 1024:.1f} GiB"


app = create_app()
