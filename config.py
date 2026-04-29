from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Top 20 Binance USDT pairs by trading volume (fixed) ──────────
    PAIRS: List[str] = field(default_factory=lambda: [
        "BTCUSDT",  "ETHUSDT",  "SOLUSDT",  "BNBUSDT",  "XRPUSDT",
        "DOGEUSDT", "PEPEUSDT", "ADAUSDT",  "SUIUSDT",  "LINKUSDT",
        "AVAXUSDT", "TRXUSDT",  "LTCUSDT",  "UNIUSDT",  "DOTUSDT",
        "NEARUSDT", "ARBUSDT",  "OPUSDT",   "ATOMUSDT", "MATICUSDT",
    ])

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
    HOST: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = field(default_factory=lambda: int(os.getenv("PORT", "5050")))

    # ── Analysis refresh ─────────────────────────────────────────────
    REFRESH_SECONDS: int = 30   # how often all engines re-run
    PARALLEL_PAIRS:  int = 5    # concurrent pairs processed at once


cfg = Config()
