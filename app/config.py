from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application settings using Pydantic BaseSettings."""
    OSRM_BASE_URL: str = "http://localhost:5000"
    APP_NAME: str = "OSRM API Gateway"
    DEBUG: bool = False

    class Config:
        env_file = ".env"

settings = Settings()
