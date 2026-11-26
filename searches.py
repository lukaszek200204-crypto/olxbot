"""Wczytywanie wyszukiwań z prostego pliku tekstowego searches.txt."""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = {
    "laptopy": (1199, "Laptopy"),
    "konsole": (1604, "Konsole"),
    "tablety": (2194, "Tablety"),
    "telefony": (1839, "Telefony"),
}

STATES = {
    "uszkodzony": ["damaged"],
    "uszkodzone": ["damaged"],
    "uszkodzona": ["damaged"],
    "nieuszkodzony": ["used", "new"],
    "nieuszkodzone": ["used", "new"],
    "nieuszkodzona": ["used", "new"],
    "sprawny": ["used", "new"],
    "sprawne": ["used", "new"],
    "wszystkie": [],
    "dowolny": [],
}


def _parse_line(line: str, lineno: int) -> dict | None:
    parts = [p.strip() for p in line.split("|")]
    what = parts[0]
    if not what:
        raise ValueError("brak frazy")
    state_word = (parts[1] if len(parts) > 1 and parts[1] else "uszkodzony").lower()
    if state_word not in STATES:
        raise ValueError(f"nieznany stan '{parts[1]}' (użyj: uszkodzony / nieuszkodzony / wszystkie)")

    search: dict = {"states": STATES[state_word]}

    m = re.match(r"kategoria\s*:\s*(.+)$", what, re.I)
    if m:
        cat = m.group(1).strip()
        low = cat.lower()
        if low.isdigit():
            search["category_id"], label = int(low), f"Kategoria {low}"
        elif low in CATEGORIES:
            search["category_id"], label = CATEGORIES[low]
        elif "olx.pl" in low or "/" in low:
            # Link do kategorii skopiowany z przeglądarki, np. https://www.olx.pl/elektronika/fotografia/
            search["category_url"] = cat
            path = [p for p in re.sub(r"^https?://[^/]+", "", cat.split("?")[0]).split("/") if p]
            label = path[-1].replace("-", " ").capitalize() if path else "Kategoria"
        else:
            raise ValueError(
                f"nieznana kategoria '{cat}' (dostępne: {', '.join(CATEGORIES)} "
                "albo wklej link do kategorii z OLX)"
            )
    else:
        search["query"] = what
        search["require_words"] = what.lower().split()
        label = what

    for extra in parts[2:]:
        if not extra:
            continue
        key, _, value = extra.partition(":")
        key = key.strip().lower()
        if key == "bez":
            search["exclude_words"] = [w.strip().lower() for w in value.split(",") if w.strip()]
        elif key == "cena":
            pm = re.fullmatch(r"\s*(\d+)?\s*-\s*(\d+)?\s*", value)
            if not pm:
                raise ValueError(f"zły zakres ceny '{value}' (przykład: cena: 1500-2800)")
            search["min_price"] = int(pm.group(1) or 0)
            search["max_price"] = int(pm.group(2) or 0)
        else:
            raise ValueError(f"nieznana opcja '{extra}' (dostępne: bez:, cena:)")

    # Nazwa = etykieta wyszukiwania w powiadomieniu i klucz w bazie
    search["name"] = label if state_word.startswith("uszkodz") else f"{label} ({state_word})"
    return search


def load_searches(path: Path) -> list[dict]:
    searches = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            searches.append(_parse_line(line, lineno))
        except ValueError as e:
            log.error("%s, linia %d: %s  ->  '%s' (pomijam)", path.name, lineno, e, line)
    return searches
