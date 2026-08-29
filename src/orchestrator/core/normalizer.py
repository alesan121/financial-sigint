"""
orchestrator/core/normalizer.py - Advanced Address Decoder (LUT) v2.9.
Mission: Ultra-fidelity MMU for asset mapping and noise suppression.

Hardware analogy: this is the definitive 'Address Decoder' (v2.9). It implements
'Class-Suffix Preservation' logic and mapping of digitized registers.
The LUT keys are synchronized with the sanity filter (no special characters).
"""

import re
import logging

# 📡 SRE ALIGNMENT: Access to the log bus for addressing observability
logger = logging.getLogger(__name__)

# ⚙️ FILTER PRE-COMPILATION (Clock Cycle Optimization)
RE_CLEAN_PREFIX = re.compile(r'^(TICKER:|SYM:|\$)')
# 🛡️ RECALIBRATION: We support alphanumeric tickers in share classes
RE_SHARE_CLASS = re.compile(r'([A-Z0-9]+)[./]([AB])')
# 🛡️ SANITY FILTER: We allow numbers (3M) but strip spaces and symbols (&, ., -)
RE_SANITY_FILTER = re.compile(r'[^A-Z0-9]')

def normalize_ticker(ticker: str) -> str:
    """
    MMU (Memory Management Unit) v2.9 - Full-Spec Industrial Address Decoder.
    Normalizes the ticker by removing modulation noise and mapping aliases.
    """
    if not ticker or not isinstance(ticker, str):
        return "UNKNOWN"

    # 1. 📡 BAND-PASS FILTER (Signal pre-processing)
    raw = ticker.upper().strip()

    # Strip high-frequency noise and useless prefixes
    t = RE_CLEAN_PREFIX.sub('', raw)

    # 🛡️ SHARE CLASS PROTECTION:
    # Preserve the .B or /B format (e.g. BRK.B) before the destructive cleanup.
    share_class_match = RE_SHARE_CLASS.search(t)
    preserved_class = f"{share_class_match.group(1)}.{share_class_match.group(2)}" if share_class_match else None

    # Harmonic cleanup: strip market suffixes (.L, .MC)
    t = t.split('.')[0].strip()
    # Apply sanity filter (A-Z, 0-9) to remove spaces, dots, and junk
    # Important: this forces the LUT keys to be in 'clean' format.
    t = RE_SANITY_FILTER.sub('', t)

    # 2. 🧩 EEPROM: Master Look-Up Table (LUT) by Memory Banks
    # SRE Note: Keys contain NO spaces or symbols (&) to match the filtering.
    LUT = {
        # --- MEMORY BANK: BIG TECH (Generals) ---
        "NVIDIA": "NVDA", "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
        "APPLE": "AAPL", "MICROSOFT": "MSFT", "AMAZON": "AMZN",
        "TESLA": "TSLA", "META": "META", "FACEBOOK": "META",
        "NETFLIX": "NFLX", "BROADCOM": "AVGO", "ADOBE": "ADBE", "ORACLE": "ORCL",
        "SALESFORCE": "CRM", "INTUIT": "INTU", "PALANTIR": "PLTR", "SERVICENOW": "NOW",
        "SNOWFLAKE": "SNOW", "DATADOG": "DDOG", "CLOUDFLARE": "NET",
        
        # --- MEMORY BANK: CHIPS & SEMIS (High Voltage) ---
        "AMD": "AMD", "INTEL": "INTC", "TSMC": "TSM", "TAIWANSEMI": "TSM",
        "ARM": "ARM", "MICRON": "MU", "QUALCOMM": "QCOM", "ASML": "ASML",
        "APPLIEDMATERIALS": "AMAT", "LAMRESEARCH": "LRCX", "KLACORP": "KLAC",
        "TERADYNE": "TER", "ENTEGRIS": "ENTG",
        
        # --- MEMORY BANK: AEROSPACE & DEFENSE (War Chest) ---
        "LOCKHEED": "LMT", "RAYTHEON": "RTX", "NORTHROP": "NOC",
        "BOEING": "BA", "GENERALDYNAMICS": "GD", "L3HARRIS": "LHX",

        # --- MEMORY BANK: AUTOMOTIVE & INDUSTRIALS (Engine Room) ---
        "FERRARI": "RACE", "TOYOTA": "TM", "VOLKSWAGEN": "VWAGY", "FORD": "F",
        "GENERALMOTORS": "GM", "CATERPILLAR": "CAT", "DEERE": "DE", "3M": "MMM",
        "HONEYWELL": "HON", "GE": "GE", "GENERALELECTRIC": "GE",

        # --- MEMORY BANK: ENERGY & COMMODITIES (Fuel) ---
        "TOTAL": "TTE", "TOTALENERGIES": "TTE", "TTEPA": "TTE",
        "EXXON": "XOM", "EXXONMOBIL": "XOM", "CHEVRON": "CVX", "SHELL": "SHEL",
        "BP": "BP", "SAUDIARAMCO": "ARMCO", "OIL": "USO", "CRUDE": "USO", 
        "NATURALGAS": "UNG", "GOLD": "GLD", "SILVER": "SLV", "BRENT": "BNO",
        "URANIUM": "URA", "LITHIUM": "LIT", "COPPER": "CPER",
        
        # --- MEMORY BANK: FINANCE & INDICES (Bus Master) ---
        "SP": "SPY", "SPY": "SPY", "SANDP": "SPY", "SP500": "SPY",
        "NASDAQ": "QQQ", "DOW": "DIA", "DOWJONES": "DIA", "VIX": "VXX", "RUSSELL": "IWM",
        "JPMORGAN": "JPM", "GOLDMAN": "GS", "GOLDMANSACHS": "GS", 
        "BERKSHIRE": "BRK.B", "WARRENBUFFETT": "BRK.B", "BRKB": "BRK.B", "BERKSHIREHATHAWAY": "BRK.B",
        "ROYALBANK": "RY", "RBC": "RY", "MORGANSTANLEY": "MS",
        "BLACKROCK": "BLK", "CITIGROUP": "C", "BANKOFAMERICA": "BAC", "CHARLESSCHWAB": "SCHW",
        "PROGRESSIVE": "PGR", "CHUBB": "CB",
        
        # --- MEMORY BANK: CONSUMER, RETAIL & LUXURY (Lifestyle Bus) ---
        "WALMART": "WMT", "COSTCO": "COST", "TARGET": "TGT", "NIKE": "NKE",
        "DISNEY": "DIS", "COCACOLA": "KO", "PEPSI": "PEP", "MCDONALDS": "MCD",
        "LVMH": "LVMUY", "HERMES": "HESAY", "SAP": "SAP", "STARBUCKS": "SBUX",
        "UBER": "UBER", "AIRBNB": "ABNB", "BOOKING": "BKNG", "HOMEDEPOT": "HD", "LOWES": "LOW",
        
        # --- MEMORY BANK: HEALTHCARE (Bio-Signals) ---
        "PFIZER": "PFE", "MODERNA": "MRNA", "ELILLY": "LLY", "ELILILLY": "LLY",
        "JOHNSONANDJOHNSON": "JNJ", "JNJ": "JNJ", "UNITEDHEALTH": "UNH", "NOVONORDISK": "NVO",
        "ASTRAZENECA": "AZN", "ABBVIE": "ABBV", "MERCK": "MRK",

        # --- MEMORY BANK: TRANSPORT & LOGISTICS (Supply Chain) ---
        "FEDEX": "FDX", "UPS": "UPS", "MAERSK": "AMKBY", "DELTA": "DAL",
        "AMERICANAIRLINES": "AAL", "UNITEDAIRLINES": "UAL", "RYANAIR": "RYAAY",

        # --- MEMORY BANK: UTILITIES & REAL ESTATE (Infrastructure) ---
        "NEXTERA": "NEE", "DUKEENERGY": "DUK", "PROLOGIS": "PLD", "AMERICANTOWER": "AMT",
        "EQUINIX": "EQIX", "REALTINCOME": "O", "VICI": "VICI",

        # --- MEMORY BANK: CRYPTO ASSETS (ETFs) ---
        "BITCOIN": "IBIT", "BTC": "IBIT", "ETHEREUM": "ETHA", "ETH": "ETHA",
        "SOLANA": "SOL", "CRYPTO": "BITO", "COINBASE": "COIN", "MICROSTRATEGY": "MSTR",
        
        # --- MEMORY BANK: MARKET INFLUENCERS (Proxy Nodes) ---
        "CATHIEWOOD": "ARKK", "PELOSI": "SPY", "ACKMAN": "PSTH", "BURRY": "SPY",

        # --- SQUELCH LIST (Noise Suppression) ---
        "CASA": "UNKNOWN", "MARKET": "UNKNOWN", "STOCK": "UNKNOWN",
        "NEWS": "UNKNOWN", "BREAKING": "UNKNOWN", "REPORT": "UNKNOWN",
        "ANALYSIS": "UNKNOWN", "BULLISH": "UNKNOWN", "BEARISH": "UNKNOWN",
        "FED": "SPY", "FEDERALRESERVE": "SPY", "INFLATION": "SPY", "CPI": "SPY",
        "GDP": "SPY", "ECONOMY": "SPY", "FOMC": "SPY", "POWELL": "SPY", "TREASURY": "TLT",
        "ANALYSTS": "UNKNOWN", "GUIDANCE": "UNKNOWN", "REVENUE": "UNKNOWN",
        "RATES": "SPY", "YIELD": "SPY"
    }
    
    # 3. ADDRESS RESOLUTION (L1 Cache Search)
    # First we try to resolve known names in the LUT (highest priority)
    resolved = LUT.get(t, None)

    if resolved:
        # 🛰️ BUS TRACE: Log the mapping if a name transformation occurred
        if resolved != t:
            logger.debug(f"MMU: Address mapped from '{t}' to physical '{resolved}'")
        return resolved

    # If it's not in the LUT but we detect a share class (BRK.B), return it
    if preserved_class:
        return preserved_class

    # 🛡️ OUTPUT FUSE: Length protection
    # If after all processing the string is still long (>5), it is analog noise.
    # Exception for macro assets or allowed suffixes.
    if len(t) > 5 and not (t.startswith("MACRO:") or t == "UNKNOWN"):
        return "UNKNOWN"

    return t
