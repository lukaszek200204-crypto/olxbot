"""Test pobierania ogłoszeń z OLX (bez Claude i Telegrama)."""
from olx import fetch_offers

offers = fetch_offers({"query": "laptop", "state": "damaged"}, limit=5)
print(f"Pobrano {len(offers)} ogłoszeń")
for o in offers:
    print(f"{o.id} | {o.price_label} | {o.state} | {o.title} | zdjęć: {len(o.photos)}")
    print("   ", o.description[:100].replace("\n", " "))
