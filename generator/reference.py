"""Reference lists for the synthetic bank: regions, segments, merchant categories, names."""

from __future__ import annotations

from dataclasses import dataclass

# Norwegian counties with approximate population weights.
REGIONS: list[tuple[str, float]] = [
    ("Oslo", 0.13),
    ("Akershus", 0.13),
    ("Vestland", 0.12),
    ("Rogaland", 0.09),
    ("Trøndelag", 0.09),
    ("Innlandet", 0.07),
    ("Østfold", 0.06),
    ("Agder", 0.06),
    ("Buskerud", 0.05),
    ("Vestfold", 0.05),
    ("Møre og Romsdal", 0.05),
    ("Nordland", 0.04),
    ("Telemark", 0.03),
    ("Troms", 0.03),
    ("Finnmark", 0.01),
]

CITIES: dict[str, list[str]] = {
    "Oslo": ["Oslo"],
    "Akershus": ["Lillestrøm", "Sandvika", "Ski", "Jessheim", "Asker"],
    "Vestland": ["Bergen", "Førde", "Stord", "Voss", "Odda"],
    "Rogaland": ["Stavanger", "Sandnes", "Haugesund", "Egersund"],
    "Trøndelag": ["Trondheim", "Steinkjer", "Levanger", "Namsos"],
    "Innlandet": ["Hamar", "Lillehammer", "Gjøvik", "Elverum"],
    "Østfold": ["Fredrikstad", "Sarpsborg", "Moss", "Halden"],
    "Agder": ["Kristiansand", "Arendal", "Grimstad", "Mandal"],
    "Buskerud": ["Drammen", "Kongsberg", "Hønefoss"],
    "Vestfold": ["Tønsberg", "Sandefjord", "Larvik", "Horten"],
    "Møre og Romsdal": ["Ålesund", "Molde", "Kristiansund"],
    "Nordland": ["Bodø", "Narvik", "Mo i Rana"],
    "Telemark": ["Skien", "Porsgrunn", "Notodden"],
    "Troms": ["Tromsø", "Harstad", "Finnsnes"],
    "Finnmark": ["Alta", "Hammerfest", "Vadsø"],
}

SEGMENTS: list[tuple[str, float]] = [
    ("Standard", 0.55),
    ("Premium", 0.20),
    ("Student", 0.15),
    ("Private Banking", 0.10),
]

# Monthly salary and housing payment per segment, in NOK.
SALARY_BY_SEGMENT = {"Student": 11000.0, "Standard": 36000.0, "Premium": 52000.0, "Private Banking": 85000.0}
HOUSING_BY_SEGMENT = {"Student": 6500.0, "Standard": 12000.0, "Premium": 16000.0, "Private Banking": 22000.0}

# Overdraft limits on current accounts, in NOK, with the share of accounts in each segment that gets them.
CREDIT_LIMITS_BY_SEGMENT = {
    "Student": ([0.0, 2000.0], [0.70, 0.30]),
    "Standard": ([0.0, 10000.0, 20000.0], [0.50, 0.35, 0.15]),
    "Premium": ([0.0, 25000.0, 50000.0], [0.30, 0.50, 0.20]),
    "Private Banking": ([0.0, 100000.0], [0.20, 0.80]),
}

# Card purchases per day on a customer's main current account.
CARD_RATE_BY_SEGMENT = {"Student": 0.30, "Standard": 0.33, "Premium": 0.42, "Private Banking": 0.45}

STREETS = [
    "Storgata", "Kirkeveien", "Skolegata", "Fjellveien", "Strandveien", "Bjørkeveien",
    "Furuveien", "Granveien", "Elveveien", "Solveien", "Havnegata", "Parkveien",
    "Industriveien", "Lerkeveien", "Myrveien", "Nedre Torggate", "Øvre Torggate",
    "Åsveien", "Dalveien", "Bergveien", "Sjøgata", "Rådhusgata", "Jernbanegata",
]

FIRST_NAMES = [
    "Ola", "Kari", "Nora", "Emma", "Jakob", "Lucas", "Sofie", "Ingrid", "Håkon", "Øystein",
    "Åse", "Bjørn", "Sigrid", "Eirik", "Magnus", "Astrid", "Solveig", "Trond", "Siri", "Lars",
    "Mari", "Anders", "Hanne", "Petter", "Thea", "Erlend", "Marte", "Knut", "Kristin", "Henrik",
    "Maren", "Vegard", "Tiril", "Synnøve", "Jørgen", "Aud", "Odd", "Ragnhild", "Svein", "Tone",
    "Amir", "Fatima", "Leila", "Omar", "Nina", "Elias", "Ahmed", "Sara", "Mathias", "Julie",
]

LAST_NAMES = [
    "Hansen", "Johansen", "Olsen", "Larsen", "Andersen", "Pedersen", "Nilsen", "Kristiansen",
    "Jensen", "Karlsen", "Johnsen", "Pettersen", "Eriksen", "Berg", "Haugen", "Hagen",
    "Johannessen", "Andreassen", "Jacobsen", "Dahl", "Jørgensen", "Halvorsen", "Lund", "Strand",
    "Solberg", "Moen", "Bakke", "Lie", "Sæther", "Næss", "Østby", "Aas", "Nguyen", "Ali",
    "Khan", "Berge", "Fjeld", "Vik", "Holm", "Tveit",
]


@dataclass(frozen=True)
class Category:
    code: str
    name: str
    share: float          # share of card purchases, zero for bill categories
    median_nok: float
    sigma: float
    foreign_ok: bool
    merchants: int        # number of merchants in the category


CARD_CATEGORIES: list[Category] = [
    Category("5411", "Grocery stores", 0.30, 420.0, 0.70, True, 220),
    Category("5812", "Restaurants", 0.10, 380.0, 0.80, True, 160),
    Category("5814", "Fast food", 0.07, 150.0, 0.50, True, 90),
    Category("5541", "Fuel stations", 0.07, 650.0, 0.50, False, 110),
    Category("5912", "Pharmacies", 0.04, 260.0, 0.70, False, 60),
    Category("4111", "Local transport", 0.06, 60.0, 0.60, True, 25),
    Category("5311", "Department stores", 0.04, 900.0, 0.90, True, 40),
    Category("5651", "Clothing", 0.05, 700.0, 0.90, True, 120),
    Category("5732", "Electronics", 0.02, 2500.0, 1.00, True, 45),
    Category("7011", "Hotels", 0.02, 1800.0, 0.80, True, 70),
    Category("4511", "Airlines", 0.015, 2400.0, 0.80, True, 12),
    Category("5942", "Books and media", 0.02, 250.0, 0.70, True, 40),
    Category("7832", "Cinemas and events", 0.02, 220.0, 0.60, True, 35),
    Category("5999", "Other retail", 0.06, 350.0, 0.90, True, 180),
    Category("5813", "Bars", 0.03, 280.0, 0.70, True, 80),
    Category("4121", "Taxis", 0.025, 320.0, 0.60, True, 40),
    Category("7230", "Hair and beauty", 0.02, 650.0, 0.50, False, 90),
    Category("5945", "Toys and hobby", 0.015, 420.0, 0.80, True, 35),
]

BILL_CATEGORIES: list[Category] = [
    Category("4900", "Utilities", 0.0, 1100.0, 0.50, False, 15),
    Category("4814", "Telecom", 0.0, 550.0, 0.35, False, 10),
    Category("6300", "Insurance", 0.0, 1400.0, 0.50, False, 12),
    Category("6513", "Housing and rent", 0.0, 12000.0, 0.30, False, 25),
]

ALL_CATEGORIES = CARD_CATEGORIES + BILL_CATEGORIES

# Countries of foreign merchants and the currency they charge in.
FOREIGN_COUNTRIES: list[tuple[str, str, float]] = [
    ("SE", "SEK", 0.40),
    ("DE", "EUR", 0.15),
    ("ES", "EUR", 0.15),
    ("FR", "EUR", 0.08),
    ("NL", "EUR", 0.07),
    ("US", "USD", 0.15),
]

FOREIGN_CITIES = {
    "SE": ["Stockholm", "Göteborg", "Malmö", "Strömstad"],
    "DE": ["Berlin", "Hamburg", "München"],
    "ES": ["Barcelona", "Málaga", "Madrid", "Alicante"],
    "FR": ["Paris", "Nice", "Lyon"],
    "NL": ["Amsterdam", "Rotterdam"],
    "US": ["New York", "Orlando", "Los Angeles"],
}

MERCHANT_WORDS = [
    "Fjord", "Fjell", "Bjørk", "Furu", "Elv", "Skog", "Nord", "Sør", "Vest", "Øst", "Sol",
    "Måne", "Stjerne", "Bre", "Vidde", "Hav", "Strand", "Berg", "Dal", "Eng", "Myr", "Tind",
    "Foss", "Holm", "Vik", "Nes", "Odde", "Sund", "Lyng", "Hei",
]

FOREIGN_MERCHANT_WORDS = [
    "Linden", "Harbor", "Central", "Royal", "Alpine", "Marina", "Plaza", "Union", "Grand",
    "Garden", "Market", "Corner", "Station", "Bridge", "Square",
]

CATEGORY_TYPE_WORDS = {
    "5411": ["Grocery", "Market", "Food Store"],
    "5812": ["Restaurant", "Bistro", "Kitchen"],
    "5814": ["Burger", "Pizza", "Kebab"],
    "5541": ["Fuel", "Energy Station"],
    "5912": ["Pharmacy"],
    "4111": ["Transit", "Rail", "Bus"],
    "5311": ["Department Store", "Warehouse"],
    "5651": ["Clothing", "Fashion", "Outfitters"],
    "5732": ["Electronics", "Tech"],
    "7011": ["Hotel", "Inn", "Lodge"],
    "4511": ["Air", "Airlines"],
    "5942": ["Books", "Media"],
    "7832": ["Cinema", "Arena", "Theatre"],
    "5999": ["Retail", "Shop", "Store"],
    "5813": ["Bar", "Pub", "Tavern"],
    "4121": ["Taxi", "Cab"],
    "7230": ["Salon", "Barber", "Spa"],
    "5945": ["Toys", "Hobby"],
    "4900": ["Power", "Water", "Energy"],
    "4814": ["Mobile", "Telecom", "Fiber"],
    "6300": ["Insurance", "Assurance"],
    "6513": ["Housing", "Property", "Homes"],
}

TXN_TYPES = ("CARD_PURCHASE", "BILL_PAYMENT", "TRANSFER_IN", "TRANSFER_OUT", "ATM_WITHDRAWAL", "FEE", "SALARY", "INTEREST")
