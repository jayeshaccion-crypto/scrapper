from pathlib import Path
from urllib.parse import urlparse

import yaml

from pydantic import BaseModel, field_validator, model_validator


def _load_site_domains() -> dict[str, str]:
    base = Path(__file__).resolve().parent.parent
    cfg = base / "config" / "sites.yaml"
    if not cfg.exists():
        return {}
    with open(cfg, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    domains: dict[str, str] = {}
    for site in data.get("sites", []):
        urls = site.get("start_urls", [])
        if urls:
            domain = urlparse(urls[0]).netloc
            if domain:
                domains[site["name"]] = domain
    return domains


SITE_DOMAINS: dict[str, str] = _load_site_domains()


class PropertyListing(BaseModel):
    source_site: str
    listing_id: str | None = None
    url: str | None = None
    title: str
    price_inr: float | None = None
    price_per_sqft_inr: float | None = None
    area_sqft: float | None = None
    bhk: int | None = None
    property_type: str | None = None
    furnishing: str | None = None
    floor: str | None = None
    total_floors: int | None = None
    age_years: int | None = None
    locality: str | None = None
    city: str | None = "Noida"
    seller_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    amenities: list[str] | None = None
    scraped_at_utc: str | None = None
    schema_version: str | None = None

    @field_validator("price_inr")
    @classmethod
    def price_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"price_inr must be > 0, got {v}")
        return v

    @field_validator("area_sqft")
    @classmethod
    def area_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"area_sqft must be > 0, got {v}")
        return v

    @field_validator("bhk")
    @classmethod
    def bhk_must_be_in_range(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 10):
            raise ValueError(f"bhk must be between 0 and 10, got {v}")
        return v

    @field_validator("title")
    @classmethod
    def title_must_be_non_empty(cls, v: str | None) -> str:
        if not v or not v.strip():
            raise ValueError("title must be a non-empty string")
        return v.strip()

    @field_validator("url")
    @classmethod
    def url_must_be_valid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"url must be HTTP/HTTPS, got {parsed.scheme}")
        return v

    @model_validator(mode="after")
    def url_matches_site_domain(self) -> "PropertyListing":
        if self.url is None:
            return self
        expected = SITE_DOMAINS.get(self.source_site)
        if expected:
            parsed = urlparse(self.url)
            if parsed.netloc and parsed.netloc != expected:
                raise ValueError(
                    f"url domain '{parsed.netloc}' does not match expected "
                    f"domain '{expected}' for source_site '{self.source_site}'"
                )
        return self
