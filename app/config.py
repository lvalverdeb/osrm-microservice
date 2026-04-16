from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application settings using Pydantic BaseSettings."""
    OSRM_BASE_URL: str = "http://localhost:5000"
    APP_NAME: str = "OSRM API Gateway"
    DEBUG: bool = False
    
    # Rate Limiting Settings
    RATE_LIMIT_ROUTE: str = "600/minute"
    RATE_LIMIT_MATRIX: str = "300/minute"
    RATE_LIMIT_MATCH: str = "600/minute"
    RATE_LIMIT_TRIP: str = "300/minute"
    RATE_LIMIT_VRP: str = "100/minute"

    class Config:
        env_file = ".env"

settings = Settings()

