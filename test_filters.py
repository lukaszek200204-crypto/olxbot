"""Pokazuje, które z aktualnych ogłoszeń przeszłyby filtry (nic nie wysyła)."""
from main import excluded_by, load_config
from olx import fetch_offers

cfg = load_config()
app = cfg["app"]
for s in cfg["search"]:
    lo = s.get("min_price", app.get("min_price_pln", 0))
    hi = s.get("max_price", app.get("max_price_pln", 0))
    print(f"\n===== {s['name']} =====")
    for o in fetch_offers(s):
        reason = excluded_by(o.title, app, s)
        if reason:
            mark = f"ODRZUT {reason}"
        elif lo and (o.price is None or o.price < lo):
            mark = f"ODRZUT cena < {lo}"
        elif hi and o.price and o.price > hi:
            mark = f"ODRZUT cena > {hi}"
        else:
            mark = "OK"
        print(f"{mark[:40]:40} | {o.price_label:>10} | {o.title[:70]}")
