"""
Crypto DayTrading Bot - BTC/USD
Strategy: RSI + Bollinger Bands + ATR-based position sizing
Exchange: Alpaca Markets (Paper Trading)
Interval: Every 15 minutes
"""

import os
import logging
import time
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import numpy as np

# â”€â”€ Logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
BASE_URL   = "https://paper-api.alpaca.markets"
DATA_URL   = "https://data.alpaca.markets"

SYMBOL          = "BTC/USD"
SYMBOL_CLEAN    = "BTCUSD"          # used in some endpoints
TRADE_BUDGET    = 140.0             # keep $10 as buffer from the $150
MAX_RISK_PCT    = 0.02              # risk max 2% of portfolio per trade
RSI_PERIOD      = 14
BB_PERIOD       = 20
BB_STD          = 2.0
RSI_OVERSOLD    = 35
RSI_OVERBOUGHT  = 65
BARS_NEEDED     = 60               # fetch enough bars for indicators

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json",
}


# â”€â”€ Alpaca helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def get_position(symbol):
    """Return current position dict or None."""
    r = requests.get(f"{BASE_URL}/v2/positions/{SYMBOL_CLEAN}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_bars(symbol: str, timeframe: str = "15Min", limit: int = BARS_NEEDED):
    """Fetch OHLCV bars from Alpaca Crypto Data API."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=limit * 0.25 * 2)   # generous window

    params = {
        "symbols": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "limit": limit,
        "feed": "us",
    }
    r = requests.get(
        f"{DATA_URL}/v1beta3/crypto/us/bars",
        headers=HEADERS,
        params=params,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    bars = data.get("bars", {}).get(symbol, [])
    if not bars:
        raise ValueError(f"No bars returned for {symbol}")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"])
    df = df.sort_values("t").reset_index(drop=True)
    df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    return df


def place_order(side: str, notional: float):
    """Place a market order by notional (dollar) amount."""
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


def close_position(symbol: str):
    """Close entire position for a symbol."""
    r = requests.delete(f"{BASE_URL}/v2/positions/{SYMBOL_CLEAN}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        log.info("No position to close.")
        return None
    r.raise_for_status()
    return r.json()


# â”€â”€ Indicators â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def compute_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_bollinger(series: pd.Series, period: int = BB_PERIOD, std: float = BB_STD):
    mid   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma  # lower, mid, upper


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


# â”€â”€ Signal logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def generate_signal(df: pd.DataFrame) -> str:
    """
    Returns 'buy', 'sell', or 'hold'.

    BUY  when:  RSI < oversold  AND  price touches/below lower BB
    SELL when:  RSI > overbought AND price touches/above upper BB
    """
    df["rsi"]  = compute_rsi(df["close"])
    df["bb_lo"], df["bb_mid"], df["bb_hi"] = compute_bollinger(df["close"])

    last = df.iloc[-1]
    rsi, price = last["rsi"], last["close"]
    bb_lo, bb_hi = last["bb_lo"], last["bb_hi"]

    log.info(
        "Price=%.2f | RSI=%.1f | BB_lo=%.2f | BB_hi=%.2f",
        price, rsi, bb_lo, bb_hi,
    )

    if rsi < RSI_OVERSOLD and price <= bb_lo * 1.005:   # 0.5% tolerance
        return "buy"
    if rsi > RSI_OVERBOUGHT and price >= bb_hi * 0.995:
        return "sell"
    return "hold"


# â”€â”€ Position sizing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def calc_notional(equity: float, atr: float, price: float) -> float:
    """
    Risk a fixed % of equity per trade (like a seat-belt for your $150).
    notional = min(budget, risk_amount)
    """
    risk_dollars = equity * MAX_RISK_PCT          # e.g. 2% of $150 = $3
    atr_pct      = atr / price                    # ATR as % of price
    # notional such that a 1-ATR move costs exactly risk_dollars
    sized        = risk_dollars / max(atr_pct, 0.001)
    return round(min(sized, TRADE_BUDGET, equity * 0.95), 2)


# â”€â”€ Main loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

    # â”€â”€ Fetch market data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    df = get_bars(SYMBOL)
    log.info("Fetched %d bars (last close=%.2f)", len(df), df["close"].iloc[-1])

    if len(df) < BB_PERIOD + 5:
        log.warning("Not enough bars (%d). Skipping.", len(df))
        return

    # â”€â”€ Indicators â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    atr    = compute_atr(df).iloc[-1]
    signal = generate_signal(df)
    log.info("Signal -> %s  |  ATR=%.2f", signal.upper(), atr)

    # â”€â”€ Current position â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    position = get_position(SYMBOL_CLEAN)
    has_position = position is not None
    if has_position:
        qty   = float(position["qty"])
        unrpnl = float(position["unrealized_pl"])
        log.info("Position: qty=%.6f BTC  unrealized_PnL=$%.2f", qty, unrpnl)

    # â”€â”€ Execute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        result = close_position(SYMBOL_CLEAN)
        log.info("Position closed: %s", result)

    elif signal == "sell" and not has_position:
        log.info("Sell signal but no position -- skipping short selling.")

    else:
        log.info("HOLD -- no action taken.")

    log.info("=" * 60)


if __name__ == "__main__":
    run()

