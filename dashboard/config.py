from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dashboard_base_url: str = ""
    dashboard_username: str = ""
    dashboard_password: str = ""
    dashboard_api_base_url: str = ""
    dashboard_api_key: str = ""


settings = DashboardSettings()
