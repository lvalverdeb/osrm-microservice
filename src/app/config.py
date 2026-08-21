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

    # Metrics
    METRICS_ENDPOINT: str = "/metrics"

    # Logging
    APPEND_TO_STDERR: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

