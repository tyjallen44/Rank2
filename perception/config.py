from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: str = "rank2.duckdb"
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
