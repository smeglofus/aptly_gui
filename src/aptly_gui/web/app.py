from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import i18n
from ..aptly import AptlyClient
from ..auth import (
    OidcClient,
    OidcConfig,
    OidcError,
    clear_session,
    csrf_matches,
    describe_weakness,
    hash_password,
    is_public,
    make_verifier,
    new_csrf_token,
    read_session,
    required_role,
    verify_password,
    write_session,
)
from ..config import LANGUAGES, Settings
from ..db import (
    AppSetting,
    AuditEntry,
    Job,
    JobType,
    MirrorSet,
    SnapshotSet,
    SnapshotSetState,
    User,
    UserRole,
    create_engine,
    create_session_factory,
    upgrade_database,
)
from ..services import (
    PATTERNS,
    PRESETS,
    SET_SUITE_COMPONENT,
    JobRunner,
    Margin,
    get_preset,
    propose_sets,
)
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
            create_timeout=settings.create_timeout,
        )
        engine = create_engine(settings.database_url)
        await upgrade_database(engine)
        sessions = create_session_factory(engine)

        app.state.settings = settings
        app.state.secret_key = await _resolve_secret_key(sessions, settings)
        app.state.session_max_age = settings.session_max_age
        app.state.secure_cookies = settings.secure_cookies
        app.state.oidc_config = OidcConfig.from_env()
        app.state.oidc = (
            OidcClient(app.state.oidc_config) if app.state.oidc_config.enabled else None
        )
        await _reconcile_admin(sessions, settings)
        app.state.has_users = await _any_user_exists(sessions)
        app.state.client = client
        app.state.sessions = sessions
        app.state.cache = StateCache(client, refresh_seconds=settings.refresh_seconds)
        app.state.language = await _stored_language(app, settings)
        app.state.runner = JobRunner(
            client,
            sessions,
            margin=Margin(
                gigabytes=settings.safety_margin_gb,
                percent=settings.safety_margin_percent,
            ),
        )
        await app.state.runner.start()
        try:
            yield
        finally:
            await app.state.runner.stop()
            await client.aclose()
            await engine.dispose()

    app = FastAPI(title="aptly-gui", lifespan=lifespan, dependencies=[Depends(_check_csrf)])
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        """Decide who may make this request before any handler runs.

        The body is deliberately not touched here — reading the form in middleware
        would consume it before the endpoint could. CSRF is a dependency instead.
        """
        path = request.url.path
        session = read_session(request)
        request.state.user = await _session_user(request, session)

        # Even a signed-out visitor needs a token, or the login form could not be
        # submitted; it is bound to their cookie exactly like any other.
        issued = None
        request.state.csrf = session.get("csrf")
        if not request.state.csrf:
            issued = new_csrf_token()
            request.state.csrf = issued

        response = await _dispatch(request, call_next, path)
        if issued and "set-cookie" not in response.headers:
            write_session(request, response, {**session, "csrf": issued})
        return response

    async def _dispatch(request: Request, call_next: Any, path: str) -> Response:
        if path.startswith("/static") or path == "/healthz":
            return cast(Response, await call_next(request))

        # Before the first account exists every road leads to creating it.
        if not request.app.state.has_users:
            if path != "/setup":
                return RedirectResponse("/setup", status_code=303)
            return cast(Response, await call_next(request))
        if path == "/setup":
            return RedirectResponse("/", status_code=303)

        if is_public(path):
            return cast(Response, await call_next(request))

        user = request.state.user
        if user is None:
            return RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)

        needed = required_role(request.method, path)
        if not user.can(needed):
            i18n.activate(request.app.state.language)
            return templates.TemplateResponse(
                request=request,
                name="forbidden.html",
                context={
                    "page": "",
                    "locale": request.app.state.language,
                    "languages": LANGUAGES,
                    "user": user,
                    "csrf_token": request.state.csrf,
                    "needed": needed,
                },
                status_code=403,
            )
        return cast(Response, await call_next(request))

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
                "user": getattr(request.state, "user", None),
                "csrf_token": getattr(request.state, "csrf", None),
                "oidc_enabled": request.app.state.oidc is not None,
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
    async def snapshots(request: Request, state: str | None = None) -> HTMLResponse:
        cached = await request.app.state.cache.get()
        context = await _snapshot_context(request, cached.state)
        shown = sorted(cached.state.snapshots, key=lambda item: item.name)
        if state == "unpublished":
            published = cached.state.published_snapshot_names
            shown = [item for item in shown if item.name not in published]
        elif state == "published":
            published = cached.state.published_snapshot_names
            shown = [item for item in shown if item.name in published]
        return await render(
            request,
            "snapshots.html",
            page="snapshots",
            context=context,
            snapshots=shown,
            total=len(cached.state.snapshots),
            state=state if state in ("published", "unpublished") else None,
        )

    @app.get("/snapshots/discard", response_class=HTMLResponse)
    async def discard_confirm(request: Request, name: str) -> HTMLResponse:
        cached = await request.app.state.cache.get()
        context = await _snapshot_context(request, cached.state)
        entry = context.get(name)
        return await render(
            request,
            "snapshot_discard.html",
            page="snapshots",
            name=name,
            entry=entry,
            blocked=None if entry is None else entry.get("blocked"),
        )

    @app.post("/snapshots/discard")
    async def discard_submit(request: Request, name: str = Form(...)) -> Response:
        cached = await request.app.state.cache.get()
        context = await _snapshot_context(request, cached.state)
        entry = context.get(name)
        if entry is None or entry.get("blocked"):
            return RedirectResponse(f"/snapshots/discard?name={name}", status_code=303)
        job = await request.app.state.runner.enqueue(
            JobType.DISCARD,
            entry.get("mirror_set_id"),
            author=request.state.user.username,
            params={"snapshots": [name]},
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/sets/{set_id}/discard/{snapshot_set_id}", response_class=HTMLResponse)
    async def discard_set_confirm(
        request: Request, set_id: int, snapshot_set_id: int
    ) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            snapshot_set = await session.get(SnapshotSet, snapshot_set_id)
            mirror_set = await session.get(MirrorSet, set_id)
        if snapshot_set is None or mirror_set is None:
            return await render(request, "not_found.html", page="sets")
        cached = await request.app.state.cache.get()
        published = cached.state.published_snapshot_names
        names = sorted(str(value) for value in snapshot_set.snapshots.values())
        return await render(
            request,
            "snapshot_set_discard.html",
            page="sets",
            mirror_set=mirror_set,
            snapshot_set=snapshot_set,
            names=names,
            published=[name for name in names if name in published],
        )

    @app.post("/sets/{set_id}/discard/{snapshot_set_id}")
    async def discard_set_submit(request: Request, set_id: int, snapshot_set_id: int) -> Response:
        async with request.app.state.sessions() as session:
            snapshot_set = await session.get(SnapshotSet, snapshot_set_id)
        if snapshot_set is None or snapshot_set.mirror_set_id != set_id:
            return RedirectResponse(f"/sets/{set_id}", status_code=303)
        cached = await request.app.state.cache.get()
        published = cached.state.published_snapshot_names
        names = sorted(str(value) for value in snapshot_set.snapshots.values())
        if any(name in published for name in names):
            return RedirectResponse(f"/sets/{set_id}/discard/{snapshot_set_id}", status_code=303)
        job = await request.app.state.runner.enqueue(
            JobType.DISCARD,
            set_id,
            author=request.state.user.username,
            params={"snapshots": names, "snapshot_set_id": snapshot_set_id},
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/publications", response_class=HTMLResponse)
    async def publications(request: Request) -> HTMLResponse:
        return await render(request, "publications.html", page="publications")

    # --- mirror sets ----------------------------------------------------

    @app.get("/sets", response_class=HTMLResponse)
    async def sets_index(request: Request) -> HTMLResponse:
        return await render(request, "sets.html", page="sets", sets=await _load_sets(request))

    @app.get("/sets/new", response_class=HTMLResponse)
    async def new_set_form(
        request: Request, preset: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        chosen = get_preset(preset)
        return await render(
            request,
            "set_new.html",
            page="sets",
            form={**_blank_form(), **(chosen.as_form() if chosen else {})},
            presets=PRESETS,
            chosen=chosen,
            error=error,
        )

    @app.post("/sets/new")
    async def new_set_submit(
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
        publish_endpoint: str = Form(""),
        public_url: str = Form(""),
        mirror_pattern: str = Form(SET_SUITE_COMPONENT),
    ) -> Response:
        form = {
            "name": name,
            "archive_url": archive_url,
            "suites": suites,
            "components": components,
            "architectures": architectures,
            "publish_prefix": publish_prefix,
            "keyrings": keyrings,
            "signing_key": signing_key,
            "filter": filter,
            "publish_endpoint": publish_endpoint,
            "public_url": public_url,
            "mirror_pattern": mirror_pattern,
        }
        async with request.app.state.sessions() as session:
            taken = (
                await session.execute(select(MirrorSet).where(MirrorSet.name == name))
            ).scalar_one_or_none()
            if taken is not None:
                return await render(
                    request,
                    "set_new.html",
                    page="sets",
                    form=form,
                    presets=PRESETS,
                    chosen=None,
                    error=i18n.gettext("A mirror set with this name already exists."),
                )
            mirror_set = MirrorSet(
                name=name,
                archive_url=archive_url,
                suites=_split(suites),
                components=_split(components),
                architectures=_split(architectures),
                keyrings=_split(keyrings),
                filter=filter or None,
                publish_prefix=publish_prefix,
                publish_endpoint=publish_endpoint or None,
                public_url=public_url.rstrip("/") or None,
                mirror_pattern=_valid_pattern(mirror_pattern),
                signing_key=signing_key or None,
                adopted=False,
            )
            session.add(mirror_set)
            session.add(AuditEntry(action="set.create", target=name))
            await session.commit()
            await session.refresh(mirror_set)

        job = await request.app.state.runner.enqueue(
            JobType.CREATE, mirror_set.id, author=request.state.user.username, params={}
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

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
        publish_endpoint: str = Form(""),
        public_url: str = Form(""),
        mirror_pattern: str = Form(SET_SUITE_COMPONENT),
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
                publish_endpoint=publish_endpoint or None,
                public_url=public_url.rstrip("/") or None,
                mirror_pattern=_valid_pattern(mirror_pattern),
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
        cached = await request.app.state.cache.get()
        present = cached.state.mirror_names
        missing = [name for name in mirror_set.expected_mirrors if name not in present]
        return await render(
            request,
            "set_detail.html",
            page="sets",
            mirror_set=mirror_set,
            snapshot_sets=snapshot_sets,
            jobs=list(jobs),
            complete_state=SnapshotSetState.COMPLETE,
            missing_mirrors=missing,
        )

    @app.post("/sets/{set_id}/create")
    async def set_create_missing(request: Request, set_id: int) -> RedirectResponse:
        """Make the mirrors a set expects but aptly does not have.

        A create job that failed part way leaves a set describing mirrors that were
        never made; without this there is no way back to a working set.
        """
        job = await request.app.state.runner.enqueue(
            JobType.CREATE, set_id, author=request.state.user.username, params={}
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/sets/{set_id}/update")
    async def set_update(request: Request, set_id: int) -> RedirectResponse:
        job = await request.app.state.runner.enqueue(
            JobType.UPDATE, set_id, author=request.state.user.username, params={}
        )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/sets/{set_id}/switch")
    async def set_switch(
        request: Request, set_id: int, snapshot_set_id: int = Form(...)
    ) -> RedirectResponse:
        job = await request.app.state.runner.enqueue(
            JobType.SWITCH,
            set_id,
            author=request.state.user.username,
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

    # --- authentication -------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_form(request: Request, error: str | None = None) -> HTMLResponse:
        return await render(request, "setup.html", page="", error=error)

    @app.post("/setup")
    async def setup_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        password_again: str = Form(...),
    ) -> Response:
        problem = _password_problem(password, password_again)
        if problem or not username.strip():
            return await render(
                request, "setup.html", page="", error=problem or i18n.gettext("Pick a username.")
            )
        async with request.app.state.sessions() as session:
            user = User(
                username=username.strip(),
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
                source="local",
            )
            session.add(user)
            session.add(AuditEntry(actor=username.strip(), action="user.create", target=username))
            await session.commit()
            await session.refresh(user)
        request.app.state.has_users = True
        return _sign_in(request, user, "/")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/", error: str | None = None) -> Response:
        if request.state.user is not None:
            return RedirectResponse("/", status_code=303)
        return await render(request, "login.html", page="", next=next, error=error)

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/"),
    ) -> Response:
        async with request.app.state.sessions() as session:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            # Verify even when the user is unknown, so a wrong name and a wrong
            # password take the same time to answer.
            ok = verify_password(user.password_hash if user else None, password)
            if user is None or not ok or not user.active:
                session.add(
                    AuditEntry(actor=username, action="login", result="denied", target="local")
                )
                await session.commit()
                return await render(
                    request,
                    "login.html",
                    page="",
                    next=next,
                    error=i18n.gettext("That username and password do not match."),
                )
            user.last_login = datetime.now(UTC)
            session.add(AuditEntry(actor=user.username, action="login", target="local"))
            await session.commit()
            await session.refresh(user)
        return _sign_in(request, user, _safe_next(next))

    @app.get("/logout")
    async def logout(request: Request) -> Response:
        response = RedirectResponse("/login", status_code=303)
        clear_session(response)
        return response

    @app.get("/auth/oidc/start")
    async def oidc_start(request: Request, next: str = "/") -> Response:
        client = request.app.state.oidc
        if client is None:
            return RedirectResponse("/login", status_code=303)
        state = new_csrf_token()
        verifier = make_verifier()
        try:
            url = await client.authorization_url(
                state=state, verifier=verifier, redirect_uri=_redirect_uri(request)
            )
        except OidcError as exc:
            return await render(request, "login.html", page="", next=next, error=str(exc))
        response = RedirectResponse(url, status_code=303)
        write_session(
            request,
            response,
            {
                "csrf": request.state.csrf or new_csrf_token(),
                "oidc_state": state,
                "oidc_verifier": verifier,
                "oidc_next": _safe_next(next),
            },
        )
        return response

    @app.get("/auth/oidc/callback")
    async def oidc_callback(
        request: Request, code: str | None = None, state: str | None = None
    ) -> Response:
        client = request.app.state.oidc
        stored = read_session(request)
        if client is None or not code:
            return RedirectResponse("/login", status_code=303)
        if not state or not csrf_matches(stored.get("oidc_state"), state):
            return await render(
                request,
                "login.html",
                page="",
                next="/",
                error=i18n.gettext("The sign-in attempt could not be verified. Try again."),
            )
        try:
            token = await client.exchange(
                code=code,
                verifier=str(stored.get("oidc_verifier", "")),
                redirect_uri=_redirect_uri(request),
            )
            claims = await client.userinfo(token)
            username = client.username_from(claims)
            role = client.role_from(claims)
        except OidcError as exc:
            return await render(request, "login.html", page="", next="/", error=str(exc))

        async with request.app.state.sessions() as session:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is None:
                user = User(username=username, role=role, source="oidc")
                session.add(user)
                session.add(
                    AuditEntry(actor=username, action="user.create", target="oidc", result=role)
                )
            elif user.source == "oidc":
                # Group membership is the provider's to decide, so re-apply it on
                # every sign-in rather than letting a local edit drift from it.
                user.role = role
            if not user.active:
                session.add(
                    AuditEntry(actor=username, action="login", result="denied", target="oidc")
                )
                await session.commit()
                return await render(
                    request,
                    "login.html",
                    page="",
                    next="/",
                    error=i18n.gettext("That account is deactivated."),
                )
            user.last_login = datetime.now(UTC)
            session.add(AuditEntry(actor=username, action="login", target="oidc"))
            await session.commit()
            await session.refresh(user)
        request.app.state.has_users = True
        return _sign_in(request, user, _safe_next(str(stored.get("oidc_next", "/"))))

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

    # --- users ----------------------------------------------------------

    @app.get("/users", response_class=HTMLResponse)
    async def users_index(request: Request, error: str | None = None) -> HTMLResponse:
        async with request.app.state.sessions() as session:
            rows = (await session.execute(select(User).order_by(User.username))).scalars().all()
        return await render(
            request, "users.html", page="users", users=list(rows), roles=list(UserRole), error=error
        )

    @app.post("/users/{user_id}/role")
    async def users_role(request: Request, user_id: int, role: str = Form(...)) -> Response:
        async with request.app.state.sessions() as session:
            user = await session.get(User, user_id)
            if user is None:
                return RedirectResponse("/users", status_code=303)
            if await _would_orphan_admins(session, user, new_role=_valid_role(role)):
                return RedirectResponse("/users?error=last_admin", status_code=303)
            user.role = _valid_role(role)
            session.add(
                AuditEntry(
                    actor=request.state.user.username,
                    action="user.role",
                    target=user.username,
                    result=user.role,
                )
            )
            await session.commit()
        return RedirectResponse("/users", status_code=303)

    @app.post("/users/{user_id}/active")
    async def users_active(request: Request, user_id: int, active: str = Form("0")) -> Response:
        wanted = active in ("1", "true", "on")
        async with request.app.state.sessions() as session:
            user = await session.get(User, user_id)
            if user is None:
                return RedirectResponse("/users", status_code=303)
            if not wanted and await _would_orphan_admins(session, user, deactivating=True):
                return RedirectResponse("/users?error=last_admin", status_code=303)
            user.active = wanted
            session.add(
                AuditEntry(
                    actor=request.state.user.username,
                    action="user.active",
                    target=user.username,
                    result="active" if wanted else "inactive",
                )
            )
            await session.commit()
        return RedirectResponse("/users", status_code=303)

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


async def _snapshot_context(request: Request, state: Any) -> dict[str, dict[str, Any]]:
    """Say, for each snapshot aptly holds, where it belongs and whether it can go.

    Without this the snapshots screen is a dead end: a list of names with no way to
    tell which set made them or why one cannot be deleted.
    """
    published = state.published_snapshot_names
    where_published = {
        source.name: f"{pub.prefix}/{pub.distribution}"
        for pub in state.published
        for source in pub.sources
    }

    sets = await _load_sets(request)
    owner: dict[str, dict[str, Any]] = {}
    for mirror_set in sets:
        for snapshot_set in mirror_set.snapshot_sets:
            for name in snapshot_set.snapshots.values():
                owner[str(name)] = {
                    "mirror_set_id": mirror_set.id,
                    "mirror_set": mirror_set.name,
                    "snapshot_set_id": snapshot_set.id,
                    "taken_at": snapshot_set.taken_at,
                }

    context: dict[str, dict[str, Any]] = {}
    for snapshot in state.snapshots:
        entry: dict[str, Any] = {
            "mirror_set_id": None,
            "mirror_set": None,
            "snapshot_set_id": None,
            "taken_at": None,
            "published_at": where_published.get(snapshot.name),
            "blocked": None,
        }
        entry.update(owner.get(snapshot.name, {}))
        if snapshot.name in published:
            entry["blocked"] = "published"
        elif entry["snapshot_set_id"] is not None:
            # Removing one snapshot would leave its set unable to publish.
            entry["blocked"] = "in_set"
        context[snapshot.name] = entry
    return context


def _managed_map(sets: list[MirrorSet]) -> dict[str, str]:
    return {name: item.name for item in sets for name in item.expected_mirrors}


def _sign_in(request: Request, user: User, destination: str) -> Response:
    response = RedirectResponse(destination, status_code=303)
    write_session(request, response, {"uid": user.id, "csrf": new_csrf_token()})
    return response


def _safe_next(value: str) -> str:
    """Only ever redirect within this site, never to a URL an attacker supplied."""
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _redirect_uri(request: Request) -> str:
    base = request.app.state.oidc_config.base_url
    if base:
        return f"{base.rstrip('/')}/auth/oidc/callback"
    return str(request.url_for("oidc_callback"))


def _password_problem(password: str, again: str) -> str | None:
    if password != again:
        return i18n.gettext("The two passwords are not the same.")
    if describe_weakness(password) == "too_short":
        return i18n.gettext("Use at least 10 characters.")
    return None


async def _session_user(request: Request, session_data: dict[str, Any]) -> User | None:
    uid = session_data.get("uid")
    if not uid or not hasattr(request.app.state, "sessions"):
        return None
    async with request.app.state.sessions() as session:
        user = await session.get(User, int(uid))
    return user if user and user.active else None


async def _check_csrf(request: Request) -> None:
    """Every form post carries a token tied to the session cookie."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.url.path.startswith("/static"):
        return
    form = await request.form()
    submitted = form.get("csrf_token")
    if not csrf_matches(getattr(request.state, "csrf", None), str(submitted or "")):
        raise HTTPException(status_code=400, detail="csrf")


async def _reconcile_admin(sessions: Any, settings: Settings) -> None:
    """Keep the one local account in step with the configuration.

    Its password lives in the environment rather than the database so it can be
    rotated by redeploying, and so no local credential is ever baked into the image.
    """
    if not settings.admin_username or not settings.admin_password:
        return
    async with sessions() as session:
        user = (
            await session.execute(select(User).where(User.username == settings.admin_username))
        ).scalar_one_or_none()
        if user is None:
            session.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role=UserRole.ADMIN,
                    source="local",
                )
            )
        else:
            # Configuration wins, including over a role someone changed by hand.
            user.password_hash = hash_password(settings.admin_password)
            user.role = UserRole.ADMIN
            user.active = True
            user.source = "local"
        await session.commit()


async def _any_user_exists(sessions: Any) -> bool:
    async with sessions() as session:
        found = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    return found is not None


async def _resolve_secret_key(sessions: Any, settings: Settings) -> str:
    """Keep one signing key so a restart does not sign everybody out."""
    if settings.secret_key:
        return settings.secret_key
    async with sessions() as session:
        row = await session.get(AppSetting, "secret_key")
        if row is None:
            row = AppSetting(key="secret_key", value=secrets.token_urlsafe(48))
            session.add(row)
            await session.commit()
        return str(row.value)


def _valid_pattern(value: str) -> str:
    return value if value in PATTERNS else SET_SUITE_COMPONENT


def _valid_role(value: str) -> str:
    try:
        return UserRole(value)
    except ValueError:
        return UserRole.VIEWER


async def _would_orphan_admins(
    session: Any, user: User, *, new_role: str | None = None, deactivating: bool = False
) -> bool:
    """Refuse the change that would leave nobody able to administer the instance."""
    if user.role != UserRole.ADMIN:
        return False
    if new_role is not None and new_role == UserRole.ADMIN:
        return False
    others = (
        (
            await session.execute(
                select(User).where(
                    User.role == UserRole.ADMIN, User.active.is_(True), User.id != user.id
                )
            )
        )
        .scalars()
        .all()
    )
    return not others and (deactivating or new_role != UserRole.ADMIN)


def _blank_form() -> dict[str, str]:
    return {
        "name": "",
        "archive_url": "",
        "suites": "",
        "components": "",
        "architectures": "amd64",
        "publish_prefix": "",
        "keyrings": "",
        "signing_key": "",
        "filter": "",
        "publish_endpoint": "",
        "public_url": "",
        "mirror_pattern": SET_SUITE_COMPONENT,
    }


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
        "create": i18n.gettext("create mirrors"),
        "update": i18n.gettext("sync and snapshot"),
        "switch": i18n.gettext("publish"),
        "discard": i18n.gettext("discard snapshots"),
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
        "viewer": i18n.gettext("viewer"),
        "operator": i18n.gettext("operator"),
        "admin": i18n.gettext("administrator"),
    }.get(value, value)


app = create_app()
