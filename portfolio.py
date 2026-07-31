# ==========================================
# KONFIGURATION & PORTFOLIO
# ==========================================

DEINE_PIN = "1337"  # 👈 Deine persönliche PIN

# PORTFOLIO (NUR GEKAUFTE POSITIONEN)
PORTFOLIO = [
    {
        "ticker": "QUTM.DE",
        "isin": "IE0007Y8Y157",
        "name": "VanEck Quantum Computing",
        "buy_date": "2026-07-23",
        "buy_price": 23.79,
        "shares": 21,
        "partially_sold": True,  # True setzen, sobald Tranche 1 verkauft wurde
        "t1_sell_date": "2026-07-30",     # Datum von Teilverkauf 1 (z. B. '2026-07-15')
        "t1_sell_price": 24.42,    # Tatsächlicher Verkaufskurs von T1 (Optional: Falls leer, wird der EMA50 genutzt)
        "sold": False,  # True setzen, sobald Tranche 2 verkauft wurde
        "t2_sell_date": None,     # Datum von Teilverkauf 2 (z. B. '2026-07-15')
        "t2_sell_price": None,    # Tatsächlicher Verkaufskurs von T2 

    },
    {
        "ticker": "SEC0.DE",
        "isin": "IE000I8KRLL9",
        "name": "iShares Global Semiconductors",
        "buy_date": "2026-07-29",
        "buy_price": 15.24,
        "shares": 33,
        "partially_sold": False,  # True setzen, sobald Tranche 1 verkauft wurde
        "t1_sell_date": None,     # Datum von Teilverkauf 1 (z. B. '2026-07-15')
        "t1_sell_price": None,    # Tatsächlicher Verkaufskurs von T1
        "sold": False,  # True setzen, sobald Tranche 2 verkauft wurde
        "t2_sell_date": None,     # Datum von Teilverkauf 2 (z. B. '2026-07-15')
        "t2_sell_price": None,    # Tatsächlicher Verkaufskurs von T2 
    },
]
