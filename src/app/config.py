from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings using Pydantic BaseSettings."""
    OSRM_BASE_URL: str = "http://localhost:5000"
    OSRM_API_URL: str = "http://localhost:8080"
    APP_NAME: str = "OSRM API Gateway"
    DEBUG: bool = False

    # Rate Limiting Settings
    RATE_LIMIT_ROUTE: str = "600/minute"
    RATE_LIMIT_MATRIX: str = "300/minute"
    RATE_LIMIT_MATCH: str = "600/minute"
    RATE_LIMIT_TRIP: str = "300/minute"
    RATE_LIMIT_VRP: str = "100/minute"
    RATE_LIMIT_NEAREST: str = "600/minute"
    RATE_LIMIT_TILE: str = "600/minute"

    # Redis Cache Settings
    REDIS_URL: str = ""
    REDIS_TTL: int = 900
    REDIS_MAXSIZE: int = 1024

    # L1 In-Memory Cache Settings
    L1_CACHE_TTL: int = 900
    L1_CACHE_MAXSIZE: int = 1024

    # OpenTelemetry Tracing
    OTLP_ENDPOINT: str = ""

    # OSRM Client Settings
    OSRM_CLIENT_TIMEOUT: int = 30
    OSRM_RETRY_ATTEMPTS: int = 3
    OSRM_RETRY_MIN: int = 1
    OSRM_RETRY_MAX: int = 10

    # Health Check Settings
    # Must stay well below deploy/docker/Dockerfile's HEALTHCHECK --timeout (currently 8s):
    # this probe blocks the /health response, so it needs enough margin left over
    # for connection setup and response overhead, or Docker kills the check before
    # the app ever gets to reply "degraded" — exactly when OSRM is actually down.
    HEALTH_CHECK_TIMEOUT: int = 2
    HEALTH_CHECK_COORDS: str = "0,0;0,0"

    # VRP / Matrix Settings
    VRP_CHUNK_SIZE: int = 80
    MATRIX_BATCH_SIZE: int = 500
    VRP_HYSTERESIS_M: float = 2000.0
    VRP_SANITY_LIMIT_M: float = 50000.0

    # Peak memory for an optimization request is stops x concurrent solves: one
    # 2000-stop solve peaked at 277 MB and four together reached 615 MB on a 2 GB
    # host. VRP_MAX_STOPS bounds the first factor (422 beyond it) and
    # VRP_MAX_CONCURRENCY the second -- but the semaphore lives in one process, so
    # node-wide concurrency is WORKERS x VRP_MAX_CONCURRENCY. Raise either one only
    # against a measured RSS ceiling for the target host.
    VRP_MAX_STOPS: int = 2000
    VRP_MAX_CONCURRENCY: int = 1
    VRP_QUEUE_TIMEOUT: float = 10.0

    # osrm-routed rejects a table request when sources x destinations exceeds
    # --max-table-size squared (default 100, so 10 000 cells) -- the budget is on
    # the product, not the coordinate count, which is why a 1 x 500 depot-to-stop
    # batch passes while a 101-coordinate symmetric matrix does not. Enforcing the
    # same number here turns a pass-through 400 into a 422 that names the limit.
    # Changing it means passing --max-table-size to osrm-routed in BOTH
    # deploy/docker/docker-compose.yml and deploy/freebsd/osrm-routed, as the
    # square root of this value.
    MATRIX_MAX_CELLS: int = 10000

    # A 2000-stop solve is ~25 sequential /trip round trips, and that serialised
    # I/O -- not CPU -- is what made /vrp p99 366 ms against /route p95 91 ms.
    # The chunks fan out instead, bounded so one solve cannot saturate a 2-core
    # engine. Concurrent /trip calls against osrm-routed node-wide are
    # WORKERS x VRP_MAX_CONCURRENCY x VRP_CHUNK_CONCURRENCY: raise this only
    # against measured engine latency, since past roughly twice the engine's core
    # count the calls just queue there instead of here.
    VRP_CHUNK_CONCURRENCY: int = 4

    # Metrics
    METRICS_ENDPOINT: str = "/metrics"

    # Logging
    APPEND_TO_STDERR: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

