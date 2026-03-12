"""
LUNA Crypto Analyst — Backend Server
Получает реальные данные с Bybit, считает индикаторы, возвращает сигналы
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import pandas as pd
import numpy as np
from typing import Optional
import asyncio

app = FastAPI(title="LUNA Crypto API")

# Разрешаем запросы с любого домена (нужно для фронтенда)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BYBIT_BASE = "https://api.bybit.com"


# ─── ПОЛУЧЕНИЕ СВЕЧЕЙ С BYBIT ───────────────────────────────────────────────
async def get_klines(symbol: str, interval: str = "60", limit: int = 200):
    """
    Получает свечи с Bybit.
    interval: 1=1мин, 5=5мин, 15=15мин, 60=1час, 240=4часа, D=день
    """
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "spot",
        "symbol": f"{symbol}USDT",
        "interval": interval,
        "limit": limit
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params)
        data = r.json()

    if data.get("retCode") != 0:
        raise HTTPException(status_code=400, detail=f"Bybit error: {data.get('retMsg')}")

    rows = data["result"]["list"]
    # Bybit возвращает [time, open, high, low, close, volume, turnover]
    df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume","turnover"])
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms")
    df = df.sort_values("time").reset_index(drop=True)
    return df


# ─── РАСЧЁТ ИНДИКАТОРОВ ─────────────────────────────────────────────────────
def calc_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def calc_macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
        "cross": "bullish" if macd_line.iloc[-1] > signal_line.iloc[-1] else "bearish"
    }


def calc_ema(close: pd.Series, period: int) -> float:
    return round(float(close.ewm(span=period, adjust=False).mean().iloc[-1]), 4)


def calc_bollinger(close: pd.Series, period: int = 20):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    price = close.iloc[-1]
    band_width = float(upper.iloc[-1] - lower.iloc[-1])
    position = (price - float(lower.iloc[-1])) / band_width if band_width > 0 else 0.5
    return {
        "upper": round(float(upper.iloc[-1]), 4),
        "middle": round(float(sma.iloc[-1]), 4),
        "lower": round(float(lower.iloc[-1]), 4),
        "position": round(position, 3)  # 0 = у нижней полосы, 1 = у верхней
    }


def find_support_resistance(df: pd.DataFrame, lookback: int = 50):
    """Ищет уровни поддержки и сопротивления по последним N свечам"""
    recent = df.tail(lookback)
    price = float(df["close"].iloc[-1])

    # Локальные минимумы = поддержка, максимумы = сопротивление
    highs = recent["high"].values
    lows = recent["low"].values

    support_levels = []
    resistance_levels = []

    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            support_levels.append(float(lows[i]))
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistance_levels.append(float(highs[i]))

    # Берём ближайшие к текущей цене
    supports = sorted([s for s in support_levels if s < price], reverse=True)[:2]
    resistances = sorted([r for r in resistance_levels if r > price])[:2]

    return {
        "support": [round(s, 4) for s in supports],
        "resistance": [round(r, 4) for r in resistances]
    }


def determine_signal(rsi: float, macd: dict, price: float, ema50: float, ema200: float, bb: dict) -> dict:
    """Определяет торговый сигнал на основе индикаторов"""
    score = 0  # -10 (сильная продажа) до +10 (сильная покупка)
    reasons = []

    # RSI
    if rsi < 30:
        score += 3
        reasons.append(f"RSI={rsi} — зона перепроданности (сигнал к покупке)")
    elif rsi < 45:
        score += 1
        reasons.append(f"RSI={rsi} — нейтрально-низкий")
    elif rsi > 70:
        score -= 3
        reasons.append(f"RSI={rsi} — зона перекупленности (сигнал к продаже)")
    elif rsi > 55:
        score -= 1
        reasons.append(f"RSI={rsi} — нейтрально-высокий")
    else:
        reasons.append(f"RSI={rsi} — нейтральная зона")

    # MACD
    if macd["cross"] == "bullish" and macd["histogram"] > 0:
        score += 2
        reasons.append("MACD бычий кроссовер — импульс роста")
    elif macd["cross"] == "bearish" and macd["histogram"] < 0:
        score -= 2
        reasons.append("MACD медвежий — импульс снижения")

    # EMA тренд
    if price > ema50 > ema200:
        score += 2
        reasons.append(f"Цена выше EMA50 и EMA200 — устойчивый восходящий тренд")
    elif price < ema50 < ema200:
        score -= 2
        reasons.append(f"Цена ниже EMA50 и EMA200 — нисходящий тренд")
    elif price > ema50:
        score += 1
        reasons.append(f"Цена выше EMA50 — краткосрочный тренд вверх")
    else:
        score -= 1
        reasons.append(f"Цена ниже EMA50 — краткосрочный тренд вниз")

    # Bollinger Bands
    if bb["position"] < 0.1:
        score += 2
        reasons.append("Цена у нижней полосы Bollinger — возможен отскок вверх")
    elif bb["position"] > 0.9:
        score -= 2
        reasons.append("Цена у верхней полосы Bollinger — возможна коррекция")

    # Определяем сигнал
    if score >= 4:
        signal = "BUY"
        label = "ПОКУПАТЬ"
        confidence = min(95, 60 + score * 4)
    elif score >= 2:
        signal = "BUY"
        label = "МОЖНО ПОКУПАТЬ"
        confidence = min(75, 50 + score * 5)
    elif score <= -4:
        signal = "SELL"
        label = "ПРОДАВАТЬ"
        confidence = min(95, 60 + abs(score) * 4)
    elif score <= -2:
        signal = "SELL"
        label = "ОСТОРОЖНО"
        confidence = min(70, 50 + abs(score) * 5)
    else:
        signal = "HOLD"
        label = "ДЕРЖАТЬ / ЖДАТЬ"
        confidence = 50

    return {
        "signal": signal,
        "label": label,
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }


def calc_trade_levels(price: float, signal: str, atr: float):
    """Рассчитывает уровни входа, стоп-лосса и тейк-профита"""
    if signal == "BUY":
        stop_loss = round(price - atr * 1.5, 4)
        tp1 = round(price + atr * 2, 4)
        tp2 = round(price + atr * 4, 4)
        stop_pct = round((stop_loss - price) / price * 100, 2)
        tp1_pct = round((tp1 - price) / price * 100, 2)
        tp2_pct = round((tp2 - price) / price * 100, 2)
    else:
        stop_loss = round(price + atr * 1.5, 4)
        tp1 = round(price - atr * 2, 4)
        tp2 = round(price - atr * 4, 4)
        stop_pct = round((stop_loss - price) / price * 100, 2)
        tp1_pct = round((tp1 - price) / price * 100, 2)
        tp2_pct = round((tp2 - price) / price * 100, 2)

    return {
        "entry": round(price, 4),
        "stop_loss": stop_loss,
        "stop_pct": stop_pct,
        "tp1": tp1,
        "tp1_pct": tp1_pct,
        "tp2": tp2,
        "tp2_pct": tp2_pct
    }


# ─── API ENDPOINTS ───────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "LUNA API running", "version": "1.0"}


@app.get("/analyze/{symbol}")
async def analyze(symbol: str, interval: str = "60"):
    """
    Полный технический анализ монеты.
    symbol: BTC, ETH, SOL и т.д.
    interval: 15, 60, 240, D
    """
    symbol = symbol.upper()

    try:
        df = await get_klines(symbol, interval, limit=200)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    close = df["close"]
    high = df["high"]
    low = df["low"]
    price = float(close.iloc[-1])

    # Считаем ATR для расчёта уровней
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    # Считаем все индикаторы
    rsi = calc_rsi(close)
    macd = calc_macd(close)
    ema50 = calc_ema(close, 50)
    ema200 = calc_ema(close, 200)
    bb = calc_bollinger(close)
    levels = find_support_resistance(df)
    signal_data = determine_signal(rsi, macd, price, ema50, ema200, bb)
    trade = calc_trade_levels(price, signal_data["signal"], atr)

    # Изменение цены за период
    price_24h_ago = float(close.iloc[-25]) if len(close) > 25 else float(close.iloc[0])
    change_24h = round((price - price_24h_ago) / price_24h_ago * 100, 2)

    # Объём
    volume = float(df["volume"].tail(24).sum())

    return {
        "symbol": symbol,
        "pair": f"{symbol}/USDT",
        "interval": interval,
        "price": price,
        "change_24h": change_24h,
        "volume_24h": round(volume, 2),
        "indicators": {
            "rsi": rsi,
            "macd": macd,
            "ema50": ema50,
            "ema200": ema200,
            "bollinger": bb,
            "atr": round(atr, 4)
        },
        "levels": levels,
        "signal": signal_data,
        "trade": trade
    }


@app.get("/price/{symbol}")
async def get_price(symbol: str):
    """Быстрое получение текущей цены"""
    symbol = symbol.upper()
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "spot", "symbol": f"{symbol}USDT"}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url, params=params)
        data = r.json()

    if not data["result"]["list"]:
        raise HTTPException(status_code=404, detail="Symbol not found")

    ticker = data["result"]["list"][0]
    return {
        "symbol": symbol,
        "price": float(ticker["lastPrice"]),
        "change_24h": float(ticker["price24hPcnt"]) * 100,
        "high_24h": float(ticker["highPrice24h"]),
        "low_24h": float(ticker["lowPrice24h"]),
        "volume_24h": float(ticker["volume24h"])
    }


@app.get("/prices")
async def get_multiple_prices(symbols: str = "BTC,ETH,SOL,XRP,BNB"):
    """Цены нескольких монет сразу"""
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    results = {}

    async def fetch_one(sym):
        try:
            url = f"{BYBIT_BASE}/v5/market/tickers"
            params = {"category": "spot", "symbol": f"{sym}USDT"}
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url, params=params)
                data = r.json()
            ticker = data["result"]["list"][0]
            return sym, {
                "price": float(ticker["lastPrice"]),
                "change_24h": round(float(ticker["price24hPcnt"]) * 100, 2)
            }
        except:
            return sym, None

    tasks = [fetch_one(s) for s in symbol_list]
    responses = await asyncio.gather(*tasks)
    for sym, data in responses:
        if data:
            results[sym] = data

    return results
