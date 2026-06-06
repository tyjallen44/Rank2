from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_places_api_key: str = ""
    yelp_api_key: str = ""
    openai_api_key: str = ""
    db_path: str = "rank2.duckdb"


settings = Settings()
