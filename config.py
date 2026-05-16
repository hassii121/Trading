from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Fallback pairs used only if Binance auto-fetch fails ──────────
    PAIRS: List[str] = field(default_factory=lambda: [
        "BTCUSDT",  "ETHUSDT",  "SOLUSDT",  "BNBUSDT",  "XRPUSDT",
        "DOGEUSDT", "PEPEUSDT", "ADAUSDT",  "SUIUSDT",  "LINKUSDT",
        "AVAXUSDT", "TRXUSDT",  "LTCUSDT",  "UNIUSDT",  "DOTUSDT",
        "NEARUSDT", "ARBUSDT",  "OPUSDT",   "ATOMUSDT", "MATICUSDT",
    ])

    # ── Dynamic pair settings ─────────────────────────────────────────
    TOP_PAIRS_COUNT:   int = 30   # how many top-volume pairs to track
    PAIRS_REFRESH_HRS: int = 6    # refresh pair list every N hours

    # ── Binance ───────────────────────────────────────────────────────
    API_KEY:    str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY",    ""))
    API_SECRET: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))

    # ── Coinglass (optional — for liquidation zones) ───────────────────
    COINGLASS_API_KEY: str = field(default_factory=lambda: os.getenv("COINGLASS_API_KEY", ""))

    # ── Timeframes used across engines ────────────────────────────────
    TF_PRIMARY:   str = "1h"    # main analysis timeframe
    TF_STRUCTURE: str = "4h"    # market structure timeframe
    TF_ENTRY:     str = "15m"   # entry precision timeframe
    CANDLE_LIMIT: int = 200

    # ── Web server ────────────────────────────────────────────────────
    HOST:       str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT:       int = field(default_factory=lambda: int(os.getenv("PORT", "5050")))
    SECRET_KEY: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "hassii-secret-2024"))
    PASSWORD:   str = field(default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", "hassii2024"))

    # ── Analysis refresh ─────────────────────────────────────────────
    REFRESH_SECONDS: int = 30   # how often all engines re-run
    PARALLEL_PAIRS:  int = 5    # concurrent pairs processed at once


cfg = Config()
