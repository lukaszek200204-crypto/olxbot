"""Pokazuje, które z aktualnych ogłoszeń przeszłyby filtry słów i ceny (nic nie wysyła)."""
from main import excluded_by, load_config
from olx import fetch_offers

cfg = load_config()
app = cfg["app"]
min_price = app.get("min_price_pln", 0)
for s in cfg["search"]:
    print(f"\n===== {s['name']} =====")
    for o in fetch_offers(s):
        word = excluded_by(o.title, app, s)
        if word:
            mark = f"ODRZUT słowo '{word}'"
        elif min_price and (o.price is None or o.price < min_price):
            mark = f"ODRZUT cena {o.price_label}"
        else:
            mark = "OK"
        print(f"{mark:32} | {o.price_label:>10} | {o.title[:75]}")
