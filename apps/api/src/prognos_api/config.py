"""Settings from the environment (same variable names as docker-compose / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SECRET = "change-me-local-only"  # noqa: S105 - the sentinel we refuse in production


@dataclass(frozen=True)
class Settings:
    env: str = "development"  # "production" enables fail-fast secret checks
    postgres_dsn: str = ""
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "prognos"
    clickhouse_password: str = ""
    clickhouse_db: str = "telemetry"
    jwt_issuer: str = "prognos"
    jwt_audience: str = "prognos-api"
    jwt_private_key_pem: str | None = None  # RS256 signing key; generated if absent (dev only)
    jwt_key_id: str = "prognos-dev"
    access_token_minutes: int = 15
    rate_limit_per_minute: int = 600  # per user
    login_attempts_per_minute: int = 10  # per client address + account
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    model_version: str = "failure-7d-v3"
    # Default ranking for /v1/vehicles/at-risk: "rules" while the model runs in shadow
    # (ADR-008); callers can ask for ?source=model explicitly.
    risk_source: str = "rules"

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        dsn = e.get("DATABASE_URL") or (
            f"host={e.get('POSTGRES_HOST', 'localhost')} port={e.get('POSTGRES_PORT', '5432')} "
            f"dbname={e.get('POSTGRES_DB', 'prognos')} user={e.get('POSTGRES_USER', 'prognos')} "
            f"password={e.get('POSTGRES_PASSWORD', '')}"
        )
        key = e.get("JWT_PRIVATE_KEY")
        if not key and e.get("JWT_PRIVATE_KEY_FILE"):
            with open(e["JWT_PRIVATE_KEY_FILE"]) as fh:
                key = fh.read()
        return cls(
            env=e.get("APP_ENV", cls.env),
            postgres_dsn=dsn,
            redis_host=e.get("REDIS_HOST", cls.redis_host),
            redis_port=int(e.get("REDIS_PORT", cls.redis_port)),
            redis_password=e.get("REDIS_PASSWORD") or None,
            clickhouse_host=e.get("CLICKHOUSE_HOST", cls.clickhouse_host),
            clickhouse_port=int(e.get("CLICKHOUSE_HTTP_PORT", cls.clickhouse_port)),
            clickhouse_user=e.get("CLICKHOUSE_USER", cls.clickhouse_user),
            clickhouse_password=e.get("CLICKHOUSE_PASSWORD", ""),
            clickhouse_db=e.get("CLICKHOUSE_DB", cls.clickhouse_db),
            jwt_issuer=e.get("JWT_ISSUER", cls.jwt_issuer),
            jwt_audience=e.get("JWT_AUDIENCE", cls.jwt_audience),
            jwt_private_key_pem=key or None,
            jwt_key_id=e.get("JWT_KEY_ID", cls.jwt_key_id),
            access_token_minutes=int(e.get("ACCESS_TOKEN_MINUTES", cls.access_token_minutes)),
            rate_limit_per_minute=int(
                e.get("API_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute)
            ),
            login_attempts_per_minute=int(
                e.get("LOGIN_ATTEMPTS_PER_MINUTE", cls.login_attempts_per_minute)
            ),
            cors_origins=tuple(o for o in e.get("CORS_ORIGINS", "").split(",") if o),
            model_version=e.get("MODEL_VERSION", cls.model_version),
            risk_source=e.get("RISK_SOURCE", cls.risk_source),
        )

    def validate(self) -> None:
        """Refuse to run in production with development defaults."""
        if self.env != "production":
            return
        problems = []
        if not self.jwt_private_key_pem:
            problems.append("JWT_PRIVATE_KEY(_FILE) is required in production")
        if DEFAULT_SECRET in self.postgres_dsn or self.clickhouse_password == DEFAULT_SECRET:
            problems.append("database passwords still use the local development default")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must list origins explicitly in production")
        if problems:
            raise RuntimeError("unsafe production configuration: " + "; ".join(problems))
