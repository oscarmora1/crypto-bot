# 🤖 Crypto DayTrading Bot — BTC/USD

Automated trading bot para Alpaca Markets Paper Trading.
Corre cada **15 minutos** via GitHub Actions con estrategia **RSI + Bollinger Bands**.

## Estrategia

| Señal | Condición |
|-------|-----------|
| **BUY**  | RSI < 35 **Y** precio toca/cruza la Banda Inferior de Bollinger |
| **SELL** | RSI > 65 **Y** precio toca/cruza la Banda Superior de Bollinger |
| **HOLD** | Cualquier otro caso |

**Gestión de riesgo:** máximo 2% del equity por trade, con sizing basado en ATR.

## Setup

### 1. Configura los GitHub Secrets

Ve a tu repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Valor |
|--------|-------|
| `ALPACA_API_KEY` | Tu Alpaca Paper API Key |
| `ALPACA_API_SECRET` | Tu Alpaca Paper API Secret |

### 2. Activa GitHub Actions

Ve a la pestaña **Actions** y habilita los workflows.
El bot correrá automáticamente cada 15 minutos.

### 3. Trigger manual

En **Actions → Crypto Trading Bot → Run workflow** para ejecutar de inmediato.

## Ejecución local

```bash
pip install -r requirements.txt
export ALPACA_API_KEY="tu_key"
export ALPACA_API_SECRET="tu_secret"
python bot.py
```

## Parámetros configurables en bot.py

```
TRADE_BUDGET    = 140.0    # max $ a invertir
MAX_RISK_PCT    = 0.02     # arriesgar max 2% del equity por trade
RSI_OVERSOLD    = 35       # umbral RSI para compra
RSI_OVERBOUGHT  = 65       # umbral RSI para venta
BB_PERIOD       = 20       # periodo Bandas de Bollinger
```

> ⚠️ Paper Trading only. Este bot usa dinero simulado. No es asesoría financiera.
