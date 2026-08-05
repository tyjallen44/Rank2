from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Legacy DuckDB file path — retained only for the one-time migration/copy
    # script and older tests. The live app talks to Postgres via database_url.
    db_path: str = "rank2.duckdb"
    # Postgres connection string (Neon in prod, local Homebrew PG in dev).
    # Sourced from env DATABASE_URL / Secret Manager on Cloud Run.
    database_url: str = "postgresql://localhost:5432/rank2"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Google Places API key — grounds the AI Visibility Score in real Google
    # ratings/review counts. Without it, Google reads come back unverified.
    google_places_api_key: str = ""
    # Web search (Anthropic native tool) — refreshes recognitions, rankings, and
    # recent events for the qualitative tiers. Requires web search to be enabled
    # for the API key's org in the Anthropic Console. Degrades gracefully if not.
    enable_web_search: bool = True
    web_search_max_uses: int = 5
    # System-wide weighted reputation — review-count-weighted blend across all of
    # a system's locations. Cap bounds per-system Places spend.
    enable_system_reputation: bool = True
    system_reputation_max_locations: int = 40


settings = Settings()
