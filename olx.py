"""Pobieranie ogłoszeń z publicznego API OLX.pl."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from curl_cffi import requests as cffi_requests

API_URL = "https://www.olx.pl/api/v1/offers/"
# User-Agent i reszta nagłówków ustawiane są przez impersonate="chrome" (spójnie z odciskiem TLS)
HEADERS = {"Accept": "application/json", "Accept-Language": "pl-PL,pl;q=0.9"}


class OLXError(Exception):
    """Błąd pobierania danych z OLX."""


@dataclass
class Offer:
    id: int
    url: str
    title: str
    description: str
    price: float | None
    price_label: str
    currency: str
    city: str
    region: str
    created: str
    state: str
    params: dict[str, str] = field(default_factory=dict)
    photos: list[str] = field(default_factory=list)


def _clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _parse(raw: dict) -> Offer:
    price, price_label, currency, state = None, "brak ceny", "PLN", ""
    params: dict[str, str] = {}
    for p in raw.get("params", []):
        val = p.get("value") or {}
        if p.get("key") == "price":
            price = val.get("value")
            price_label = val.get("label") or price_label
            currency = val.get("currency") or currency
        else:
            label = val.get("label") or ""
            params[p.get("name", p.get("key", ""))] = label
            if p.get("key") == "state":
                state = val.get("key", "")
    loc = raw.get("location") or {}
    photos = [
        ph["link"].replace("{width}", "800").replace("{height}", "600")
        for ph in raw.get("photos", [])
        if ph.get("link")
    ]
    return Offer(
        id=raw["id"],
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        description=_clean_html(raw.get("description", "")),
        price=price,
        price_label=price_label,
        currency=currency,
        city=(loc.get("city") or {}).get("name", ""),
        region=(loc.get("region") or {}).get("name", ""),
        created=raw.get("created_time", ""),
        state=state,
        params=params,
        photos=photos,
    )


def fetch_offers(search: dict, limit: int = 40) -> list[Offer]:
    """Zwraca najnowsze ogłoszenia dla jednego wyszukiwania z configu."""
    q = {
        "offset": 0,
        "limit": limit,
        "sort_by": "created_at:desc",
    }
    if search.get("query"):
        q["query"] = search["query"]
    if search.get("category_id"):
        q["category_id"] = search["category_id"]
    if search.get("state"):
        q["filter_enum_state[0]"] = search["state"]

    try:
        r = cffi_requests.get(API_URL, params=q, headers=HEADERS, timeout=30, impersonate="chrome")
    except Exception as e:  # błędy sieciowe curl_cffi
        raise OLXError(f"błąd sieci: {e}") from e
    if r.status_code != 200:
        raise OLXError(f"HTTP {r.status_code}")
    try:
        payload = r.json()
    except ValueError as e:
        raise OLXError("odpowiedź nie jest JSON-em") from e
    # Pomijamy ogłoszenia promowane (zawsze na górze, często stare)
    promoted = set((payload.get("metadata") or {}).get("promoted") or [])
    return [_parse(o) for i, o in enumerate(payload.get("data", [])) if i not in promoted]
