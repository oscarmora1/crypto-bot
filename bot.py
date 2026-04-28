"""
Crypto DayTrading Bot - BTC/USD
Strategy: RSI + Bollinger Bands + ATR-based position sizing
Exchange: Alpaca Markets (Paper Trading)
Interval: Every 15 minutes
"""

import os
import logging
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
BASE_URL   = "https://paper-api.alpaca.markets"
DATA_URL   = "https://data.alpaca.markets"

SYMBOL         = "BTC/USD"
SYMBOL_CLEAN   = "BTCUSD"
TRADE_BUDGET   = 140.0
MAX_RISK_PCT   = 0.02
RSI_PERIOD     = 14
BB_PERIOD      = 20
BB_STD         = 2.0
RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65
BARS_NEEDED    = 60

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json",
}


def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def get_position():
    r = requests.get(f"{BASE_URL}/v2/positions/{SYMBOL_CLEAN}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_bars():
    """Fetch OHLCV bars — URL built as string to avoid %2F encoding BTC/USD."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=BARS_NEEDED * 0.5)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str   = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Use BTCUSD (no slash) for the data endpoint
    url = (
        f"{DATA_URL}/v1beta3/crypto/us/bars"
        f"?symbols=BTC%2FUSD&timeframe=15Min"
        f"&start={start_str}&end={end_str}&limit={BARS_NEEDED}"
    )
    r = requests.get(url, headers=HEADERS, timeout=15)
    log.info("Bars URL: %s", url)
    log.info("Bars response status: %s", r.status_code)
    if not r.ok:
        log.error("Bars error body: %s", r.text)
    r.raise_for_status()
    data = r.json()
    log.info("Bars keys: %s", list(data.get("bars", {}).keys()))
    bars = data.get("bars", {}).get("BTC/USD", [])
    if not bars:
        raise ValueError(f"No bars returned. Full response: {data}")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"])
    df = df.sort_values("t").reset_index(drop=True)
    df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    return df


def place_order(side: str, notional: float):
    payload = {
        "symbol": SYMBOL_CLEAN,
        "notional": str(round(notional, 2)),
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
    }
    r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=10)
    if not r.ok:
        log.error("Order failed: %s", r.text)
        r.raise_for_status()
    return r.json()


def close_position():
    r = requests.delete(f"{BASE_URL}/v2/positions/{SYMBOL_CLEAN}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        log.info("No position to close.")
        return None
    r.raise_for_status()
    return r.json()


def compute_rsi(series: pd.Series) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_bollinger(series: pd.Series):
    mid   = series.rolling(BB_PERIOD).mean()
    sigma = series.rolling(BB_PERIOD).std()
    return mid - BB_STD * sigma, mid, mid + BB_STD * sigma


def compute_atr(df: pd.DataFrame) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=13, min_periods=14).mean()


def generate_signal(df: pd.DataFrame) -> str:
    df["rsi"] = compute_rsi(df["close"])
    df["bb_lo"], df["bb_mid"], df["bb_hi"] = compute_bollinger(df["close"])
    last = df.iloc[-1]
    rsi, price = last["rsi"], last["close"]
    bb_lo, bb_hi = last["bb_lo"], last["bb_hi"]
    log.info("Price=%.2f | RSI=%.1f | BB_lo=%.2f | BB_hi=%.2f", price, rsi, bb_lo, bb_hi)
    if rsi < RSI_OVERSOLD and price <= bb_lo * 1.005:
        return "buy"
    if rsi > RSI_OVERBOUGHT and price >= bb_hi * 0.995:
        return "sell"
    return "hold"


def calc_notional(equity: float, atr: float, price: float) -> float:
    risk_dollars = equity * MAX_RISK_PCT
    atr_pct      = atr / price
    sized        = risk_dollars / max(atr_pct, 0.001)
    return round(min(sized, TRADE_BUDGET, equity * 0.95), 2)


def run():
    log.info("=" * 60)
    log.info("  Crypto Bot starting -- %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    account = get_account()
    equity  = float(account["equity"])
    cash    = float(account["cash"])
    log.info("Account equity=$%.2f  cash=$%.2f", equity, cash)

    if equity < 5:
        log.warning("Equity too low ($%.2f). Stopping.", equity)
        return

    df = get_bars()
    log.info("Fetched %d bars (last close=%.2f)", len(df), df["close"].iloc[-1])

    if len(df) < BB_PERIOD + 5:
        log.warning("Not enough bars (%d). Skipping.", len(df))
        return

    atr    = compute_atr(df).iloc[-1]
    signal = generate_signal(df)
    log.info("Signal -> %s  |  ATR=%.2f", signal.upper(), atr)

    position     = get_position()
    has_position = position is not None
    if has_position:
        log.info("Position: qty=%.6f BTC  unrealized_PnL=$%.2f",
                 float(position["qty"]), float(position["unrealized_pl"]))

    if signal == "buy" and not has_position:
        notional = calc_notional(equity, atr, df["close"].iloc[-1])
        if cash >= notional and notional >= 1.0:
            log.info("BUY $%.2f of BTC", notional)
            order = place_order("buy", notional)
            log.info("Order placed: id=%s", order.get("id"))
        else:
            log.info("Insufficient cash ($%.2f) or notional too small ($%.2f)", cash, notional)
    elif signal == "sell" and has_position:
        log.info("SELL / close BTC position")
        log.info("Position closed: %s", close_position())
    elif signal == "sell" and not has_position:
        log.info("Sell signal but no position -- skipping.")
    else:
        log.info("HOLD -- no action taken.")

    log.info("=" * 60)


if __name__ == "__main__":
    run()
