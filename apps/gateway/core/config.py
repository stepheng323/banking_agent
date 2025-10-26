from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    meta_verify_token: str = "development_token"
    meta_access_token: str = "development_access_token"
    meta_phone_number_id: str = "development_phone_id"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    redis_url: str = "redis://localhost:6379"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
