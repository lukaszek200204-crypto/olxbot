"""Wysyłanie powiadomień przez Telegram Bot API."""
from __future__ import annotations

import html
import logging

import requests

from analyzer import Analysis
from olx import Offer

log = logging.getLogger(__name__)

ICONS = {
    "bardzo opłacalne": "🟢🟢",
    "opłacalne": "🟢",
    "na granicy": "🟡",
    "nieopłacalne": "🔴",
    "brak danych": "⚪",
}


def profit_range(o: Offer, a: Analysis) -> tuple[int, int] | None:
    if o.price is None:
        return None
    lo = a.cena_sprzedazy_min - o.price - a.koszt_naprawy_max
    hi = a.cena_sprzedazy_max - o.price - a.koszt_naprawy_min
    return int(lo), int(hi)


def format_message(o: Offer, a: Analysis, search_name: str) -> str:
    e = html.escape
    profit = profit_range(o, a)
    profit_txt = f"{profit[0]} – {profit[1]} zł" if profit else "brak ceny zakupu"
    parts = "\n".join(f"  • {e(p)}" for p in a.potrzebne_czesci[:5]) or "  • —"
    risks = "\n".join(f"  ⚠️ {e(r)}" for r in a.ryzyka[:4]) or "  —"
    return (
        f"{ICONS.get(a.oplacalnosc, '')} <b>{e(o.title)}</b>\n"
        f"<i>{e(search_name)} · {e(o.city)}</i>\n\n"
        f"💬 {e(a.streszczenie)}\n"
        f"🔧 <b>Usterka:</b> {e(a.usterka)}\n\n"
        f"💰 <b>Cena zakupu:</b> {e(o.price_label)}\n"
        f"🛠 <b>Koszt naprawy:</b> {a.koszt_naprawy_min} – {a.koszt_naprawy_max} zł "
        f"(trudność {a.trudnosc}/5, ~{a.czas_naprawy_h:g} h)\n"
        f"🏷 <b>Sprzedaż po naprawie:</b> {a.cena_sprzedazy_min} – {a.cena_sprzedazy_max} zł\n"
        f"📈 <b>Zysk:</b> {profit_txt}\n"
        f"⚖️ <b>Werdykt:</b> {e(a.oplacalnosc)} (pewność: {e(a.pewnosc_oceny)})\n\n"
        f"<b>Części:</b>\n{parts}\n"
        f"<b>Ryzyka:</b>\n{risks}\n\n"
        f"📝 {e(a.komentarz)}\n\n"
        f'<a href="{e(o.url)}">➡️ Otwórz ogłoszenie</a>'
    )


def format_simple(o: Offer, search_name: str) -> str:
    """Wiadomość bez analizy AI - same dane z ogłoszenia."""
    e = html.escape
    desc = o.description.replace("\n", " ")
    if len(desc) > 400:
        desc = desc[:400].rsplit(" ", 1)[0] + "…"
    return (
        f"🆕 <b>{e(o.title)}</b>\n"
        f"<i>{e(search_name)} · {e(o.city)}, {e(o.region)}</i>\n\n"
        f"💰 <b>{e(o.price_label)}</b>\n\n"
        f"{e(desc)}\n\n"
        f'<a href="{e(o.url)}">➡️ Otwórz ogłoszenie</a>'
    )


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def send(self, text: str, photo: str | None = None) -> None:
        # Podpis zdjęcia ma limit 1024 znaków - dłuższe wiadomości idą jako tekst
        if photo and len(text) <= 1024:
            r = requests.post(
                f"{self.base}/sendPhoto",
                data={"chat_id": self.chat_id, "photo": photo, "caption": text, "parse_mode": "HTML"},
                timeout=30,
            )
            if r.ok:
                return
            log.warning("sendPhoto nie powiódł się (%s), wysyłam tekst", r.text[:200])
        r = requests.post(
            f"{self.base}/sendMessage",
            data={
                "chat_id": self.chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
            timeout=30,
        )
        if not r.ok:
            log.error("Telegram błąd: %s", r.text[:300])

    def print_chat_ids(self) -> None:
        r = requests.get(f"{self.base}/getUpdates", timeout=30)
        r.raise_for_status()
        updates = r.json().get("result", [])
        if not updates:
            print("Brak wiadomości. Napisz cokolwiek do swojego bota na Telegramie i uruchom ponownie.")
        seen = set()
        for u in updates:
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
            if chat and chat["id"] not in seen:
                seen.add(chat["id"])
                name = chat.get("title") or chat.get("username") or chat.get("first_name")
                print(f"chat_id = {chat['id']}   ({name})")
