"""Generates synthetic, Dhaga & Co.-shaped sample data for the MVP.

Not real client data (we don't have DB access) — but deliberately shaped like
what the brief describes: Hinglish/vernacular search, colour typed a dozen
different ways, ~44% of returns landing in "Other", and five customer
personas (including a deliberate insufficient-data case) so every branch of
the pipeline in docs/architecture.md is reachable and testable.

Run: python data/generate_mock_data.py
Deterministic (fixed seed) so the committed CSVs are reproducible.
"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(7)

DATA_DIR = Path(__file__).resolve().parent
TODAY = datetime(2026, 9, 28)

CATEGORIES = ["womenswear", "kidswear", "mens"]

# Colour typed ~90 different ways in the real catalogue (per the brief) — this
# generates a comparable number of distinct spellings, not a token handful,
# so the extraction/matching steps are actually exercised against messy data.
COLOUR_VARIANTS = {
    "navy": ["Navy Blue", "navy", "Nvy Blu", "Dark Blue", "NAVY", "Navy", "dark navy"],
    "maroon": ["Maroon", "maroon", "Marun", "Wine", "MAROON", "Marron", "Wine Red"],
    "mustard": ["Mustard", "Mustard Yellow", "mustard yellow", "Ochre", "Mustrd", "Dark Yellow"],
    "black": ["Black", "black", "Jet Black", "BLACK", "Coal Black", "Blck"],
    "pink": ["Pink", "Baby Pink", "Hot Pink", "pink", "Rani Pink", "Blush Pink", "PINK"],
    "white": ["White", "Off White", "white", "Ivory", "Off-White", "WHITE", "Cream White"],
    "red": ["Red", "red", "Bright Red", "RED", "Rani Red", "Tomato Red"],
    "green": ["Green", "green", "Bottle Green", "Olive Green", "GREEN", "Mint Green"],
    "yellow": ["Yellow", "yellow", "Lemon Yellow", "YELLOW", "Sunshine Yellow"],
    "orange": ["Orange", "orange", "Rust Orange", "ORANGE", "Rust"],
    "blue": ["Blue", "blue", "Sky Blue", "Royal Blue", "BLUE", "Powder Blue"],
    "purple": ["Purple", "purple", "Wine Purple", "PURPLE", "Lavender"],
    "grey": ["Grey", "Gray", "grey", "GREY", "Charcoal Grey", "Charcoal"],
    "beige": ["Beige", "beige", "Sand Beige", "BEIGE", "Fawn"],
    "peach": ["Peach", "peach", "Light Peach", "PEACH"],
    "teal": ["Teal", "teal", "Sea Green", "TEAL"],
}

FABRICS = [
    "cotton", "Cotton blend", "georgette", "rayon", "Rayon", "chiffon",
    "poly cotton", "Pure Cotton", "net", "crepe",
]

OCCASION_PHRASES = [
    "mehndi function dress", "office wear kurti", "school ke liye shirt",
    "diwali ke liye kurti", "shaadi wala lehenga", "party wear top",
    "casual daily wear kurti", "college jaane ke liye jeans",
    "haldi ceremony dress", "birthday party frock for kids",
]

RETURN_REASONS_STRUCTURED = {
    "size_issue": [
        "size chart galat tha, bahut chota nikla",
        "runs small, ordered usual size but too tight",
        "size bada tha, loose fit",
    ],
    "quality": [
        "fabric quality thik nahi thi",
        "stitching came apart after one wash",
        "colour looked different from photos",
    ],
    "changed_mind": [
        "didn't need it anymore",
        "found a better option elsewhere",
    ],
    # ~44% of real returns land here per the brief — free text, no consistent tagging
    "other": [
        "not as expected",
        "just returning it",
        "issue with the order",
        "returning",
        "not required",
        "quality theek nahi laga",
    ],
}

REVIEW_TEXT_POSITIVE = [
    "Loved the fit, ordering again for the next function!",
    "Fabric is really comfortable, good for daily wear.",
    "Colour exactly like the photo, happy with this one.",
]
REVIEW_TEXT_NEGATIVE = [
    "Size ran small, had to return.",
    "Fabric felt cheap for the price.",
    "Delivery took too long for an occasion outfit.",
]

CUSTOMER_CITY_TIERS = ["tier-1", "tier-2", "tier-3"]


def _rand_date(days_back_min, days_back_max):
    days_back = random.randint(days_back_min, days_back_max)
    return TODAY - timedelta(days=days_back)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d")


def build_catalogue(n=150):
    rows = []
    colour_keys = list(COLOUR_VARIANTS.keys())
    for i in range(1, n + 1):
        sku_id = f"SKU{i:04d}"
        category = random.choice(CATEGORIES)
        colour_key = random.choice(colour_keys)
        colour_raw = random.choice(COLOUR_VARIANTS[colour_key])
        fabric = random.choice(FABRICS)
        price = random.choice([399, 499, 599, 699, 799, 899, 999, 1199, 1499])
        in_stock = random.random() > 0.1
        garment = {
            "womenswear": random.choice(["Kurti", "Dress", "Top", "Lehenga", "Saree"]),
            "kidswear": random.choice(["Frock", "T-Shirt", "Shorts Set"]),
            "mens": random.choice(["Shirt", "T-Shirt", "Trousers"]),
        }[category]
        description = f"{colour_raw} {fabric} {garment.lower()}, everyday and occasion wear."
        rows.append(
            {
                "sku_id": sku_id,
                "category": category,
                "price_inr": price,
                "colour_raw": colour_raw,
                "fabric_free_text": fabric,
                "description": description,
                "in_stock": str(in_stock),
            }
        )
    return rows


def build_customers_and_orders(catalogue):
    customers = []
    orders = []
    events = []
    returns = []
    reviews = []

    order_seq = 1
    event_seq = 1
    return_seq = 1

    by_category = {}
    for row in catalogue:
        by_category.setdefault(row["category"], []).append(row)

    def make_order(customer_id, days_ago, category=None, status="delivered"):
        nonlocal order_seq
        pool = by_category[category] if category else catalogue
        sku = random.choice(pool)
        order_id = f"ORD{order_seq:05d}"
        order_seq += 1
        orders.append(
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "sku_id": sku["sku_id"],
                "category": sku["category"],
                "price_inr": sku["price_inr"],
                "payment_mode": random.choice(["COD", "COD", "COD", "Prepaid"]),
                "order_date": _fmt(_rand_date(days_ago, days_ago)),
                "status": status,
            }
        )
        return order_id, sku

    def make_event(customer_id, days_ago, query_text):
        nonlocal event_seq
        events.append(
            {
                "event_id": f"EVT{event_seq:05d}",
                "customer_id": customer_id,
                "event_type": "search",
                "query_text": query_text,
                "timestamp": _fmt(_rand_date(days_ago, days_ago)),
            }
        )
        event_seq += 1

    def make_return(customer_id, order_id, reason_code, days_ago):
        nonlocal return_seq
        text = random.choice(RETURN_REASONS_STRUCTURED[reason_code])
        returns.append(
            {
                "return_id": f"RET{return_seq:05d}",
                "order_id": order_id,
                "customer_id": customer_id,
                "reason_code": reason_code,
                "reason_text": text,
                "timestamp": _fmt(_rand_date(days_ago, days_ago)),
            }
        )
        return_seq += 1

    def make_review(customer_id, sku_id, positive):
        reviews.append(
            {
                "customer_id": customer_id,
                "sku_id": sku_id,
                "rating": random.choice([4, 5]) if positive else random.choice([1, 2, 3]),
                "review_text": random.choice(REVIEW_TEXT_POSITIVE if positive else REVIEW_TEXT_NEGATIVE),
                "timestamp": _fmt(_rand_date(10, 300)),
            }
        )

    cust_seq = 1

    def new_customer_id():
        nonlocal cust_seq
        cid = f"CUST{cust_seq:04d}"
        cust_seq += 1
        return cid

    # 15 occasion-driven: recent orders + occasion search phrases across categories
    for _ in range(15):
        cid = new_customer_id()
        customers.append({"customer_id": cid, "city_tier": random.choice(CUSTOMER_CITY_TIERS),
                           "install_date": _fmt(_rand_date(200, 500))})
        for _ in range(random.randint(2, 4)):
            oid, sku = make_order(cid, random.randint(5, 60))
            if random.random() < 0.4:
                make_review(cid, sku["sku_id"], positive=True)
        for _ in range(random.randint(2, 5)):
            make_event(cid, random.randint(1, 20), random.choice(OCCASION_PHRASES))

    # 15 price-sensitive: orders skewed to the lowest price band, filter-heavy browsing
    for _ in range(15):
        cid = new_customer_id()
        customers.append({"customer_id": cid, "city_tier": random.choice(CUSTOMER_CITY_TIERS),
                           "install_date": _fmt(_rand_date(200, 500))})
        cheap_pool = [r for r in catalogue if r["price_inr"] <= 599]
        for _ in range(random.randint(2, 4)):
            sku = random.choice(cheap_pool)
            orders.append({
                "order_id": f"ORD{order_seq:05d}", "customer_id": cid, "sku_id": sku["sku_id"],
                "category": sku["category"], "price_inr": sku["price_inr"],
                "payment_mode": "COD", "order_date": _fmt(_rand_date(5, 90)), "status": "delivered",
            })
            order_seq += 1
        for _ in range(random.randint(2, 4)):
            make_event(cid, random.randint(1, 30), random.choice(
                ["sasta kurti under 500", "budget dress", "discount offers womenswear", "cheap shirt under 400"]
            ))

    # 15 fit-frustrated: multiple returns, several landing in "Other"
    for _ in range(15):
        cid = new_customer_id()
        customers.append({"customer_id": cid, "city_tier": random.choice(CUSTOMER_CITY_TIERS),
                           "install_date": _fmt(_rand_date(200, 500))})
        for _ in range(random.randint(3, 5)):
            oid, sku = make_order(cid, random.randint(10, 80), status="returned")
            reason = random.choices(
                ["size_issue", "quality", "other"], weights=[0.35, 0.2, 0.45]
            )[0]
            make_return(cid, oid, reason, random.randint(1, 70))
        for _ in range(random.randint(1, 3)):
            make_event(cid, random.randint(1, 30), random.choice(OCCASION_PHRASES))

    # 15 brand-loyal-dormant: several past orders, nothing recent (90+ days)
    for _ in range(15):
        cid = new_customer_id()
        customers.append({"customer_id": cid, "city_tier": random.choice(CUSTOMER_CITY_TIERS),
                           "install_date": _fmt(_rand_date(300, 600))})
        for _ in range(random.randint(3, 6)):
            oid, sku = make_order(cid, random.randint(100, 260))
            if random.random() < 0.5:
                make_review(cid, sku["sku_id"], positive=True)
        # no recent events — that's the point

    # 15 insufficient-data: 0-1 orders, no events — must short-circuit the pipeline
    for _ in range(15):
        cid = new_customer_id()
        customers.append({"customer_id": cid, "city_tier": random.choice(CUSTOMER_CITY_TIERS),
                           "install_date": _fmt(_rand_date(1, 30))})
        if random.random() < 0.3:
            make_order(cid, random.randint(1, 10))

    return customers, orders, events, returns, reviews


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    catalogue = build_catalogue()
    customers, orders, events, returns, reviews = build_customers_and_orders(catalogue)

    write_csv(DATA_DIR / "catalogue.csv", catalogue,
              ["sku_id", "category", "price_inr", "colour_raw", "fabric_free_text", "description", "in_stock"])
    write_csv(DATA_DIR / "customers.csv", customers, ["customer_id", "city_tier", "install_date"])
    write_csv(DATA_DIR / "orders.csv", orders,
              ["order_id", "customer_id", "sku_id", "category", "price_inr", "payment_mode", "order_date", "status"])
    write_csv(DATA_DIR / "app_events.csv", events,
              ["event_id", "customer_id", "event_type", "query_text", "timestamp"])
    write_csv(DATA_DIR / "returns.csv", returns,
              ["return_id", "order_id", "customer_id", "reason_code", "reason_text", "timestamp"])
    write_csv(DATA_DIR / "reviews.csv", reviews,
              ["customer_id", "sku_id", "rating", "review_text", "timestamp"])

    print(f"customers={len(customers)} orders={len(orders)} events={len(events)} "
          f"returns={len(returns)} reviews={len(reviews)} catalogue={len(catalogue)}")


if __name__ == "__main__":
    main()
