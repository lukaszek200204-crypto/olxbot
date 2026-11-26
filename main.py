"""OLX Naprawy - śledzi uszkodzone przedmioty na OLX, ocenia opłacalność naprawy, powiadamia na Telegramie.

Użycie:
    python main.py            # pętla co N minut
    python main.py --once     # jedno przejście
    python main.py --chat-id  # pokaż chat_id (najpierw napisz do bota)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

from analyzer import Analyzer
from olx import OLXError, fetch_offers
from searches import load_searches
from telegram import Telegram, format_message, format_simple, profit_range

BASE = Path(__file__).parent
log = logging.getLogger("olx-naprawy")


def load_config() -> dict:
    path = BASE / "config.toml"
    if not path.exists():
        # Na GitHubie nie ma config.toml (zawiera sekrety) - używamy przykładu + zmiennych środowiskowych
        path = BASE / "config.example.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    # Wyszukiwania z searches.txt (jeśli jest) mają pierwszeństwo przed [[search]] w configu
    searches_file = BASE / "searches.txt"
    if searches_file.exists():
        cfg["search"] = load_searches(searches_file)
        if not cfg["search"]:
            sys.exit("searches.txt nie zawiera żadnego aktywnego wyszukiwania.")
    tg = cfg.setdefault("telegram", {})
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        tg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        tg["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    return cfg


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(BASE / "olx.db")
    db.execute(
        """CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY,
            url TEXT, title TEXT, price REAL, search TEXT,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            analysis TEXT, notified INTEGER DEFAULT 0
        )"""
    )
    db.execute("CREATE TABLE IF NOT EXISTS searches (name TEXT PRIMARY KEY)")
    # Migracja: wyszukiwania, które już mają ogłoszenia w bazie, uznaj za zainicjowane
    db.execute("INSERT OR IGNORE INTO searches (name) SELECT DISTINCT search FROM offers WHERE search IS NOT NULL")
    db.commit()
    return db


def _word_match(word: str, token: str) -> bool:
    """'wentylator*' = zaczyna się od, 'pad' = całe słowo."""
    return token.startswith(word[:-1]) if word.endswith("*") else token == word


def excluded_by(title: str, app: dict, search: dict | None = None) -> str | None:
    """Zwraca słowo, które wyklucza ogłoszenie, albo None.
    Sprawdza listy globalne ([app]) oraz exclude_words danego wyszukiwania."""
    low = title.lower()
    tokens = re.findall(r"\w+", low)
    if not tokens:
        return None
    # Wyszukiwanie po frazie: tytuł musi zawierać wszystkie słowa frazy (OLX zwraca też luźne dopasowania)
    for w in (search or {}).get("require_words", []):
        if w not in low:
            return f"brak '{w}' w tytule"
    for w in app.get("exclude_words", []) + (search or {}).get("exclude_words", []):
        w = w.lower()
        if any(_word_match(w, t) for t in tokens):
            return f"słowo '{w}'"
    for w in app.get("exclude_title_start", []):
        if _word_match(w.lower(), tokens[0]):
            return f"tytuł zaczyna się od '{w}'"
    return None


def run_once(cfg: dict, db: sqlite3.Connection, analyzer: Analyzer | None, tg: Telegram) -> bool:
    """Zwraca False, jeśli żadne wyszukiwanie nie powiodło się (np. OLX blokuje)."""
    ok_count = 0
    app = cfg.get("app", {})
    min_profit = app.get("min_profit_pln", 0)
    max_price = app.get("max_price_pln", 0)
    min_price = app.get("min_price_pln", 0)
    max_age_h = app.get("max_age_hours", 48)
    for search in cfg.get("search", []):
        name = search.get("name", search.get("query", "?"))
        # Nowe (pierwszy raz uruchamiane) wyszukiwanie: tylko zapamiętaj, nie wysyłaj starych ogłoszeń
        first_run = not db.execute("SELECT 1 FROM searches WHERE name=?", (name,)).fetchone()
        skip_analysis = first_run and not app.get("analyze_on_first_run", False)
        if skip_analysis:
            log.info("[%s] nowe wyszukiwanie: zapamiętuję obecne ogłoszenia bez wysyłania.", name)
        try:
            offers = fetch_offers(search)
        except OLXError as e:
            log.error("[%s] błąd pobierania OLX: %s", name, e)
            continue
        ok_count += 1
        if first_run:
            db.execute("INSERT OR IGNORE INTO searches (name) VALUES (?)", (name,))
            db.commit()

        if not offers:
            log.warning(
                "[%s] OLX nie zwrócił żadnych ogłoszeń - jeśli to kategoria, może nie mieć filtra stanu "
                "'uszkodzone'; spróbuj stanu 'wszystkie' albo frazy.", name
            )
        new = [o for o in offers if not db.execute("SELECT 1 FROM offers WHERE id=?", (o.id,)).fetchone()]
        log.info("[%s] %d ogłoszeń, nowych: %d", name, len(offers), len(new))

        for o in new:
            db.execute(
                "INSERT OR IGNORE INTO offers (id, url, title, price, search) VALUES (?,?,?,?,?)",
                (o.id, o.url, o.title, o.price, name),
            )
            db.commit()
            if skip_analysis:
                continue
            if max_age_h and o.created:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(o.created)
                    if age > timedelta(hours=max_age_h):
                        log.info("  pomijam (stare, %d dni): %s", age.days, o.title[:70])
                        continue
                except ValueError:
                    pass
            reason = excluded_by(o.title, app, search)
            if reason:
                log.info("  pomijam (%s): %s", reason, o.title[:70])
                continue
            lo = search.get("min_price", min_price)
            hi = search.get("max_price", max_price)
            if lo and (o.price is None or o.price < lo):
                log.info("  pomijam (cena %s < %d zł): %s", o.price_label, lo, o.title[:70])
                continue
            if hi and o.price and o.price > hi:
                log.info("  pomijam (za drogie %s): %s", o.price_label, o.title)
                continue

            if analyzer is None:  # tryb bez analizy - wysyłamy wszystko
                log.info("  wysyłam: %s | %s", o.price_label, o.title[:70])
                _deliver(db, tg, o, format_simple(o, name))
                continue

            try:
                a = analyzer.analyze(o)
            except anthropic.AuthenticationError:
                db.execute("DELETE FROM offers WHERE id=?", (o.id,))
                db.commit()
                sys.exit("Błędny lub brakujący ANTHROPIC_API_KEY.")
            except (anthropic.APIError, anthropic.AnthropicError) as e:
                # Usuń z bazy, żeby ogłoszenie zostało przeanalizowane w następnym przebiegu
                db.execute("DELETE FROM offers WHERE id=?", (o.id,))
                db.commit()
                if isinstance(e, anthropic.RateLimitError):
                    log.warning("Limit API - przerywam przebieg, ponowię później")
                    return True
                log.error("  błąd Claude dla %s: %s", o.id, e)
                continue
            if a is None:
                continue

            db.execute("UPDATE offers SET analysis=? WHERE id=?", (a.model_dump_json(), o.id))
            db.commit()

            pr = profit_range(o, a)
            mid = (pr[0] + pr[1]) / 2 if pr else None
            log.info("  %s | %s | zysk %s", o.title[:60], a.oplacalnosc, pr)

            if a.oplacalnosc == "nieopłacalne":
                continue
            if mid is not None and mid < min_profit:
                continue

            _deliver(db, tg, o, format_message(o, a, name))

    return ok_count > 0


def _deliver(db: sqlite3.Connection, tg: Telegram, o, text: str) -> None:
    """Wysyła powiadomienie; jeśli Telegram odrzuci, usuwa ogłoszenie z bazy, żeby ponowić w kolejnym przebiegu."""
    if tg.send(text, photo=o.photos[0] if o.photos else None):
        db.execute("UPDATE offers SET notified=1 WHERE id=?", (o.id,))
    else:
        log.error("  nie wysłano (%s) - ponowię w następnym przebiegu", o.title[:60])
        db.execute("DELETE FROM offers WHERE id=?", (o.id,))
    db.commit()
    time.sleep(1)  # limit Telegrama


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--chat-id", action="store_true")
    p.add_argument("--test", action="store_true", help="wyślij najnowsze ogłoszenie jako test (bez zapisu w bazie)")
    args = p.parse_args()

    cfg = load_config()
    tcfg = cfg["telegram"]
    tg = Telegram(tcfg["bot_token"], str(tcfg.get("chat_id", "")))

    if args.chat_id:
        tg.print_chat_ids()
        return
    if not tg.chat_id:
        sys.exit("Uzupełnij telegram.chat_id w config.toml (python main.py --chat-id).")

    if args.test:
        search = cfg["search"][0]
        offers = fetch_offers(search)
        if not offers:
            sys.exit("Test: OLX nie zwrócił ogłoszeń.")
        o = offers[0]
        log.info("Test: wysyłam '%s'", o.title)
        msg = "🧪 <b>TEST – bot działa</b>\n\n" + format_simple(o, search.get("name", "?"))
        if not tg.send(msg, photo=o.photos[0] if o.photos else None):
            sys.exit("Test: Telegram odrzucił wiadomość - sprawdź sekrety TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        log.info("Test: wiadomość wysłana.")
        return

    ccfg = cfg.get("claude", {})
    analyzer = None
    if ccfg.get("enabled", True):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit(
                "Brak ANTHROPIC_API_KEY. Ustaw: setx ANTHROPIC_API_KEY \"sk-ant-...\" i zrestartuj VS Code "
                "albo wyłącz analizę: [claude] enabled = false w config.toml."
            )
        analyzer = Analyzer(
            model=ccfg.get("model", "claude-opus-5-5"),
            effort=ccfg.get("effort", "medium"),
            analyze_photos=ccfg.get("analyze_photos", True),
        )
    else:
        log.info("Analiza AI wyłączona - wysyłam wszystkie nowe ogłoszenia.")
    db = open_db()
    interval = cfg.get("app", {}).get("interval_minutes", 10) * 60

    while True:
        ok = False
        try:
            ok = run_once(cfg, db, analyzer, tg)
        except Exception:
            log.exception("Nieoczekiwany błąd w przebiegu")
        if args.once:
            # Kod błędu -> GitHub Actions pokaże czerwony przebieg i wyśle maila
            sys.exit(0 if ok else 1)
        log.info("Następne sprawdzenie za %d min", interval // 60)
        time.sleep(interval)


if __name__ == "__main__":
    main()
