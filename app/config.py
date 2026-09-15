from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    supabase_url: str
    supabase_service_role_key: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    cors_origins: str = "*"
    cron_counter_secret: str = ""
    public_base_url: str = ""
    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    line_seat_lookup_keyword: str = "我坐哪啊？"


settings = Settings()
