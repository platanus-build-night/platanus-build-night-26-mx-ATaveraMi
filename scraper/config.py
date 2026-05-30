"""Configuración del scraper (desde variables de entorno / .env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    concurrency: int = int(os.getenv("SCRAPER_CONCURRENCY", "5"))
    per_site_max_pages: int = int(os.getenv("SCRAPER_MAX_PAGES", "25"))
    request_timeout: float = float(os.getenv("SCRAPER_TIMEOUT", "15"))      # read timeout por request
    connect_timeout: float = float(os.getenv("SCRAPER_CONNECT_TIMEOUT", "5"))  # conexión: muere rápido
    site_budget: float = float(os.getenv("SCRAPER_SITE_BUDGET", "90"))      # tope de tiempo por desarrolladora
    max_markdown_chars: int = int(os.getenv("SCRAPER_MAX_MD_CHARS", "18000"))
    output_path: str = os.getenv("SCRAPER_OUTPUT", "data/scraped.json")


settings = Settings()
