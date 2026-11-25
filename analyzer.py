"""Analiza ogłoszenia przez Claude: streszczenie, wycena naprawy, opłacalność."""
from __future__ import annotations

import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from olx import Offer

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Jesteś doświadczonym serwisantem i handlarzem sprzętu używanego w Polsce \
(elektronika, telefony, laptopy, konsole, AGD, narzędzia, rowery, auta i inne). \
Oceniasz ogłoszenia z OLX z uszkodzonymi przedmiotami pod kątem zakupu, naprawy i odsprzedaży.

Dla ogłoszenia:
1. Streść krótko (1-2 zdania), co jest sprzedawane i co jest zepsute.
2. Zdiagnozuj najbardziej prawdopodobną usterkę na podstawie opisu (i zdjęć, jeśli są). \
Jeśli opis jest niejasny, napisz to i oszacuj ostrożnie.
3. Wyceń naprawę wykonaną samodzielnie przez kupującego: realne ceny części na polskim rynku \
(Allegro/AliExpress/zamienniki) w PLN, bez kosztu robocizny serwisu. Podaj widełki min-max.
4. Oszacuj widełki ceny sprzedaży PO udanej naprawie na OLX/Allegro w Polsce (sprzęt używany, \
sprawny), biorąc pod uwagę model, wiek i stan wizualny.
5. Oceń trudność, czas i ryzyko (np. ukryte usterki, blokady iCloud/FRP/BIOS, zalanie, \
brak części, "na części" bez gwarancji naprawy).
6. Werdykt opłacalności uwzględniający cenę zakupu z ogłoszenia.

Bądź realistą i raczej konserwatywny w szacunkach zysku. Wszystkie kwoty w PLN, liczby całkowite. \
Pisz po polsku, zwięźle."""


class Analysis(BaseModel):
    streszczenie: str = Field(description="1-2 zdania: co to jest i co jest zepsute")
    przedmiot: str = Field(description="Marka i model, jeśli da się ustalić")
    usterka: str = Field(description="Najbardziej prawdopodobna usterka / diagnoza")
    potrzebne_czesci: list[str]
    koszt_naprawy_min: int
    koszt_naprawy_max: int
    trudnosc: int = Field(description="1 (banalna) - 5 (bardzo trudna / wymaga sprzętu)")
    czas_naprawy_h: float
    cena_sprzedazy_min: int = Field(description="Cena sprzedaży po naprawie - dolna granica")
    cena_sprzedazy_max: int = Field(description="Cena sprzedaży po naprawie - górna granica")
    ryzyka: list[str]
    oplacalnosc: Literal["bardzo opłacalne", "opłacalne", "na granicy", "nieopłacalne", "brak danych"]
    pewnosc_oceny: Literal["niska", "średnia", "wysoka"]
    komentarz: str = Field(description="Krótka rada: ile maksymalnie zapłacić, o co dopytać sprzedawcę")


def _offer_text(o: Offer) -> str:
    params = "\n".join(f"- {k}: {v}" for k, v in o.params.items()) or "- brak"
    return (
        f"TYTUŁ: {o.title}\n"
        f"CENA: {o.price_label}\n"
        f"LOKALIZACJA: {o.city}, {o.region}\n"
        f"PARAMETRY:\n{params}\n\n"
        f"OPIS:\n{o.description}"
    )


class Analyzer:
    def __init__(self, model: str, effort: str = "medium", analyze_photos: bool = True):
        self.client = anthropic.Anthropic()  # ANTHROPIC_API_KEY ze zmiennej środowiskowej
        self.model = model
        self.effort = effort
        self.analyze_photos = analyze_photos

    def analyze(self, offer: Offer) -> Analysis | None:
        content: list[dict] = []
        if self.analyze_photos:
            for url in offer.photos[:3]:
                content.append({"type": "image", "source": {"type": "url", "url": url}})
        content.append({"type": "text", "text": _offer_text(offer)})

        try:
            resp = self._call(content)
        except anthropic.BadRequestError as e:
            # Np. zdjęcie niedostępne dla API - ponów bez zdjęć
            if self.analyze_photos and offer.photos:
                log.warning("Błąd z obrazkami (%s), ponawiam bez zdjęć", e.message)
                resp = self._call([content[-1]])
            else:
                raise

        if resp.stop_reason == "refusal":
            log.warning("Model odmówił analizy ogłoszenia %s", offer.id)
            return None
        if resp.stop_reason == "max_tokens":
            log.warning("Odpowiedź ucięta (max_tokens) dla %s", offer.id)
            return None
        return resp.parsed_output

    def _call(self, content: list[dict]):
        return self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # przy odmowie klasyfikatora API samo przełączy na inny model
            output_config={"effort": self.effort},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=Analysis,
        )
