"""App factory, middleware and entry points.

prognos-api serve                      # uvicorn on :8000
prognos-api create-user --email a@b.c --tenant acme-logistics --role fleet_manager
                                        # password read from stdin, never argv
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from prognos_api import errors
from prognos_api.config import Settings
from prognos_api.deps import AppState, load_policy
from prognos_api.ratelimit import RateLimiter
from prognos_api.routers import alerts, auth, live, vehicles, work_orders
from prognos_api.security import TokenService, hash_password

log = logging.getLogger("prognos_api")

REQUESTS = Counter("prognos_api_requests", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "prognos_api_request_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)  # fmt: skip

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
            settings.postgres_dsn, min_size=1, max_size=10, open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )  # fmt: skip
        await pool.open(wait=True, timeout=30)
        client = aioredis.Redis(host=settings.redis_host, port=settings.redis_port,
                                password=settings.redis_password, socket_timeout=2)  # fmt: skip
        tokens = TokenService(
            settings.jwt_private_key_pem, key_id=settings.jwt_key_id,
            issuer=settings.jwt_issuer, audience=settings.jwt_audience,
            ttl_minutes=settings.access_token_minutes,
        )  # fmt: skip
        if tokens.ephemeral:
            log.warning("no JWT_PRIVATE_KEY configured: using an ephemeral dev key "
                        "(tokens stop working on restart)")  # fmt: skip
        app.state.prognos = AppState(settings, pool, client, tokens, await load_policy(pool),
                                     RateLimiter(client))  # fmt: skip
        try:
            yield
        finally:
            await client.aclose()
            await pool.close()

    app = FastAPI(
        title="Prognos API", version="1.0.0", lifespan=lifespan,
        description="Predictive maintenance for connected fleets. All data is tenant-scoped.",
    )  # fmt: skip
    errors.install(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
        )  # fmt: skip

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid[:64]
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")  # template, not raw path: bounded labels
        LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        response.headers["X-Request-ID"] = request.state.request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    for module in (auth, vehicles, alerts, work_orders, live):
        app.include_router(module.router)
    return app


async def create_user(dsn: str, email: str, tenant_slug: str | None, roles: list[str],
                      display_name: str, password: str) -> str:  # fmt: skip
    async with await AsyncConnection.connect(dsn) as conn, conn.transaction():
        tenant_id = None
        if tenant_slug:
            row = await (await conn.execute("SELECT tenant_id FROM tenants WHERE slug = %s",
                                            (tenant_slug,))).fetchone()  # fmt: skip
            if row is None:
                raise SystemExit(f"unknown tenant slug: {tenant_slug}")
            tenant_id = row[0]
        row = await (await conn.execute(
            "INSERT INTO users (tenant_id, email, display_name, password_hash)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash,"
            " tenant_id = EXCLUDED.tenant_id, display_name = EXCLUDED.display_name"
            " RETURNING user_id::text",
            (tenant_id, email, display_name, hash_password(password)),
        )).fetchone()  # fmt: skip
        assert row is not None
        for role in roles:
            await conn.execute("INSERT INTO user_roles VALUES (%s, %s) ON CONFLICT DO NOTHING",
                               (row[0], role))  # fmt: skip
        return str(row[0])


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")  # fmt: skip
    parser = argparse.ArgumentParser(prog="prognos-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=os.getenv("API_HOST", "0.0.0.0"))  # noqa: S104 - in a container
    serve.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8000")))
    user = sub.add_parser("create-user")
    user.add_argument("--email", required=True)
    user.add_argument("--tenant", help="tenant slug (omit for platform staff)")
    user.add_argument("--role", action="append", required=True)
    user.add_argument("--name", default="")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                    access_log=False)  # fmt: skip
        return 0
    password = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass()
    if len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    user_id = asyncio.run(create_user(Settings.from_env().postgres_dsn, args.email, args.tenant,
                                      args.role, args.name or args.email, password))  # fmt: skip
    print(user_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
