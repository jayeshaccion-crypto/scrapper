from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


SITE_DOMAINS: dict[str, str] = {
    "99acres": "www.99acres.com",
    "magicbricks": "www.magicbricks.com",
    "squareyards": "www.squareyards.com",
    "olx": "www.olx.in",
    "proptiger": "www.proptiger.com",
    "proptiger-flats": "www.proptiger.com",
    "nobroker": "www.nobroker.in",
}


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
    def url_must_be_valid(cls, v: str | None, info) -> str | None:
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
