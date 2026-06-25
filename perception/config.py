from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: str = "rank2.duckdb"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Google Places API key — grounds the AI Visibility Score in real Google
    # ratings/review counts. Without it, Google reads come back unverified.
    google_places_api_key: str = ""


settings = Settings()
