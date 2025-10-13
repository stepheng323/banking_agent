from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    meta_verify_token: str
    meta_access_token: str
    meta_phone_number_id: str
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
