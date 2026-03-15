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



def detect_divergence(close: pd.Series, rsi_series: pd.Series, macd_hist: pd.Series, lookback: int = 30) -> dict:
    """Определяет дивергенцию между ценой и RSI/MACD."""
    if len(close) < lookback:
        return {"rsi": None, "macd": None}

    price = close.iloc[-lookback:]
    rsi   = rsi_series.iloc[-lookback:]
    hist  = macd_hist.iloc[-lookback:]

    def find_pivots(series, order=5):
        highs, lows = [], []
        for i in range(order, len(series) - order):
            window = series.iloc[i-order:i+order+1]
            if series.iloc[i] == window.max():
                highs.append(i)
            if series.iloc[i] == window.min():
                lows.append(i)
        return highs, lows

    price_highs, price_lows = find_pivots(price)
    rsi_highs,   rsi_lows   = find_pivots(rsi)
    hist_highs,  hist_lows  = find_pivots(hist)

    rsi_div  = None
    macd_div = None

    # Бычья RSI: цена ниже, RSI выше
    if len(price_lows) >= 2 and len(rsi_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        r1, r2 = rsi_lows[-2],   rsi_lows[-1]
        if price.iloc[p2] < price.iloc[p1] and rsi.iloc[r2] > rsi.iloc[r1]:
            rsi_div = "bullish"

    # Медвежья RSI: цена выше, RSI ниже
    if rsi_div is None and len(price_highs) >= 2 and len(rsi_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        r1, r2 = rsi_highs[-2],   rsi_highs[-1]
        if price.iloc[p2] > price.iloc[p1] and rsi.iloc[r2] < rsi.iloc[r1]:
            rsi_div = "bearish"

    # Скрытая бычья RSI: цена выше min, RSI ниже — продолжение роста
    if rsi_div is None and len(price_lows) >= 2 and len(rsi_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        r1, r2 = rsi_lows[-2],   rsi_lows[-1]
        if price.iloc[p2] > price.iloc[p1] and rsi.iloc[r2] < rsi.iloc[r1]:
            rsi_div = "hidden_bullish"

    # Скрытая медвежья RSI: цена ниже max, RSI выше — продолжение падения
    if rsi_div is None and len(price_highs) >= 2 and len(rsi_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        r1, r2 = rsi_highs[-2],   rsi_highs[-1]
        if price.iloc[p2] < price.iloc[p1] and rsi.iloc[r2] > rsi.iloc[r1]:
            rsi_div = "hidden_bearish"

    # Бычья MACD: цена ниже, гистограмма выше
    if len(price_lows) >= 2 and len(hist_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        h1, h2 = hist_lows[-2],  hist_lows[-1]
        if price.iloc[p2] < price.iloc[p1] and hist.iloc[h2] > hist.iloc[h1]:
            macd_div = "bullish"

    # Медвежья MACD: цена выше, гистограмма ниже
    if macd_div is None and len(price_highs) >= 2 and len(hist_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        h1, h2 = hist_highs[-2],  hist_highs[-1]
        if price.iloc[p2] > price.iloc[p1] and hist.iloc[h2] < hist.iloc[h1]:
            macd_div = "bearish"

    return {"rsi": rsi_div, "macd": macd_div}


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
        # Стоп-лосс ВСЕГДА ниже цены, TP ВСЕГДА выше
        stop_loss = round(price - atr * 1.5, 4)
        tp1       = round(price + atr * 2,   4)
        tp2       = round(price + atr * 4,   4)
    elif signal == "SELL":
        # Шорт: стоп-лосс выше цены, TP ниже
        stop_loss = round(price + atr * 1.5, 4)
        tp1       = round(price - atr * 2,   4)
        tp2       = round(price - atr * 4,   4)
    else:  # HOLD — показываем уровни как для потенциальной покупки
        stop_loss = round(price - atr * 2,   4)  # широкий стоп 
        tp1       = round(price + atr * 1.5, 4)
        tp2       = round(price + atr * 3,   4)

    # Гарантируем правильное направление независимо от ATR
    if signal in ("BUY", "HOLD"):
        stop_loss = min(stop_loss, round(price * 0.96, 4))  # SL не выше -4%
        tp1       = max(tp1,       round(price * 1.02, 4))  # TP1 не ниже +2%
        tp2       = max(tp2,       round(price * 1.04, 4))  # TP2 не ниже +4%
    else:
        stop_loss = max(stop_loss, round(price * 1.03, 4))
        tp1       = min(tp1,       round(price * 0.97, 4))
        tp2       = min(tp2,       round(price * 0.94, 4))

    stop_pct = round((stop_loss - price) / price * 100, 2)
    tp1_pct  = round((tp1  - price) / price * 100, 2)
    tp2_pct  = round((tp2  - price) / price * 100, 2)

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
    sma30 = round(float(close.rolling(30).mean().iloc[-1]), 4)
    sma90 = round(float(close.rolling(90).mean().iloc[-1]), 4) if len(close) >= 90 else None
    bb = calc_bollinger(close)
    levels = find_support_resistance(df)
    # Полные серии для дивергенции
    rsi_full  = close.apply(lambda x: x)  # placeholder
    rsi_series = pd.Series([
        float(v) for v in
        close.ewm(com=13, adjust=False).mean() / close.ewm(com=13, adjust=False).mean()
    ])
    # Считаем полный RSI как серию
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs_series = avg_gain / avg_loss.replace(0, 1e-10)
    rsi_full_series = 100 - (100 / (1 + rs_series))

    ema12_s = close.ewm(span=12, adjust=False).mean()
    ema26_s = close.ewm(span=26, adjust=False).mean()
    macd_line_s = ema12_s - ema26_s
    signal_line_s = macd_line_s.ewm(span=9, adjust=False).mean()
    macd_hist_s = macd_line_s - signal_line_s

    divergence = detect_divergence(close, rsi_full_series, macd_hist_s)
    signal_data = determine_signal(rsi, macd, price, ema50, ema200, bb)

    # Сигнал MA30/90 — свежее пересечение (последние 3 свечи)
    ma_signal = "neutral"
    ma_cross = None
    ma_cross_distance = None
    ma_cross_candles_ago = None   # сколько свечей назад произошло пересечение

    if sma90 and len(close) >= 95:
        sma30_series = close.rolling(30).mean()
        sma90_series = close.rolling(90).mean()

        cur30  = float(sma30_series.iloc[-1])
        cur90  = float(sma90_series.iloc[-1])

        # Текущий разрыв в %
        gap_now = (cur30 - cur90) / cur90 * 100
        ma_cross_distance = round(gap_now, 3)

        # Ищем пересечение среди последних 3 свечей
        # Проверяем: была ли смена знака разрыва в последних 3 свечах
        for i in range(1, 4):
            past30 = float(sma30_series.iloc[-1 - i])
            past90 = float(sma90_series.iloc[-1 - i])
            gap_past = (past30 - past90) / past90 * 100

            # Смена знака = пересечение
            if gap_past < 0 and gap_now > 0:
                # MA30 была ниже MA90, теперь выше — Золотой крест
                ma_cross = "golden"
                ma_cross_candles_ago = i
                break
            elif gap_past > 0 and gap_now < 0:
                # MA30 была выше MA90, теперь ниже — Крест смерти
                ma_cross = "death"
                ma_cross_candles_ago = i
                break

        # Текущий тренд MA
        if cur30 > cur90:
            ma_signal = "bullish"
        elif cur30 < cur90:
            ma_signal = "bearish"
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
            "sma30": sma30,
            "sma90": sma90,
            "ma_signal": ma_signal,
            "ma_cross": ma_cross,
            "ma_cross_candles_ago": ma_cross_candles_ago,
            "ma_cross_distance": ma_cross_distance,
            "bollinger": bb,
            "atr": round(atr, 4),
            "divergence": divergence
        },
        "levels": levels,
        "signal": signal_data,
        "trade": trade
    }



@app.get("/futures/{symbol}")
async def get_futures(symbol: str):
    """Данные по фьючерсам: открытый интерес, фандинг, лонги/шорты"""
    symbol = symbol.upper()
    async with httpx.AsyncClient(timeout=15) as client:
        # Открытый интерес
        oi_r = await client.get(
            f"{BYBIT_BASE}/v5/market/open-interest",
            params={"category": "linear", "symbol": f"{symbol}USDT", "intervalTime": "1h", "limit": 1}
        )
        # Фандинг рейт
        fr_r = await client.get(
            f"{BYBIT_BASE}/v5/market/funding/history",
            params={"category": "linear", "symbol": f"{symbol}USDT", "limit": 1}
        )
        # Тикер фьючерса (лонги/шорты ratio)
        tk_r = await client.get(
            f"{BYBIT_BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": f"{symbol}USDT"}
        )

    result = {"symbol": symbol}

    try:
        oi_data = oi_r.json()
        if oi_data.get("result", {}).get("list"):
            oi = float(oi_data["result"]["list"][0]["openInterest"])
            result["open_interest"] = round(oi, 2)
            result["open_interest_fmt"] = f"{oi/1e6:.2f}M" if oi > 1e6 else f"{oi/1e3:.1f}K"
    except: pass

    try:
        fr_data = fr_r.json()
        if fr_data.get("result", {}).get("list"):
            fr = float(fr_data["result"]["list"][0]["fundingRate"]) * 100
            result["funding_rate"] = round(fr, 4)
            if fr > 0.05:
                result["funding_signal"] = f"🔴 Высокий фандинг +{fr:.4f}% — лонги переплачивают, рынок перегрет"
            elif fr < -0.05:
                result["funding_signal"] = f"🟢 Отрицательный фандинг {fr:.4f}% — шорты переплачивают, возможен рост"
            elif fr > 0:
                result["funding_signal"] = f"⚪ Фандинг +{fr:.4f}% — нейтрально, небольшой перевес лонгов"
            else:
                result["funding_signal"] = f"⚪ Фандинг {fr:.4f}% — нейтрально, небольшой перевес шортов"
    except: pass

    try:
        tk_data = tk_r.json()
        if tk_data.get("result", {}).get("list"):
            t = tk_data["result"]["list"][0]
            price = float(t.get("lastPrice", 0))
            mark  = float(t.get("markPrice", 0))
            index = float(t.get("indexPrice", 0))
            oi_val = float(t.get("openInterestValue", 0))
            result["mark_price"]  = round(mark, 4)
            result["index_price"] = round(index, 4)
            result["oi_value_usd"] = round(oi_val, 0)
            result["oi_value_fmt"] = f"${oi_val/1e6:.1f}M" if oi_val > 1e6 else f"${oi_val/1e3:.0f}K"
            # Basis (разница спот vs фьючерс)
            if price > 0 and index > 0:
                basis = (price - index) / index * 100
                result["basis"] = round(basis, 4)
                if basis > 0.3:
                    result["basis_signal"] = f"🔴 Фьючерс дороже спота на {basis:.2f}% — перегрев"
                elif basis < -0.3:
                    result["basis_signal"] = f"🟢 Фьючерс дешевле спота на {abs(basis):.2f}% — недооценён"
                else:
                    result["basis_signal"] = f"⚪ Фьючерс ≈ спот (±{abs(basis):.2f}%) — нейтрально"
    except: pass

    return result


@app.get("/screener")
async def screener(coins: str = "ETH,SOL,XRP,ADA,LTC,LINK,ATOM,BCH,NEAR,ICP,TON,HYPE,AVAX,SUI,DOT,OP"):
    """Скрининг монет — ищет лучшие возможности прямо сейчас"""
    symbols = [s.strip().upper() for s in coins.split(",")]

    async def analyze_one(sym):
        try:
            df = await get_klines(sym, "60", 200)
            if df is None or len(df) < 50:
                return None
            close = df["close"]
            price = float(close.iloc[-1])
            price_24h_ago = float(close.iloc[-25]) if len(close) > 25 else float(close.iloc[0])
            chg24 = round((price - price_24h_ago) / price_24h_ago * 100, 2)

            rsi    = calc_rsi(close)
            macd   = calc_macd(close)
            ema50  = calc_ema(close, 50)
            ema200 = calc_ema(close, 200)
            bb     = calc_bollinger(close)
            sma30  = round(float(close.rolling(30).mean().iloc[-1]), 4)
            sma90  = round(float(close.rolling(90).mean().iloc[-1]), 4) if len(close) >= 90 else None
            signal = determine_signal(rsi, macd, price, ema50, ema200, bb)

            # Score for ranking
            score = signal["score"]
            # Bonus for RSI in buy zone
            if 35 < rsi < 55: score += 1
            # Bonus for price above both MAs
            if sma90 and sma30 > sma90 and price > sma30: score += 1

            return {
                "symbol": sym,
                "price": round(price, 4),
                "change_24h": chg24,
                "signal": signal["signal"],
                "confidence": signal["confidence"],
                "score": round(score, 2),
                "rsi": rsi,
                "ma_trend": "up" if (sma90 and sma30 > sma90) else "down" if (sma90 and sma30 < sma90) else "neutral",
                "trend": "up" if price > ema50 > ema200 else "down" if price < ema50 < ema200 else "mixed",
            }
        except:
            return None

    results = await asyncio.gather(*[analyze_one(s) for s in symbols])
    valid = [r for r in results if r]
    # Sort: BUY first by score desc, then HOLD, then SELL
    order = {"BUY": 0, "HOLD": 1, "SELL": 2}
    valid.sort(key=lambda x: (order.get(x["signal"], 3), -x["score"]))
    return valid

@app.get("/klines/{symbol}")
async def get_klines_endpoint(symbol: str, interval: str = "60", limit: int = 60):
    """Свечи для графика дашборда"""
    symbol = symbol.upper()
    df = await get_klines(symbol, interval, min(limit, 200))
    if df is None or len(df) == 0:
        return []
    result = []
    for _, row in df.iterrows():
        result.append([
            int(row["timestamp"]) if "timestamp" in df.columns else 0,
            str(round(float(row["open"]),  6)),
            str(round(float(row["high"]),  6)),
            str(round(float(row["low"]),   6)),
            str(round(float(row["close"]), 6)),
            str(round(float(row["volume"]),2)),
        ])
    return result


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


@app.get("/history/{symbol}")
async def get_history(symbol: str):
    """
    Исторический анализ: динамика за 24ч/7д/30д/год,
    тренды, максимумы/минимумы, объёмы
    """
    symbol = symbol.upper()

    # Параллельно тянем разные периоды
    async def fetch(interval, limit):
        try:
            df = await get_klines(symbol, interval, limit)
            return df
        except:
            return None

    # Дневные свечи за 365 дней + часовые за 7 дней
    daily, hourly = await asyncio.gather(
        fetch("D", 365),
        fetch("60", 168)
    )

    result = {"symbol": symbol}

    if daily is not None and len(daily) > 0:
        price_now = float(daily["close"].iloc[-1])

        # Динамика по периодам
        def pct_change(days_ago):
            idx = max(0, len(daily) - days_ago - 1)
            old_price = float(daily["close"].iloc[idx])
            return round((price_now - old_price) / old_price * 100, 2) if old_price else 0

        result["price_changes"] = {
            "24h":  pct_change(1),
            "7d":   pct_change(7),
            "30d":  pct_change(30),
            "90d":  pct_change(90),
            "365d": pct_change(364),
        }

        # Максимум/минимум за год
        year_data = daily.tail(365)
        result["year_high"] = round(float(year_data["high"].max()), 4)
        result["year_low"]  = round(float(year_data["low"].min()), 4)
        result["from_high_pct"] = round((price_now - result["year_high"]) / result["year_high"] * 100, 2)
        result["from_low_pct"]  = round((price_now - result["year_low"])  / result["year_low"]  * 100, 2)

        # Тренды
        def trend(days):
            if len(daily) < days:
                return "недостаточно данных"
            recent = daily.tail(days)["close"]
            slope = float(recent.iloc[-1]) - float(recent.iloc[0])
            pct = slope / float(recent.iloc[0]) * 100
            if pct > 5:   return f"🟢 Рост +{pct:.1f}%"
            elif pct < -5: return f"🔴 Падение {pct:.1f}%"
            else:          return f"⚪ Боковик {pct:+.1f}%"

        result["trends"] = {
            "3d":  trend(3),
            "7d":  trend(7),
            "30d": trend(30),
            "90d": trend(90),
        }

        # Серия дней роста/падения подряд
        closes = daily["close"].tail(30).values
        streak = 0
        direction = None
        for i in range(len(closes)-1, 0, -1):
            if closes[i] > closes[i-1]:
                if direction is None: direction = "up"
                if direction == "up": streak += 1
                else: break
            elif closes[i] < closes[i-1]:
                if direction is None: direction = "down"
                if direction == "down": streak += 1
                else: break
            else:
                break

        if direction == "up":
            result["streak"] = f"🟢 Растёт {streak} дней подряд"
        elif direction == "down":
            result["streak"] = f"🔴 Падает {streak} дней подряд"
        else:
            result["streak"] = "⚪ Без чёткого направления"

        # Объём — сравниваем текущий с средним
        avg_vol = float(daily["volume"].tail(30).mean())
        cur_vol = float(daily["volume"].iloc[-1])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1
        if vol_ratio > 1.5:
            result["volume_signal"] = f"📈 Объём выше нормы в {vol_ratio:.1f}x — повышенный интерес"
        elif vol_ratio < 0.5:
            result["volume_signal"] = f"📉 Объём ниже нормы в {1/vol_ratio:.1f}x — низкий интерес"
        else:
            result["volume_signal"] = f"⚪ Объём в норме"

    return result


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
