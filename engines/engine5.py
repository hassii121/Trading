"""
Engine 5 — Confidence & Risk Management Engine
Scores every setup (0–100) from all engine outputs and decides:
- How strong the trade is
- How much capital to risk
- How to manage the trade after entry
"""
import logging

log = logging.getLogger(__name__)

# Score thresholds
SCORE_HIGH   = 80
SCORE_GOOD   = 60
SCORE_WEAK   = 30

# Risk % per confidence band
RISK_HIGH    = 1.5
RISK_GOOD    = 0.75
RISK_WEAK    = 0.25
RISK_NONE    = 0.0

# Ideal minimum RR for full confidence
IDEAL_RR     = 2.0


class Engine5:
    def __init__(self, cfg):
        self.cfg = cfg

    # ── Public entry point ───────────────────────────────────────────
    def run(self, pair: str, e1: dict, e2: dict, e3: dict, e4: dict) -> dict:
        try:
            # ── Unpack Engine 1
            pd       = e1.get("price_data", {})
            dv       = e1.get("derivatives", {})
            sn       = e1.get("sentiment",  {})
            ss       = e1.get("session",    {})
            vol      = pd.get("volatility", "Low")
            atr      = pd.get("atr", 0)
            session  = ss.get("active", "")
            funding  = dv.get("funding_bias", "Neutral")
            sent_scr = sn.get("score") or 50

            # ── Unpack Engine 2
            sw2       = e2.get("sweep", {})
            sweep_ok  = sw2.get("detected", False)
            reaction  = sw2.get("reaction", "None")

            # ── Unpack Engine 3
            choch_ok  = e3.get("choch", {}).get("detected", False)
            bos_ok    = e3.get("bos",   {}).get("confirmed", False)
            strength  = e3.get("strength", "Weak")
            manip     = e3.get("manipulation", False)

            # ── Unpack Engine 4
            e4_dec    = e4.get("decision", "NO_TRADE")
            entry_low  = e4.get("entry_low")
            entry_high = e4.get("entry_high")
            sl        = e4.get("stop_loss")
            tp1       = e4.get("tp1")
            tp2       = e4.get("tp2")
            tp3       = e4.get("tp3")
            rr        = e4.get("risk_reward") or 0

            # ════════════════════════════════════════════════════════
            # STEP 1 — Build confidence score
            # ════════════════════════════════════════════════════════
            scores = self._score(
                session, vol, funding, sent_scr,
                sweep_ok, reaction,
                choch_ok, bos_ok, strength, manip,
                e4_dec, rr
            )

            raw_total = sum(scores.values())
            total     = max(0, min(100, raw_total))

            # ════════════════════════════════════════════════════════
            # STEP 2 — Confidence label + risk %
            # ════════════════════════════════════════════════════════
            if e4_dec == "NO_TRADE" or total < SCORE_WEAK:
                label, risk_pct, final = "No Trade",         RISK_NONE, "NO_TRADE"
            elif total < SCORE_GOOD:
                label, risk_pct, final = "Weak Setup",       RISK_WEAK, e4_dec
            elif total < SCORE_HIGH:
                label, risk_pct, final = "Good Setup",       RISK_GOOD, e4_dec
            else:
                label, risk_pct, final = "High Probability", RISK_HIGH, e4_dec

            # ════════════════════════════════════════════════════════
            # STEP 3 — SL validation
            # ════════════════════════════════════════════════════════
            sl_valid, sl_note = self._validate_sl(e4_dec, entry_low, entry_high, sl, atr, rr)
            if not sl_valid:
                total = max(0, total - 10)  # penalty for tight SL

            # ════════════════════════════════════════════════════════
            # STEP 4 — TP management plan
            # ════════════════════════════════════════════════════════
            management = {
                "breakeven_at":          "TP1 hit",
                "tp1_action":            "Close 50% — lock partial profit",
                "tp2_action":            "Close 30% — trail SL below structure",
                "tp3_action":            "Close 20% — let runner go",
                "trail_method":          "Below last swing low (BUY) / Above last swing high (SELL)",
                "exit_on_choch":         True,
                "exit_on_session_close": False,
            }

            # ════════════════════════════════════════════════════════
            # STEP 5 — Summary text
            # ════════════════════════════════════════════════════════
            summary = self._build_summary(
                session, sweep_ok, reaction, choch_ok, bos_ok,
                strength, manip, funding, sent_scr, e4_dec, sl_note, total
            )

            return {
                "pair":           pair,
                "final_decision": final,
                "confidence":     total,
                "label":          label,
                "risk_pct":       risk_pct,
                "score_breakdown": scores,
                "sl_valid":       sl_valid,
                "sl_note":        sl_note,
                "management":     management,
                "summary":        summary,
                "ok":             True,
            }

        except Exception as e:
            log.error("Engine5 [%s] error: %s", pair, e)
            return {"pair": pair, "ok": False, "confidence": 0,
                    "final_decision": "NO_TRADE", "error": str(e)}

    # ── Scoring breakdown ────────────────────────────────────────────
    @staticmethod
    def _score(session, vol, funding, sent_scr,
               sweep_ok, reaction,
               choch_ok, bos_ok, strength, manip,
               e4_dec, rr) -> dict:
        s = {}

        # Session quality
        s["session"] = 20 if session in ("London", "New York") else 5

        # Liquidity sweep quality
        if sweep_ok and reaction == "Reversal":
            s["liquidity"] = 25
        elif sweep_ok:
            s["liquidity"] = 10
        else:
            s["liquidity"] = 0

        # Structure confirmation
        if choch_ok and bos_ok:
            s["structure"] = 25
        elif choch_ok and strength == "Strong":
            s["structure"] = 18
        elif choch_ok:
            s["structure"] = 12
        else:
            s["structure"] = 0

        # Funding alignment with trade direction
        if e4_dec == "BUY":
            s["funding"] = 10 if funding == "Short-heavy" else 5 if funding == "Neutral" else -5
        elif e4_dec == "SELL":
            s["funding"] = 10 if funding == "Long-heavy"  else 5 if funding == "Neutral" else -5
        else:
            s["funding"] = 0

        # Sentiment alignment
        if e4_dec == "BUY"  and sent_scr <= 45:
            s["sentiment"] = 5
        elif e4_dec == "SELL" and sent_scr >= 65:
            s["sentiment"] = 5
        else:
            s["sentiment"] = 0

        # Volatility (Low now penalises instead of killing the signal)
        s["volatility"] = 10 if vol == "High" else 5 if vol == "Medium" else -10

        # Manipulation bonus (sweep → CHoCH = smart money confirmed)
        s["manipulation"] = 5 if manip else 0

        # RR bonus
        if rr >= IDEAL_RR:
            s["rr_bonus"] = 5
        elif rr >= 1.5:
            s["rr_bonus"] = 2
        else:
            s["rr_bonus"] = 0

        return s

    # ── SL validation ────────────────────────────────────────────────
    @staticmethod
    def _validate_sl(e4_dec, entry_low, entry_high, sl, atr, rr) -> tuple:
        if not sl or not entry_low or not entry_high:
            return True, ""
        entry_mid = (entry_low + entry_high) / 2
        sl_dist   = abs(entry_mid - sl)
        notes     = []

        if atr and sl_dist < atr * 0.8:
            notes.append("SL too tight — prone to being hunted")
        if rr > 0 and rr < 1.5:
            notes.append(f"RR {rr:.2f}:1 is below minimum 1.5:1")

        return (len(notes) == 0), " | ".join(notes)

    # ── Summary text ─────────────────────────────────────────────────
    @staticmethod
    def _build_summary(session, sweep_ok, reaction, choch_ok, bos_ok,
                       strength, manip, funding, sent_scr, e4_dec, sl_note, score) -> str:
        parts = []

        if manip:
            parts.append("Smart money pattern: Sweep → CHoCH detected")
        elif sweep_ok and reaction == "Reversal":
            parts.append("Clean liquidity sweep with confirmed reversal")
        elif sweep_ok:
            parts.append("Sweep detected — reaction weak")

        if choch_ok and bos_ok:
            parts.append("Full structure confirmation: CHoCH + BOS")
        elif choch_ok:
            parts.append(f"CHoCH confirmed ({strength} structure)")

        if session in ("London", "New York"):
            parts.append(f"High-liquidity session: {session}")
        else:
            parts.append(f"Session: {session} — lower liquidity")

        if e4_dec == "BUY" and funding == "Short-heavy":
            parts.append("Shorts will fuel the rally (funding aligned)")
        elif e4_dec == "SELL" and funding == "Long-heavy":
            parts.append("Longs will fuel the drop (funding aligned)")

        if e4_dec == "BUY"  and sent_scr <= 35:
            parts.append("Extreme fear — accumulation zone")
        elif e4_dec == "SELL" and sent_scr >= 75:
            parts.append("Extreme greed — distribution zone")

        if sl_note:
            parts.append(f"⚠ {sl_note}")

        if score >= 80:
            parts.append("🔥 High-probability setup")
        elif score >= 60:
            parts.append("✅ Good setup — standard risk")
        elif score >= 40:
            parts.append("⚠ Weak setup — reduce size")
        else:
            parts.append("❌ Insufficient confluence — skip")

        return " | ".join(parts) if parts else "No confluences detected"
