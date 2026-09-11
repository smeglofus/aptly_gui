from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import i18n
from ..aptly import AptlyClient
from ..config import LANGUAGES, Settings
from ..db import (
    AppSetting,
    AuditEntry,
    Job,
    JobType,
    MirrorSet,
    SnapshotSetState,
    create_all,
    create_engine,
    create_session_factory,
)
from ..services import JobRunner, propose_sets
from .state import StateCache

HERE = Path(__file__).parent
LANGUAGE_KEY = "language"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.add_extension("jinja2.ext.i18n")
    # Added by jinja2.ext.i18n at runtime, so it is invisible to the type checker.
    templates.env.install_gettext_callables(  # type: ignore[attr-defined]
        i18n.gettext, i18n.ngettext, newstyle=True
    )
    templates.env.filters["age"] = _age
    templates.env.filters["gib"] = _gib
    templates.env.filters["job_type"] = _job_type
    templates.env.filters["job_state"] = _job_state
    templates.env.filters["bytes"] = _bytes
    templates.env.filters["duration"] = _duration

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = AptlyClient(
            settings.aptly_url,
            socket_path=settings.aptly_socket,
            timeout=settings.request_timeout,
        )
        engine = create_engine(settings.database_url)
        await create_all(engine)
        sessions = create_session_factory(engine)

        app.state.settings = settings
        app.state.client = client
        app.state.sessions = sessions
        app.state.cache = StateCache(client, refresh_seconds=settings.refresh_seconds)
        app.state.language = await _stored_language(app, settings)
        app.state.runner = JobRunner(client, sessions)
        await app.state.runner.start()
        try:
            yield
        finally:
            await app.state.runner.stop()
            await client.aclose()
            await engine.dispose()

    app = FastAPI(title="aptly-gui", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    async def render(request: Request, template: str, **context: Any) -> HTMLResponse:
        i18n.activate(request.app.state.language)
        cached = await request.app.state.cache.get(force=request.query_params.get("refresh") == "1")
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={
                "cached": cached,
                "s": cached.state,
                "now": datetime.now(UTC),
                "locale": request.app.state.language,
                "languages": LANGUAGES,
                **context,
            },
        )

    # --- overview -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        sets = await _load_sets(request)
        return await render(request, "dashboard.html", page="dashboard", sets=sets)

    @app.get("/mirrors", response_class=HTMLResponse)
    async def mirrors(request: Request) -> HTMLResponse:
        sets = await _load_sets(request)
        return await render(
            request, "mirrors.html", page="mirrors", managed_mirrors=_managed_map(sets)
        )

    @app.get("/snapshots", response_class=HTMLResponse)
    async def snapshots(request: Request) -> HTMLResponse:
        return await render(request, "snapshots.html", page="snapshots")

    @app.get("/publications", response_class=HTMLResponse)
    async def publications(request: Request) -> HTMLResponse:
        return await render(request, "publications.html", page="publications")

    # --- mirror sets ----------------------------------------------------

    @app.get("/sets", response_class=HTMLResponse)
    async def sets_index(request: Request) -> HTMLResponse:
        return await render(request, "sets.html", page="sets", sets=await _load_sets(request))

    @app.get("/sets/adopt", response_class=HTMLResponse)
    async def adopt_form(request: Request) -> HTMLResponse:
        cached = await request.app.state.cache.get()
        sets = await _load_sets(request)
        proposals, leftovers = propose_sets(
            cached.state.mirrors, cached.state.published, managed=set(_managed_map(sets))
        )
        return await render(
            request, "adopt.html", page="sets", proposals=proposals, leftovers=leftovers
        )

    @app.post("/sets/adopt")
    async def adopt_submit(
        request: Request,
        name: str = Form(...),
        archive_url: str = Form(...),
        suites: str = Form(...),
        components: str = Form(...),
        architectures: str = Form(...),
        publish_prefix: str = Form(...),
        keyrings: str = Form(""),
        signing_key: str = Form(""),
        filter: str = Form(""),
    ) -> RedirectResponse:
        async with request.app.state.sessions() as session:
            mirror_set = MirrorSet(
                name=name,
                archive_url=archive_url,
                suites=_split(suites),
                components=_split(components),
                architectures=_split(architectures),
                keyrings=_split(keyrings),
                filter=filter or None,
                publish_prefix=publish_prefix,
                signing_key=signing_key or None,
                adopted=True,
            )
            session.add(mirror_set)
            session.add(AuditEntry(action="set.adopt", target=name))
            await session.commit()
            await session.refresh(mirror_set)
        return RedirectResponse(f"/sets/{mirror_set.id}", status_code=303)

    @app.get("/sets/{set_id}", response_class=HTMLResponse)
    async def set_detail(request: Request, set_id: int) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            mirror_set = await session.get(
                MirrorSet, set_id, options=[selectinload(MirrorSet.snapshot_sets)]
            )
            if mirror_set is None:
                return await render(request, "not_found.html", page="sets")
            snapshot_sets = sorted(
                mirror_set.snapshot_sets, key=lambda item: item.taken_at, reverse=True
            )
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .where(Job.mirror_set_id == set_id)
                        .order_by(Job.id.desc())
                        .limit(5)
                    )
                )
                .scalars()
                .all()
            )
        return await render(
            request,
            "set_detail.html",
            page="sets",
            mirror_set=mirror_set,
            snapshot_sets=snapshot_sets,
            jobs=list(jobs),
            complete_state=SnapshotSetState.COMPLETE,
        )

    @app.post("/sets/{set_id}/update")
    async def set_update(request: Request, set_id: int) -> RedirectResponse:
        job = await request.app.state.runner.enqueue(
            JobType.UPDATE, set_id, author="anonymous", params={}
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/sets/{set_id}/switch")
    async def set_switch(
        request: Request, set_id: int, snapshot_set_id: int = Form(...)
    ) -> RedirectResponse:
        job = await request.app.state.runner.enqueue(
            JobType.SWITCH,
            set_id,
            author="anonymous",
            params={"snapshot_set_id": snapshot_set_id},
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    # --- jobs -----------------------------------------------------------

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_index(request: Request) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .options(selectinload(Job.mirror_set), selectinload(Job.steps))
                        .order_by(Job.id.desc())
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )
        return await render(request, "jobs.html", page="jobs", jobs=list(jobs))

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(request: Request, job_id: int) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            job = await session.get(
                Job, job_id, options=[selectinload(Job.steps), selectinload(Job.mirror_set)]
            )
        if job is None:
            return await render(request, "not_found.html", page="jobs")
        return await render(request, "job_detail.html", page="jobs", job=job)

    @app.get("/partials/job/{job_id}", response_class=HTMLResponse)
    async def job_partial(request: Request, job_id: int) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            job = await session.get(Job, job_id, options=[selectinload(Job.steps)])
        return await render(request, "_job_progress.html", job=job)

    # --- settings -------------------------------------------------------

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_form(request: Request, saved: bool = False) -> HTMLResponse:
        return await render(
            request,
            "settings.html",
            page="settings",
            saved=saved,
            aptly_target=settings.aptly_socket or settings.aptly_url,
            database_url=settings.database_url,
        )

    @app.post("/settings")
    async def settings_save(request: Request, language: str = Form(...)) -> RedirectResponse:
        if language not in LANGUAGES:
            language = settings.default_language
        async with request.app.state.sessions() as session:
            row = await session.get(AppSetting, LANGUAGE_KEY)
            if row is None:
                session.add(AppSetting(key=LANGUAGE_KEY, value=language))
            else:
                row.value = language
            session.add(AuditEntry(action="settings.language", target=language))
            await session.commit()
        request.app.state.language = language
        return RedirectResponse("/settings?saved=1", status_code=303)

    # --- misc -----------------------------------------------------------

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


async def _stored_language(app: FastAPI, settings: Settings) -> str:
    async with app.state.sessions() as session:
        row = await session.get(AppSetting, LANGUAGE_KEY)
    return row.value if row and row.value in LANGUAGES else settings.default_language


async def _load_sets(request: Request) -> list[MirrorSet]:
    async with request.app.state.sessions() as session:
        rows = (
            (
                await session.execute(
                    select(MirrorSet)
                    .options(selectinload(MirrorSet.snapshot_sets))
                    .order_by(MirrorSet.name)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _managed_map(sets: list[MirrorSet]) -> dict[str, str]:
    return {name: item.name for item in sets for name in item.expected_mirrors}


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def _age(value: datetime | None) -> str:
    if value is None:
        return i18n.gettext("never")
    seconds = (datetime.now(UTC) - value).total_seconds()
    if seconds < 90:
        return i18n.gettext("just now")
    minutes = seconds / 60
    if minutes < 90:
        return i18n.ngettext("%(n)d min ago", "%(n)d min ago", int(minutes)) % {"n": int(minutes)}
    hours = minutes / 60
    if hours < 48:
        return i18n.ngettext("%(n)d hour ago", "%(n)d hours ago", int(hours)) % {"n": int(hours)}
    days = int(hours / 24)
    return i18n.ngettext("%(n)d day ago", "%(n)d days ago", days) % {"n": days}


def _gib(megabytes: int | None) -> str:
    if megabytes is None:
        return "—"
    return f"{megabytes / 1024:.1f} GiB"


def _bytes(value: float | None) -> str:
    if not value:
        return "0 B"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _duration(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "0 s"
    total = int(seconds)
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{total // 60} min {total % 60} s"
    return f"{total // 3600} h {(total % 3600) // 60} min"


def _job_type(value: str) -> str:
    return {
        "update": i18n.gettext("sync and snapshot"),
        "switch": i18n.gettext("publish"),
        "cleanup": i18n.gettext("cleanup"),
    }.get(value, value)


def _job_state(value: str) -> str:
    return {
        "queued": i18n.gettext("queued"),
        "running": i18n.gettext("running"),
        "succeeded": i18n.gettext("succeeded"),
        "failed": i18n.gettext("failed"),
        "interrupted": i18n.gettext("interrupted"),
        "pending": i18n.gettext("pending"),
        "skipped": i18n.gettext("skipped"),
    }.get(value, value)


app = create_app()
