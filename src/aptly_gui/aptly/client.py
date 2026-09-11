from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import httpx

from .models import (
    Mirror,
    MirrorSpec,
    Published,
    Signing,
    Snapshot,
    Storage,
    Task,
    TaskState,
)


class AptlyError(RuntimeError):
    """An error reported by aptly itself, rather than a transport failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TaskFailed(AptlyError):
    def __init__(self, task: Task, output: str) -> None:
        super().__init__(f"{task.name}: {output or 'no output'}")
        self.task = task
        self.output = output


def encode_prefix(prefix: str) -> str:
    """Encode a publish prefix for use in a URL path.

    aptly maps `_` to `__` and `/` to `_`, and spells the root prefix `:.`.
    """
    if prefix in ("", ".", "/"):
        return ":."
    return prefix.strip("/").replace("_", "__").replace("/", "_")


class AptlyClient:
    """Async client for the subset of aptly's REST API that aptly-gui needs."""

    def __init__(
        self,
        base_url: str = "http://localhost",
        *,
        socket_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        transport = httpx.AsyncHTTPTransport(uds=socket_path) if socket_path else None
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
        )

    async def __aenter__(self) -> AptlyClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise AptlyError(_error_message(response), status_code=response.status_code)
        if not response.content:
            return None
        return response.json()

    # --- server ---------------------------------------------------------

    async def version(self) -> str:
        payload = await self._request("GET", "/api/version")
        # aptly 1.6.1 returns the version with a trailing newline inside the string.
        return str(payload["Version"]).strip()

    async def storage(self) -> Storage:
        return Storage.parse(await self._request("GET", "/api/storage"))

    async def is_ready(self) -> bool:
        try:
            await self._request("GET", "/api/ready")
        except (AptlyError, httpx.HTTPError):
            return False
        return True

    # --- tasks ----------------------------------------------------------

    async def list_tasks(self) -> list[Task]:
        return [Task.parse(t) for t in await self._request("GET", "/api/tasks")]

    async def get_task(self, task_id: int) -> Task:
        return Task.parse(await self._request("GET", f"/api/tasks/{task_id}"))

    async def task_output(self, task_id: int) -> str:
        return str(await self._request("GET", f"/api/tasks/{task_id}/output"))

    async def wait_for_task(
        self,
        task_id: int,
        *,
        poll_interval: float = 2.0,
        timeout: float | None = None,
    ) -> Task:
        """Poll until the task finishes, raising TaskFailed if aptly reports an error.

        The task queue lives in aptly's memory: if `aptly api serve` restarts the id
        disappears and this raises AptlyError. Callers recovering a job must fall back
        to comparing real state, not to task ids.
        """
        waited = 0.0
        while True:
            task = await self.get_task(task_id)
            if task.state.finished:
                if task.state is TaskState.FAILED:
                    raise TaskFailed(task, await self.task_output(task_id))
                return task
            if timeout is not None and waited >= timeout:
                raise TimeoutError(f"task {task_id} still {task.state.name} after {waited:.0f}s")
            await asyncio.sleep(poll_interval)
            waited += poll_interval

    # --- mirrors --------------------------------------------------------

    async def list_mirrors(self) -> list[Mirror]:
        return [Mirror.parse(m) for m in await self._request("GET", "/api/mirrors")]

    async def get_mirror(self, name: str) -> Mirror:
        return Mirror.parse(await self._request("GET", f"/api/mirrors/{name}"))

    async def create_mirror(self, spec: MirrorSpec) -> Mirror:
        return Mirror.parse(await self._request("POST", "/api/mirrors", json=spec.payload()))

    async def update_mirror(self, spec: MirrorSpec) -> Task:
        """Sync a mirror, taking the whole spec because aptly forgets `Keyrings`."""
        payload = await self._request(
            "PUT",
            f"/api/mirrors/{spec.name}",
            params={"_async": 1},
            json=spec.payload(),
        )
        return Task.parse(payload)

    async def delete_mirror(self, name: str, *, force: bool = False) -> Task:
        params: dict[str, Any] = {"_async": 1}
        if force:
            params["force"] = 1
        return Task.parse(await self._request("DELETE", f"/api/mirrors/{name}", params=params))

    # --- snapshots ------------------------------------------------------

    async def list_snapshots(self) -> list[Snapshot]:
        return [Snapshot.parse(s) for s in await self._request("GET", "/api/snapshots")]

    async def create_snapshot_from_mirror(
        self, mirror: str, name: str, *, description: str = ""
    ) -> Snapshot:
        payload = await self._request(
            "POST",
            f"/api/mirrors/{mirror}/snapshots",
            json={"Name": name, "Description": description},
        )
        return Snapshot.parse(payload)

    async def delete_snapshot(self, name: str, *, force: bool = False) -> None:
        params = {"force": 1} if force else None
        await self._request("DELETE", f"/api/snapshots/{name}", params=params)

    async def snapshot_diff(self, left: str, right: str) -> list[dict[str, Any]]:
        return list(await self._request("GET", f"/api/snapshots/{left}/diff/{right}"))

    # --- publish --------------------------------------------------------

    async def list_published(self) -> list[Published]:
        return [Published.parse(p) for p in await self._request("GET", "/api/publish")]

    async def publish_snapshots(
        self,
        prefix: str,
        distribution: str,
        sources: dict[str, str],
        *,
        architectures: list[str],
        signing: Signing,
    ) -> Task:
        """Create a publication mapping component -> snapshot."""
        payload = await self._request(
            "POST",
            f"/api/publish/{encode_prefix(prefix)}",
            params={"_async": 1},
            json={
                "SourceKind": "snapshot",
                "Sources": [{"Component": c, "Name": n} for c, n in sources.items()],
                "Distribution": distribution,
                "Architectures": architectures,
                "Signing": signing.payload(),
            },
        )
        return Task.parse(payload)

    async def switch_published(
        self,
        prefix: str,
        distribution: str,
        sources: dict[str, str],
        *,
        signing: Signing,
    ) -> Task:
        """Point an existing publication at different snapshots.

        Note the asymmetry with publish_snapshots: creating takes `Sources`, switching
        takes `Snapshots`.
        """
        payload = await self._request(
            "PUT",
            f"/api/publish/{encode_prefix(prefix)}/{distribution}",
            params={"_async": 1},
            json={
                "Snapshots": [{"Component": c, "Name": n} for c, n in sources.items()],
                "Signing": signing.payload(),
            },
        )
        return Task.parse(payload)

    async def drop_published(self, prefix: str, distribution: str, *, force: bool = False) -> Task:
        params: dict[str, Any] = {"_async": 1}
        if force:
            params["force"] = 1
        payload = await self._request(
            "DELETE",
            f"/api/publish/{encode_prefix(prefix)}/{distribution}",
            params=params,
        )
        return Task.parse(payload)

    # --- maintenance ----------------------------------------------------

    async def db_cleanup(self) -> Task:
        return Task.parse(await self._request("POST", "/api/db/cleanup", params={"_async": 1}))


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])
    return str(payload)
