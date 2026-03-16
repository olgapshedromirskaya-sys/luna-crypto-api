"""
LUNA Crypto Bot — Telegram
Подключён к серверу luna-crypto-api.onrender.com
"""

import os
import asyncio
import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
API_URL   = "https://luna-crypto-api.onrender.com"

# ── ЗАЩИТА: только твой Telegram ID ──────────────────────────────────────────
# Узнай свой ID: напиши боту @userinfobot в Telegram
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # вставь свой ID на Render

# ── ХРАНИЛИЩЕ БЮДЖЕТА (в памяти) ─────────────────────────────────────────────
user_budgets = {}  # {user_id: float}
DEFAULT_BUDGET = 22.0

def get_budget(user_id: int) -> float:
    return user_budgets.get(user_id, DEFAULT_BUDGET)

def set_budget(user_id: int, amount: float):
    user_budgets[user_id] = amount

# ── ПРОВЕРКА ДОСТУПА ──────────────────────────────────────────────────────────
def is_allowed(update: Update) -> bool:
    if OWNER_ID == 0:
        return True  # если ID не задан — пускаем всех (для теста)
    return update.effective_user.id == OWNER_ID

# Твой список избранных монет
FAVE_COINS = ["ETH","SOL","XRP","ADA","LTC","LINK","ATOM","BCH","NEAR","ICP","TON","HYPE"]

ALL_COINS = {
    "🔥 Топ": ["BTC","ETH","XRP","BNB","SOL","ADA","LTC","BCH","TON","AVAX","DOT","TRX","DOGE","SHIB","UNI"],
    "⭐ Мой список": FAVE_COINS,
    "⚡ Альткоины": ["LINK","ATOM","NEAR","ICP","HYPE","SUI","ARB","OP","INJ","SEI","ENA","FET","RENDER","WLD","TAO"],
    "🆕 Новинки": ["BERA","LAYER","MOVE","IP","VIRTUAL","AIXBT","SONIC","PENGU","TRUMP","BONK","FLOKI"],
}

INTERVALS = {
    "15м": "15",
    "1ч":  "60",
    "4ч":  "240",
    "1д":  "D",
    "1н":  "W",
}

INTERVAL_DESC = {
    "15": "⚡ 15 минут — скальпинг (сделка на 1-4 часа)",
    "60": "🕐 1 час — дневная торговля (сделка на 1-3 дня)",
    "240": "🕓 4 часа — среднесрочно (сделка на 3-14 дней)",
    "D":  "📅 1 день — долгосрочно (держать недели/месяцы)",
    "W":  "📆 1 неделя — инвестиция (держать месяцы/год)",
}

INTERVAL_ADVICE = {
    "15": "Это быстрая торговля. Следи за сделкой — она может закрыться за пару часов. Подходит если сидишь у экрана.",
    "60": "Хороший баланс — не надо смотреть каждую минуту. Проверяй раз в несколько часов.",
    "240": "Спокойная торговля. Открыл сделку и проверяешь раз в день. Меньше стресса.",
    "D":  "Долгосрочная позиция. Купил и ждёшь несколько недель. Стоп-лосс широкий — небольшие колебания не страшны.",
    "W":  "Инвестиция на месяцы. Купил и забыл на время. Подходит если веришь в рост монеты.",
}

# ── ПОЛУЧЕНИЕ ДАННЫХ С СЕРВЕРА ───────────────────────────────────────────────
async def get_analysis(symbol: str, interval: str = "60") -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{API_URL}/analyze/{symbol}?interval={interval}")
            return r.json()
    except Exception as e:
        print(f"API error: {e}")
        return None

async def get_multi_analysis(symbol: str) -> dict:
    """Получает анализ по всем ключевым таймфреймам параллельно"""
    timeframes = ["15", "60", "240", "D"]
    async def fetch(iv):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(f"{API_URL}/analyze/{symbol}?interval={iv}")
                return iv, r.json()
        except:
            return iv, None
    results = await asyncio.gather(*[fetch(iv) for iv in timeframes])
    return {iv: data for iv, data in results if data and "signal" in data}

async def get_history(symbol: str) -> dict | None:
    """Получает исторические данные: динамика, тренды, максимумы"""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(f"{API_URL}/history/{symbol}")
            return r.json()
    except Exception as e:
        print(f"History error: {e}")
        return None

async def get_prices(symbols: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_URL}/prices?symbols={','.join(symbols)}")
            return r.json()
    except:
        return {}

# ── ФОРМАТИРОВАНИЕ СООБЩЕНИЙ ─────────────────────────────────────────────────
def signal_emoji(signal: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")

def change_emoji(change: float) -> str:
    return "📈" if change >= 0 else "📉"

def format_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.5f}"

def format_history(symbol: str, h: dict) -> str:
    if not h:
        return ""

    lines = []
    pc = h.get("price_changes", {})
    trends = h.get("trends", {})

    # Динамика цены
    def fmt_pct(val):
        if val is None: return "нет данных"
        icon = "📈" if val > 0 else ("📉" if val < 0 else "➡️")
        return f"{icon} {val:+.2f}%"

    lines.append("📊 *Динамика цены:*")
    lines.append(f"• За 24 часа:   {fmt_pct(pc.get('24h'))}")
    lines.append(f"• За 7 дней:    {fmt_pct(pc.get('7d'))}")
    lines.append(f"• За 30 дней:   {fmt_pct(pc.get('30d'))}")
    lines.append(f"• За 3 месяца:  {fmt_pct(pc.get('90d'))}")
    lines.append(f"• За год:       {fmt_pct(pc.get('365d'))}")
    lines.append("")

    # Тренды
    lines.append("🔍 *Тренды:*")
    if trends.get("3d"):  lines.append(f"• 3 дня:    {trends['3d']}")
    if trends.get("7d"):  lines.append(f"• 7 дней:   {trends['7d']}")
    if trends.get("30d"): lines.append(f"• 30 дней:  {trends['30d']}")
    if trends.get("90d"): lines.append(f"• 90 дней:  {trends['90d']}")
    lines.append("")

    # Серия дней
    if h.get("streak"):
        lines.append(f"⏱ *Серия:* {h['streak']}")

    # Объём
    if h.get("volume_signal"):
        lines.append(f"📦 *Объём:* {h['volume_signal']}")
    lines.append("")

    # Максимум/минимум за год
    yh = h.get("year_high")
    yl = h.get("year_low")
    fh = h.get("from_high_pct")
    fl = h.get("from_low_pct")
    if yh and yl:
        lines.append("📅 *За последний год:*")
        lines.append(f"• Максимум: *{format_price(yh)}*  (сейчас {fh:+.1f}% от пика)")
        lines.append(f"• Минимум:  *{format_price(yl)}*  (сейчас {fl:+.1f}% от дна)")

    return "\n".join(lines)


def signal_short(sig: str, conf: int) -> str:
    icons = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
    labels = {"BUY": "ПОКУПАТЬ", "SELL": "ПРОДАВАТЬ", "HOLD": "ЖДАТЬ"}
    return f"{icons.get(sig,'⚪')} {labels.get(sig,'?')} ({conf}%)"

def format_multi_analysis(symbol: str, all_data: dict, budget_usd: float = 22.0) -> str:
    usd_to_rub = 90

    if not all_data:
        return f"⚠️ Не удалось получить данные по {symbol}"

    first = next(iter(all_data.values()))
    price = first["price"]
    chg   = first["change_24h"]
    ce    = change_emoji(chg)

    lines_out = [
        f"🔮 *LUNA — Полный анализ {symbol}/USDT*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 Цена сейчас: *{format_price(price)}*  {ce} {chg:+.2f}% за сутки",
        "",
        "━━━ Взгляд с разных периодов ━━━",
        "",
        "_Каждый период — это отдельный взгляд на монету._",
        "_15 минут — для быстрой сделки на пару часов._",
        "_1 час — для сделки на 1–3 дня._",
        "_4 часа — для сделки на неделю._",
        "_1 день — для долгосрочного вложения на месяц+._",
        "",
    ]

    tf_meta = {
        "15":  ("⚡ 15 минут", "быстрая сделка, 1–4 часа"),
        "60":  ("🕐 1 час",    "дневная торговля, 1–3 дня"),
        "240": ("🕓 4 часа",   "среднесрочно, 1–2 недели"),
        "D":   ("📅 1 день",   "долгосрочно, месяц+"),
    }

    buy_count = sell_count = hold_count = 0

    for iv in ["15", "60", "240", "D"]:
        if iv not in all_data:
            continue
        d    = all_data[iv]
        sig  = d["signal"]["signal"]
        conf = d["signal"]["confidence"]
        rsi  = d["indicators"]["rsi"]
        macd = d["indicators"]["macd"]["cross"]
        ema50  = d["indicators"]["ema50"]
        ema200 = d["indicators"]["ema200"]
        name, use = tf_meta.get(iv, (iv, ""))

        # Сигнал
        sig_icons  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
        sig_labels = {"BUY": "ПОКУПАТЬ", "SELL": "НЕ ПОКУПАТЬ", "HOLD": "ПОДОЖДАТЬ"}
        sig_icon  = sig_icons.get(sig, "⚪")
        sig_label = sig_labels.get(sig, "?")

        # RSI коротко
        if rsi < 30:   rsi_short = f"RSI {rsi} — очень дёшево 🔵"
        elif rsi < 45: rsi_short = f"RSI {rsi} — немного дёшево 🟡"
        elif rsi < 55: rsi_short = f"RSI {rsi} — в норме ⚪"
        elif rsi < 70: rsi_short = f"RSI {rsi} — немного дорого 🟠"
        else:          rsi_short = f"RSI {rsi} — перегрета 🔴"

        # Тренд коротко
        if price > ema50 > ema200:    trend_short = "тренд вверх 📈"
        elif price < ema50 < ema200:  trend_short = "тренд вниз 📉"
        elif price > ema50:           trend_short = "слабый рост ↗️"
        else:                         trend_short = "слабое падение ↘️"

        macd_short = "импульс вверх 🟢" if macd == "bullish" else "импульс вниз 🔴"

        # MA30/90 коротко
        sma30_v = d["indicators"].get("sma30")
        sma90_v = d["indicators"].get("sma90")
        ma_sig  = d["indicators"].get("ma_signal", "neutral")
        ma_cr   = d["indicators"].get("ma_cross")
        ma_ago2 = d["indicators"].get("ma_cross_candles_ago")
        ma_dst  = d["indicators"].get("ma_cross_distance")
        if sma30_v and sma90_v:
            ago2_map = {1: " (1 св. назад)", 2: " (2 св. назад)", 3: " (3 св. назад)"}
            ago2_str = ago2_map.get(ma_ago2, "") if ma_ago2 else ""
            if ma_cr == "golden":
                ma_short = f"MA: 🌟 ЗОЛОТОЙ КРЕСТ{ago2_str} — сигнал роста!"
            elif ma_cr == "death":
                ma_short = f"MA: 💀 КРЕСТ СМЕРТИ{ago2_str} — сигнал падения!"
            elif ma_sig == "bullish":
                ma_short = f"MA30 > MA90 — тренд вверх 🟢"
            elif ma_sig == "bearish":
                ma_short = f"MA30 < MA90 — тренд вниз 🔴"
            else:
                ma_short = f"MA: боковик ⚪"
        else:
            ma_short = None

        lines_out.append(f"*{name}* ({use})")
        lines_out.append(f"  {sig_icon} *{sig_label}* — уверенность {conf}%")
        lines_out.append(f"  • {rsi_short}")
        lines_out.append(f"  • MACD: {macd_short}")
        lines_out.append(f"  • {trend_short}")
        if ma_short:
            lines_out.append(f"  • {ma_short}")

        # Дивергенция в мульти-анализе
        div_d = d["indicators"].get("divergence", {})
        rdiv  = div_d.get("rsi")
        mdiv  = div_d.get("macd")
        if rdiv == "bullish" or mdiv == "bullish":
            lines_out.append(f"  • 🔔 Бычья дивергенция — сигнал разворота вверх!")
        elif rdiv == "bearish" or mdiv == "bearish":
            lines_out.append(f"  • 🔔 Медвежья дивергенция — сигнал разворота вниз!")
        elif rdiv in ("hidden_bullish",) or mdiv in ("hidden_bullish",):
            lines_out.append(f"  • 🔍 Скрытая бычья — продолжение роста")
        elif rdiv in ("hidden_bearish",) or mdiv in ("hidden_bearish",):
            lines_out.append(f"  • 🔍 Скрытая медвежья — продолжение падения")
        lines_out.append("")

        if sig == "BUY":   buy_count  += 1
        elif sig == "SELL": sell_count += 1
        else:               hold_count += 1

    # Общий вывод
    total = buy_count + sell_count + hold_count
    lines_out.append("━━━ Общий вывод ━━━")
    lines_out.append("")
    if buy_count >= 3:
        lines_out.append(f"🟢 *СИЛЬНЫЙ СИГНАЛ К ПОКУПКЕ*")
        lines_out.append(f"_{buy_count} из {total} периодов говорят покупать — хорошее совпадение_")
    elif buy_count == 2:
        lines_out.append(f"🟢 *МОЖНО РАССМОТРЕТЬ ПОКУПКУ*")
        lines_out.append(f"_Половина периодов ({buy_count}/{total}) за покупку — умеренный сигнал_")
    elif sell_count >= 3:
        lines_out.append(f"🔴 *НЕ ПОКУПАТЬ СЕЙЧАС*")
        lines_out.append(f"_{sell_count} из {total} периодов против — высокий риск потери_")
    elif sell_count == 2:
        lines_out.append(f"🟡 *ЛУЧШЕ ПОДОЖДАТЬ*")
        lines_out.append(f"_Сигналы смешанные — нет уверенности в направлении_")
    else:
        lines_out.append(f"🟡 *НЕЙТРАЛЬНО — НАБЛЮДАТЬ*")
        lines_out.append(f"_Рынок в нерешительности. Подожди чёткого сигнала_")
    lines_out.append("")

    # Детальный план по 1ч
    if "60" in all_data:
        d1h = all_data["60"]
        trd = d1h["trade"]
        lvl = d1h["levels"]
        sig1h = d1h["signal"]["signal"]
        entry = trd["entry"]

        if sig1h == "BUY":
            sl  = entry * 0.97   # стоп-лосс на 3% ниже входа
            tp1 = entry * 1.03   # цель 1 на 3% выше
            tp2 = entry * 1.06   # цель 2 на 6% выше
        elif sig1h == "SELL":
            sl  = entry * 1.03   # стоп-лосс на 3% выше (шорт)
            tp1 = entry * 0.97   # цель 1 на 3% ниже
            tp2 = entry * 0.94   # цель 2 на 6% ниже
        else:  # HOLD
            sl  = entry * 0.96   # широкий стоп-лосс 4% ниже
            tp1 = entry * 1.03   # цель 1 на 3% выше
            tp2 = entry * 1.06   # цель 2 на 6% выше

        sl_pct  = round((sl  - entry) / entry * 100, 1)
        tp1_pct = round((tp1 - entry) / entry * 100, 1)
        tp2_pct = round((tp2 - entry) / entry * 100, 1)

        invest  = round(min(budget_usd * 0.7, budget_usd - 5), 0)
        reserve = budget_usd - invest
        qty     = invest / price if price > 0 else 0
        tp1_usd = qty * (tp1 - entry)
        tp2_usd = qty * (tp2 - entry)
        sl_usd  = qty * (sl  - entry)

        lines_out += [
            "━━━ План сделки (на основе 1 часа) ━━━",
            "",
            f"▶️ Купить по: *{format_price(entry)}*",
            "",
            f"🛡 Стоп-лосс: *{format_price(sl)}* ({sl_pct:+.1f}%)",
            "_Если цена упадёт сюда — продай. Это защита от большого убытка_",
            "",
            f"🎯 Цель 1: *{format_price(tp1)}* ({tp1_pct:+.1f}%)",
            "_Дойдёт сюда — продай половину, зафикси первую прибыль_",
            "",
            f"🎯 Цель 2: *{format_price(tp2)}* ({tp2_pct:+.1f}%)",
            "_Если рост продолжится — продай остаток здесь_",
            "",
            "━━━ Считаем для твоего бюджета ━━━",
            "",
            f"💵 Вкладываешь: *${invest:.0f}* из ${budget_usd:.0f}",
            f"🏦 Резерв: *${reserve:.0f}* держи в USDT — не трогай",
            f"🪙 Купишь: *{qty:.4f} {symbol}*",
            "",
            f"При Цели 1 заработаешь: *+${tp1_usd:.2f}* (≈ +{tp1_usd*usd_to_rub:.0f} ₽)",
            f"При Цели 2 заработаешь: *+${tp2_usd:.2f}* (≈ +{tp2_usd*usd_to_rub:.0f} ₽)",
            f"Если сработает стоп-лосс: *{sl_usd:.2f}$* (≈ {sl_usd*usd_to_rub:.0f} ₽)",
            "",
        ]

        # Уровни
        sup = lvl.get("support", [])
        res = lvl.get("resistance", [])
        if sup or res:
            lines_out.append("━━━ Ключевые уровни цены ━━━")
            lines_out.append("")
            if sup:
                lines_out.append(f"🟢 *Поддержка: {' / '.join([format_price(s) for s in sup])}*")
                lines_out.append("_Здесь цена часто останавливается и растёт обратно_")
            if res:
                lines_out.append(f"🔴 *Сопротивление: {' / '.join([format_price(r) for r in res])}*")
                lines_out.append("_Здесь цена часто тормозит — зона активных продаж_")
            lines_out.append("")

    lines_out.append("⚠️ _Это анализ, не финансовый совет. Крипто — высокий риск._")
    return "\n".join(lines_out)

def format_analysis(data: dict, budget_usd: float = 22.0) -> str:
    sym   = data["symbol"]
    pair  = data["pair"]
    price = data["price"]
    chg   = data["change_24h"]
    ind   = data["indicators"]
    sig   = data["signal"]
    trd   = data["trade"]
    lvl   = data["levels"]
    ce    = change_emoji(chg)
    usd_to_rub = 90

    # ── ГЛАВНЫЙ ВЫВОД ────────────────────────────────────────
    if sig["signal"] == "BUY":
        verdict      = "🟢 МОЖНО ПОКУПАТЬ"
        verdict_why  = "Большинство индикаторов показывают хороший момент для входа"
    elif sig["signal"] == "SELL":
        verdict      = "🔴 НЕ ПОКУПАТЬ"
        verdict_why  = "Сейчас плохой момент — цена скорее всего продолжит падать"
    else:
        verdict      = "🟡 ПОДОЖДАТЬ"
        verdict_why  = "Нет чёткого сигнала — рынок в нерешительности, лучше понаблюдать"

    conf = sig["confidence"]
    if conf >= 75:
        conf_txt = f"Уверенность: {conf}% — сигнал сильный"
    elif conf >= 55:
        conf_txt = f"Уверенность: {conf}% — сигнал умеренный"
    else:
        conf_txt = f"Уверенность: {conf}% — сигнал слабый, осторожно"

    # ── ТЕРМОМЕТР (RSI) ────────────────────────────────────────
    rsi = ind["rsi"]
    if rsi < 30:
        rsi_verdict = "🔵 Очень дёшево"
        rsi_action  = "Монета сильно упала — исторически хороший момент для покупки"
    elif rsi < 45:
        rsi_verdict = "🟡 Немного дёшево"
        rsi_action  = "Цена чуть ниже нормы — неплохой момент для входа"
    elif rsi < 55:
        rsi_verdict = "⚪ В норме"
        rsi_action  = "Цена в нейтральной зоне, без перегрева"
    elif rsi < 70:
        rsi_verdict = "🟠 Немного дорого"
        rsi_action  = "Монета немного выросла — возможна небольшая коррекция"
    else:
        rsi_verdict = "🔴 Перегрета"
        rsi_action  = "Монета сильно выросла — высокий риск отката вниз"

    # ── НАПРАВЛЕНИЕ ДВИЖЕНИЯ (MACD) ──────────────────────────
    macd = ind["macd"]
    if macd["cross"] == "bullish":
        macd_txt = "🟢 Импульс вверх — скорость роста ускоряется"
    else:
        macd_txt = "🔴 Импульс вниз — скорость падения ускоряется"

    # ── СКОЛЬЗЯЩИЕ СРЕДНИЕ MA30 / MA90 ───────────────────────
    sma30 = ind.get("sma30")
    sma90 = ind.get("sma90")
    ma_signal = ind.get("ma_signal", "neutral")
    ma_cross  = ind.get("ma_cross")

    if sma30 and sma90:
        ma_dist = ind.get("ma_cross_distance")
        ma_ago  = ind.get("ma_cross_candles_ago")

        ago_map = {1: "только что (1 свеча назад)", 2: "2 свечи назад", 3: "3 свечи назад"}
        ago_str = ago_map.get(ma_ago, "") if ma_ago else ""
        dist_str = f"{abs(ma_dist):.2f}%" if ma_dist is not None else ""

        if ma_cross == "golden":
            ma_cross_txt = (
                f"🌟 ЗОЛОТОЙ КРЕСТ — {ago_str}\n"
                f"MA30 пересекла MA90 снизу вверх!\n"
                f"Сигнал начала роста. Разрыв: +{dist_str}\n"
                f"Хороший момент для покупки — действуй!"
            )
        elif ma_cross == "death":
            ma_cross_txt = (
                f"💀 КРЕСТ СМЕРТИ — {ago_str}\n"
                f"MA30 пересекла MA90 сверху вниз!\n"
                f"Сигнал начала падения. Разрыв: -{dist_str}\n"
                f"Не покупай сейчас — дождись восстановления."
            )
        else:
            ma_cross_txt = None

        if ma_signal == "bullish":
            ma_trend = f"🟢 MA30 ({format_price(sma30)}) выше MA90 ({format_price(sma90)}) — среднесрочный тренд вверх"
        elif ma_signal == "bearish":
            ma_trend = f"🔴 MA30 ({format_price(sma30)}) ниже MA90 ({format_price(sma90)}) — среднесрочный тренд вниз"
        else:
            ma_trend = f"⚪ MA30 ({format_price(sma30)}) и MA90 ({format_price(sma90)}) — боковое движение"

        if price > sma30 > sma90:
            ma_position = "Цена выше обеих линий — уверенный рост"
        elif price < sma30 < sma90:
            ma_position = "Цена ниже обеих линий — уверенное падение"
        elif price > sma30:
            ma_position = "Цена выше быстрой MA30 — краткосрочный рост"
        else:
            ma_position = "Цена ниже быстрой MA30 — краткосрочное давление"
    else:
        ma_trend    = f"🟡 MA30 ({format_price(sma30) if sma30 else '?'}) — недостаточно данных для MA90"
        ma_cross_txt = None
        ma_position  = ""

    # ── ДИВЕРГЕНЦИЯ ──────────────────────────────────────────
    div      = ind.get("divergence", {})
    rsi_div  = div.get("rsi")
    macd_div = div.get("macd")

    div_lines = []
    if rsi_div == "bullish":
        div_lines.append("🔔 *RSI: Бычья дивергенция!*")
        div_lines.append("_Цена падала, RSI рос — продавцы слабеют, возможен разворот ВВЕРХ_")
    elif rsi_div == "bearish":
        div_lines.append("🔔 *RSI: Медвежья дивергенция!*")
        div_lines.append("_Цена росла, RSI падал — покупатели слабеют, возможен разворот ВНИЗ_")
    elif rsi_div == "hidden_bullish":
        div_lines.append("🔍 *RSI: Скрытая бычья дивергенция*")
        div_lines.append("_Сигнал продолжения роста — тренд вверх сохраняется_")
    elif rsi_div == "hidden_bearish":
        div_lines.append("🔍 *RSI: Скрытая медвежья дивергенция*")
        div_lines.append("_Сигнал продолжения падения — тренд вниз сохраняется_")

    if macd_div == "bullish":
        div_lines.append("🔔 *MACD: Бычья дивергенция!*")
        div_lines.append("_Импульс падения слабеет — разворот вверх близко_")
    elif macd_div == "bearish":
        div_lines.append("🔔 *MACD: Медвежья дивергенция!*")
        div_lines.append("_Импульс роста слабеет — разворот вниз близко_")

    if rsi_div and macd_div and rsi_div[:4] == macd_div[:4]:
        div_lines.append("⚡ *Оба индикатора подтверждают — сигнал очень сильный!*")

    div_block = "\n".join(div_lines) if div_lines else None

    # ── ТРЕНД (EMA) ───────────────────────────────────────────
    ema50, ema200 = ind["ema50"], ind["ema200"]
    if price > ema50 > ema200:
        trend_verdict = "📈 Устойчивый рост"
        trend_explain = f"Цена ${format_price(price)} выше средней за 2 дня (${format_price(ema50)}) и за 8 дней (${format_price(ema200)}) — монета уверенно растёт"
    elif price < ema50 < ema200:
        trend_verdict = "📉 Устойчивое падение"
        trend_explain = f"Цена ${format_price(price)} ниже средней за 2 дня (${format_price(ema50)}) и за 8 дней (${format_price(ema200)}) — монета уверенно падает"
    elif price > ema50:
        trend_verdict = "↗️ Слабый рост"
        trend_explain = f"Цена выше средней за 2 дня, но ниже за 8 дней — краткосрочный подъём, тренд неустойчив"
    else:
        trend_verdict = "↘️ Слабое падение"
        trend_explain = f"Цена ниже средней за 2 дня — небольшая коррекция"

    # ── ДИАПАЗОН (BOLLINGER) ───────────────────────────────────
    bb = ind["bollinger"]
    bb_pos = round(bb["position"] * 100)
    if bb_pos < 20:
        bb_txt = f"📍 Цена у нижней границы нормального диапазона ({bb_pos}%) — часто означает отскок вверх"
    elif bb_pos > 80:
        bb_txt = f"📍 Цена у верхней границы нормального диапазона ({bb_pos}%) — часто означает коррекцию вниз"
    else:
        bb_txt = f"📍 Цена в середине нормального диапазона ({bb_pos}%) — нейтрально"

    # ── УРОВНИ ───────────────────────────────────────────────
    sup = lvl.get("support", [])
    res = lvl.get("resistance", [])
    sup_txt = " и ".join([format_price(s) for s in sup]) if sup else "не найдена"
    res_txt = " и ".join([format_price(r) for r in res]) if res else "не найдено"

    # ── ПЛАН СДЕЛКИ (с исправлением направления) ─────────────
    entry = trd["entry"]
    if sig["signal"] == "BUY":
        sl  = entry * 0.97   # стоп-лосс на 3% ниже входа
        tp1 = entry * 1.03   # цель 1 на 3% выше
        tp2 = entry * 1.06   # цель 2 на 6% выше
    elif sig["signal"] == "SELL":
        sl  = entry * 1.03   # стоп-лосс на 3% выше (шорт)
        tp1 = entry * 0.97   # цель 1 на 3% ниже
        tp2 = entry * 0.94   # цель 2 на 6% ниже
    else:  # HOLD
        sl  = entry * 0.96   # широкий стоп-лосс 4% ниже
        tp1 = entry * 1.03   # цель 1 на 3% выше
        tp2 = entry * 1.06   # цель 2 на 6% выше

    sl_pct  = round((sl  - entry) / entry * 100, 1)
    tp1_pct = round((tp1 - entry) / entry * 100, 1)
    tp2_pct = round((tp2 - entry) / entry * 100, 1)

    # ── ДЕНЬГИ ────────────────────────────────────────────────
    invest  = round(min(budget_usd * 0.7, budget_usd - 5), 0)
    reserve = budget_usd - invest
    qty     = invest / price if price > 0 else 0
    tp1_usd = qty * (tp1 - entry)
    tp2_usd = qty * (tp2 - entry)
    sl_usd  = qty * (sl  - entry)

    parts = [
        f"🔮 *LUNA — {pair}*\n",
        f"━━━━━━━━━━━━━━━━━━━━\n",
        f"💰 Цена сейчас: *{format_price(price)}*  {ce} {chg:+.2f}% за сутки\n\n",
        f"*{verdict}*\n",
        f"_{verdict_why}_\n",
        f"_{conf_txt}_\n\n",
        f"━━━ Что говорят индикаторы ━━━\n\n",
        f"🌡 *Термометр монеты (RSI = {rsi})*\n",
        f"{rsi_verdict}\n",
        f"_{rsi_action}_\n\n",
        f"🧭 *Направление движения (MACD)*\n",
        f"{macd_txt}\n\n",
        f"📉 *Скользящие средние (MA30 / MA90)*\n",
        f"{ma_trend}\n",
        f"_{ma_position}_\n",
    ]
    if ma_cross_txt:
        parts.append(f"{ma_cross_txt}\n")
    parts.append("\n")
    if div_block:
        parts.append(f"🔔 *Дивергенция:*\n{div_block}\n\n")
    parts += [
        f"📏 *Тренд (средние цены)*\n",
        f"{trend_verdict}\n",
        f"_{trend_explain}_\n\n",
        f"📐 *Нормальный диапазон цены (Bollinger)*\n",
        f"Верхняя граница: {format_price(bb['upper'])} | Нижняя: {format_price(bb['lower'])}\n",
        f"{bb_txt}\n\n",
        f"━━━ Ключевые уровни цены ━━━\n\n",
        f"🟢 *Поддержка: {sup_txt}*\n",
        f"_Здесь цена много раз останавливалась и отскакивала вверх — зона интереса покупателей_\n\n",
        f"🔴 *Сопротивление: {res_txt}*\n",
        f"_Здесь цена много раз тормозила — зона где продавцы активны_\n\n",
        f"━━━ План сделки ━━━\n\n",
        f"▶️ Купить по: *{format_price(entry)}*\n\n",
        f"🛡 Стоп-лосс: *{format_price(sl)}* ({sl_pct:+.1f}%)\n",
        f"_Если цена упадёт до этой отметки — продай. Это защита от большого убытка_\n\n",
        f"🎯 Цель 1: *{format_price(tp1)}* ({tp1_pct:+.1f}%)\n",
        f"_Когда цена дойдёт сюда — продай половину и зафикси первую прибыль_\n\n",
        f"🎯 Цель 2: *{format_price(tp2)}* ({tp2_pct:+.1f}%)\n",
        f"_Если рост продолжится — продай остаток здесь_\n\n",
        f"━━━ Считаем для твоего бюджета ━━━\n\n",
        f"💵 Вкладываешь: *${invest:.0f}* из ${budget_usd:.0f}\n",
        f"🏦 Резерв: *${reserve:.0f}* держи в USDT на бирже — не трогай\n",
        f"🪙 Купишь: *{qty:.4f} {sym}*\n\n",
        f"При Цели 1 заработаешь: *+${tp1_usd:.2f}* (≈ +{tp1_usd*usd_to_rub:.0f} ₽)\n",
        f"При Цели 2 заработаешь: *+${tp2_usd:.2f}* (≈ +{tp2_usd*usd_to_rub:.0f} ₽)\n",
        f"Если сработает стоп-лосс: *{sl_usd:.2f}$* (≈ {sl_usd*usd_to_rub:.0f} ₽)\n\n",
        f"⚠️ _Это анализ, не финансовый совет. Крипто — высокий риск._",
    ]
    msg = "".join(parts)
    return msg

def format_prices_msg(prices: dict) -> str:
    lines = ["💹 *Текущие цены (Bybit)*\n"]
    for ticker, info in prices.items():
        emoji = "📈" if info["change_24h"] >= 0 else "📉"
        lines.append(f"{emoji} *{ticker}*: {format_price(info['price'])}  {info['change_24h']:+.2f}%")
    lines.append("\n_Данные с Bybit через luna-crypto-api_")
    return "\n".join(lines)


def get_uid(update_or_query) -> int:
    """Получить user_id из update или query"""
    try:
        if hasattr(update_or_query, 'effective_user') and update_or_query.effective_user:
            return update_or_query.effective_user.id
        if hasattr(update_or_query, 'from_user') and update_or_query.from_user:
            return update_or_query.from_user.id
    except:
        pass
    return 0

# ── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────────
def main_keyboard(user_id: int = 0):
    from telegram import WebAppInfo
    dashboard_url = "https://olgapshedromirskaya-sys.github.io/luna-crypto-api/dashboard.html"
    rows = []
    # Кнопка дашборда — только для владельца
    if user_id == OWNER_ID:
        rows.append([InlineKeyboardButton("📊 Открыть дашборд", web_app=WebAppInfo(url=dashboard_url))])
    rows += [
        [InlineKeyboardButton("⭐ Мой список монет", callback_data="group_fave")],
        [InlineKeyboardButton("🔥 Топ монеты",       callback_data="group_top"),
         InlineKeyboardButton("⚡ Альткоины",        callback_data="group_alt")],
        [InlineKeyboardButton("🆕 Новинки",          callback_data="group_new")],
        [InlineKeyboardButton("💹 Цены топ-5",       callback_data="prices_top"),
         InlineKeyboardButton("💵 Мой бюджет",       callback_data="budget_menu")],
        [InlineKeyboardButton("💼 Мой портфель",     callback_data="portfolio_main")],
        [InlineKeyboardButton("❓ Помощь",            callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)

def budget_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("$10",  callback_data="budget_set_10"),
         InlineKeyboardButton("$22",  callback_data="budget_set_22"),
         InlineKeyboardButton("$50",  callback_data="budget_set_50")],
        [InlineKeyboardButton("$100", callback_data="budget_set_100"),
         InlineKeyboardButton("$200", callback_data="budget_set_200"),
         InlineKeyboardButton("$500", callback_data="budget_set_500")],
        [InlineKeyboardButton("✏️ Ввести вручную", callback_data="budget_custom")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="back_main")],
    ])

def coin_keyboard(group_key: str):
    coins = ALL_COINS.get(group_key, FAVE_COINS)
    rows = []
    row = []
    for i, coin in enumerate(coins):
        row.append(InlineKeyboardButton(coin, callback_data=f"coin_{coin}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def interval_keyboard(coin: str):
    row = [InlineKeyboardButton(label, callback_data=f"analyze_{coin}_{iv}")
           for label, iv in INTERVALS.items()]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Все таймфреймы сразу", callback_data=f"multianalyze_{coin}")],
        row,
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
    ])

def after_analysis_keyboard(coin: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Все таймфреймы + история", callback_data=f"multianalyze_{coin}")],
        [InlineKeyboardButton("⚡ 15м", callback_data=f"analyze_{coin}_15"),
         InlineKeyboardButton("🕐 1ч",  callback_data=f"analyze_{coin}_60"),
         InlineKeyboardButton("🕓 4ч",  callback_data=f"analyze_{coin}_240"),
         InlineKeyboardButton("📅 1д",  callback_data=f"analyze_{coin}_D")],
        [InlineKeyboardButton("🏦 Фундаментал", callback_data=f"fundamental_{coin}")],
        [InlineKeyboardButton("📋 Другая монета", callback_data="back_main"),
         InlineKeyboardButton("💹 Все цены",      callback_data="prices_fave")],
    ])


# ── ХЭНДЛЕРЫ ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Доступ закрыт.")
        return
    name = update.effective_user.first_name or "трейдер"
    budget = get_budget(update.effective_user.id)
    text = (
        f"🔮 *Привет, {name}!*\n\n"
        f"Я *LUNA* — твой персональный аналитик крипторынка на Bybit.\n\n"
        f"Получаю *реальные данные* прямо с биржи и считаю:\n"
        f"📊 RSI, MACD, EMA50/200, Bollinger Bands\n"
        f"🎯 Точку входа, стоп-лосс и тейк-профит\n"
        f"💰 Прибыль под твой бюджет в рублях\n\n"
        f"💵 Текущий бюджет: *${budget:.0f}*\n\n"
        f"Выбери монету для анализа 👇"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    text = """📖 *Команды LUNA:*

/start — главное меню
/analyze ETH — быстрый анализ монеты
/prices — текущие цены
/fave — анализ твоих монет
/help — помощь

*Таймфреймы:*
• 15м — скальпинг (быстрые сделки)
• 1ч — дневная торговля  
• 4ч — среднесрочно
• 1д — долгосрочные инвестиции

*Сигналы:*
🟢 ПОКУПАТЬ — хорошее время для входа
🔴 ПРОДАВАТЬ — стоит зафиксировать прибыль
🟡 ДЕРЖАТЬ — ждать лучшей точки входа"""
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0))

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    args = ctx.args
    if not args:
        await update.message.reply_text("Укажи монету. Пример: /analyze ETH")
        return
    coin = args[0].upper()
    interval = args[1] if len(args) > 1 else "60"
    await do_analysis(update, ctx, coin, interval)

async def cmd_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Загружаю цены с Bybit...")
    prices = await get_prices(["BTC","ETH","SOL","XRP","BNB","ADA","TON","DOGE"])
    if prices:
        await msg.edit_text(format_prices_msg(prices), parse_mode="Markdown")
    else:
        await msg.edit_text("⚠️ Не удалось получить цены. Попробуй позже.")

async def cmd_fave(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Загружаю цены твоих монет...")
    prices = await get_prices(FAVE_COINS[:8])
    if prices:
        await msg.edit_text(
            format_prices_msg(prices),
            parse_mode="Markdown",
            reply_markup=coin_keyboard("⭐ Мой список")
        )
    else:
        await msg.edit_text("⚠️ Ошибка загрузки.", reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0))

async def do_multi_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE, coin: str):
    """Анализ по всем таймфреймам сразу"""
    if not is_allowed(update):
        return
    uid = update.effective_user.id
    budget = get_budget(uid)

    if update.message:
        msg = await update.message.reply_text(f"⏳ Анализирую {coin}/USDT по всем таймфреймам...")
    else:
        msg = await update.callback_query.message.reply_text(f"⏳ Анализирую {coin}/USDT по всем таймфреймам...")

    all_data, hist = await asyncio.gather(
        get_multi_analysis(coin),
        get_history(coin)
    )

    if not all_data:
        await msg.edit_text(f"⚠️ Не удалось получить данные по {coin}.")
        return

    main_text = format_multi_analysis(coin, all_data, budget_usd=budget)
    hist_text = format_history(coin, hist) if hist else ""

    # Вставляем историю между сигналами и планом сделки
    if hist_text:
        # Найдём место после сводной таблицы сигналов
        split_marker = "🎯 *План сделки"
        if split_marker in main_text:
            parts = main_text.split(split_marker, 1)
            text = parts[0] + hist_text + "\n\n" + split_marker + parts[1]
        else:
            text = main_text + "\n\n" + hist_text
    else:
        text = main_text

    await msg.edit_text(text, parse_mode="Markdown", reply_markup=after_analysis_keyboard(coin))


async def do_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE, coin: str, interval: str):
    if not is_allowed(update):
        return
    uid = update.effective_user.id
    budget = get_budget(uid)

    if update.message:
        msg = await update.message.reply_text(f"⏳ Анализирую {coin}/USDT с Bybit...")
    else:
        msg = await update.callback_query.message.reply_text(f"⏳ Анализирую {coin}/USDT с Bybit...")

    data = await get_analysis(coin, interval)

    if not data or "signal" not in data:
        await msg.edit_text(f"⚠️ Не удалось получить данные по {coin}. Проверь тикер или попробуй позже.")
        return

    iv_label = {v: k for k, v in INTERVALS.items()}.get(interval, interval)
    iv_label = {v: k for k, v in INTERVALS.items()}.get(interval, interval)
    header = f"АНАЛИЗ {coin} | {iv_label} | Bybit | Бюджет: ${budget:.0f}\n\n"
    text = header + format_analysis(data, budget_usd=budget)

    await msg.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=after_analysis_keyboard(coin)
    )


async def do_fundamental(coin: str, msg, query=None):
    """Загружает и форматирует полный фундаментальный анализ"""
    await msg.edit_text(f"🏦 Загружаю фундаментальный анализ {coin}...")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{API_URL}/fundamental/{coin}")
            d = r.json()
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка загрузки: {e}")
        return

    fg      = d.get("fear_greed")
    cg      = d.get("coingecko")
    news    = d.get("news", [])
    events  = d.get("events", [])
    verdict = d.get("fund_verdict", "НЕЙТРАЛЬНО")
    ev      = d.get("fund_emoji", "🟡")
    signals = d.get("fund_signals", [])
    score   = d.get("fund_score", 0)

    out = []
    out.append(f"🏦 *Фундаментальный анализ {coin}/USDT*")
    out.append("")
    out.append(f"━━━ Общий сигнал ━━━")
    out.append(f"{ev} *{verdict}* (балл: {score:+d})")
    for s in signals:
        out.append(f"• {s}")
    out.append("")

    # Fear & Greed
    if fg:
        zone  = fg.get("zone", "")
        out.append("━━━ 😱 Страх и Жадность (Fear & Greed) ━━━")
        out.append(f"{fg['emoji']} *{fg['value']}/100* — Зона {zone}")
        out.append(f"_{fg['ru']}_")
        out.append("")
        out.append("_Шкала: 0–20 😱Экстр.страх | 20–45 😟Страх | 45–55 😐Нейтрально | 55–80 😊Жадность | 80–100 🤑Экстр.жадность_")
        out.append("")

    # CoinGecko
    if cg:
        out.append("━━━ 📊 Рыночные данные ━━━")
        out.append(f"🏅 Рейтинг: #{cg['rank']} по капитализации")
        out.append(f"💰 Капитализация: {cg['market_cap_fmt']}")
        out.append(f"📈 Объём 24ч: {cg['volume_fmt']}")
        chg_emoji = "🟢" if cg['cap_change_24h'] >= 0 else "🔴"
        out.append(f"{chg_emoji} Изм. капитал. 24ч: {cg['cap_change_24h']:+.2f}%")
        out.append(f"🏆 Исторический максимум: ${cg['ath']:,.2f} ({cg['ath_change_pct']:+.1f}% от пика)")
        sent_emoji = "🟢" if cg['sentiment_up'] > 55 else "🔴" if cg['sentiment_up'] < 45 else "🟡"
        out.append(f"{sent_emoji} Настроение сообщества: {cg['sentiment_up']}% за рост / {cg['sentiment_down']}% за падение")
        out.append("")

    # Новости
    if news:
        out.append("━━━ 📰 Последние новости ━━━")
        for n in news[:4]:
            se = "🟢" if n["sentiment"] == "positive" else "🔴" if n["sentiment"] == "negative" else "⚪"
            out.append(f"{se} {n['title'][:75]}")
            out.append(f"   _📅 {n['published']}_")
        out.append("")

    # События
    if events:
        past   = [e for e in events if e["is_past"]]
        future = [e for e in events if not e["is_past"]]

        if future:
            out.append("━━━ 🔮 Предстоящие события ━━━")
            for e in future[:4]:
                days = e["days_diff"]
                if days == 0:   when = "сегодня"
                elif days == 1: when = "завтра"
                else:           when = f"через {days} дн."
                conf = f" ({e['confidence']}%)" if e['confidence'] else ""
                cat  = f" [{e['category']}]" if e['category'] else ""
                out.append(f"📌 *{e['date']}* ({when}){conf}{cat}")
                out.append(f"   {e['title']}")
            out.append("")

        if past:
            out.append("━━━ 📋 Прошедшие события ━━━")
            for e in past[:3]:
                out.append(f"✅ *{e['date']}* — {e['title'][:60]}")
            out.append("")

    elif not events:
        out.append("📅 _Событий в календаре не найдено_")
        out.append("")

    out.append("_Источники: CoinGecko · CryptoPanic · CoinMarketCal · Alternative.me_")

    text = "\n".join(out)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Тех. анализ", callback_data=f"analyze_{coin}_60")],
        [InlineKeyboardButton("🔍 Все таймфреймы", callback_data=f"multianalyze_{coin}")],
        [InlineKeyboardButton("📋 Другая монета", callback_data="back_main")],
    ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def do_portfolio(msg):
    """Показывает анализ портфеля Bybit"""
    await msg.edit_text("💼 Загружаю твой портфель с Bybit...")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API_URL}/portfolio/analyze")
            d = r.json()
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {e}")
        return

    if "error" in d:
        await msg.edit_text(
            f"⚠️ {d['error']}\n\n"
            f"Добавь BYBIT_API_KEY и BYBIT_API_SECRET на Render в сервис luna-crypto-api.",
            parse_mode="Markdown"
        )
        return

    total  = d.get("total_usdt", 0)
    usdt   = d.get("usdt_balance", 0)
    positions = d.get("positions", [])

    out = []
    out.append("💼 *Мой портфель Bybit*")
    out.append("")
    out.append(f"💰 Общая стоимость: *${total:.2f}*")
    out.append(f"💵 Свободный USDT: *${usdt:.2f}*")
    out.append("")

    if not positions:
        out.append("_Открытых позиций не найдено_")
    else:
        out.append("━━━ Мои монеты ━━━")
        out.append("")
        for p in positions:
            sig_emoji = "🟢" if p["signal"] == "BUY" else "🔴" if p["signal"] == "SELL" else "🟡"
            trend_emoji = "📈" if p["trend"] == "up" else "📉" if p["trend"] == "down" else "↔"
            out.append(f"{sig_emoji} *{p['symbol']}* — ${p['usd_value']:.2f}")
            out.append(f"   Кол-во: {p['balance']} | Цена: ${p['price']}")
            out.append(f"   RSI: {p['rsi']} {trend_emoji} | Уверенность: {p['confidence']}%")
            out.append(f"   {p['advice']}")
            out.append("")

    out.append("_Данные обновляются в реальном времени с Bybit_")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 История сделок",   callback_data="portfolio_history")],
        [InlineKeyboardButton("💰 Только баланс",    callback_data="portfolio_balance")],
        [InlineKeyboardButton("🏠 Главное меню",     callback_data="back_main")],
    ])
    await msg.edit_text("\n".join(out), parse_mode="Markdown", reply_markup=kb)


async def do_balance(msg):
    """Показывает баланс кошелька"""
    await msg.edit_text("💰 Загружаю баланс кошелька...")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API_URL}/portfolio/balance")
            d = r.json()
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {e}")
        return

    if "error" in d:
        await msg.edit_text(f"⚠️ {d['error']}")
        return

    out = []
    out.append("💰 *Баланс кошелька Bybit*")
    out.append("")
    out.append(f"💵 Итого: *${d['total_usdt']:.2f} USDT*")
    out.append("")
    out.append("━━━ Монеты ━━━")
    for coin in d.get("coins", []):
        out.append(f"• *{coin['symbol']}*: {coin['balance']} (≈ ${coin['usd_value']:.2f})")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Анализ портфеля",  callback_data="portfolio_main")],
        [InlineKeyboardButton("📋 История сделок",   callback_data="portfolio_history")],
        [InlineKeyboardButton("🏠 Главное меню",     callback_data="back_main")],
    ])
    await msg.edit_text("\n".join(out), parse_mode="Markdown", reply_markup=kb)


async def do_history(msg):
    """Показывает историю сделок"""
    await msg.edit_text("📋 Загружаю историю сделок...")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API_URL}/portfolio/history?limit=15")
            d = r.json()
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {e}")
        return

    if "error" in d:
        await msg.edit_text(f"⚠️ {d['error']}")
        return

    trades = d.get("trades", [])
    out = []
    out.append("📋 *История сделок (последние 15)*")
    out.append("")

    if not trades:
        out.append("_Сделок не найдено_")
    else:
        for t in trades:
            side_emoji = "🟢" if t["side"].upper() == "BUY" else "🔴"
            side_ru    = "Купил" if t["side"].upper() == "BUY" else "Продал"
            out.append(f"{side_emoji} *{t['symbol']}* — {side_ru}")
            out.append(f"   {t['qty']} по ${t['price']} = *${t['total_usdt']}*")
            out.append(f"   📅 {t['date']} | Комиссия: ${t['fee']}")
            out.append("")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Анализ портфеля", callback_data="portfolio_main")],
        [InlineKeyboardButton("🏠 Главное меню",    callback_data="back_main")],
    ])
    await msg.edit_text("\n".join(out), parse_mode="Markdown", reply_markup=kb)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_main":
        await query.message.reply_text("Выбери монету 👇", reply_markup=main_keyboard(query.from_user.id if query else 0))

    elif data == "help":
        await query.message.reply_text(
            "📖 Напиши /help для списка всех команд.",
            reply_markup=main_keyboard(query.from_user.id if query else 0)
        )

    elif data == "budget_menu":
        uid = query.from_user.id
        budget = get_budget(uid)
        await query.message.reply_text(
            f"💵 *Твой бюджет: ${budget:.0f}*\n\nВыбери сумму или введи вручную:",
            parse_mode="Markdown",
            reply_markup=budget_keyboard()
        )

    elif data.startswith("budget_set_"):
        amount = float(data.replace("budget_set_", ""))
        uid = query.from_user.id
        set_budget(uid, amount)
        await query.message.reply_text(
            f"✅ Бюджет обновлён: *${amount:.0f}*\n\nТеперь все расчёты будут под эту сумму.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(query.from_user.id if query else 0)
        )

    elif data == "budget_custom":
        ctx.user_data["awaiting_budget"] = True
        await query.message.reply_text(
            "✏️ Введи сумму бюджета в долларах (например: *35*):",
            parse_mode="Markdown"
        )

    elif data.startswith("group_"):
        group_map = {
            "group_fave": "⭐ Мой список",
            "group_top":  "🔥 Топ",
            "group_alt":  "⚡ Альткоины",
            "group_new":  "🆕 Новинки",
        }
        group = group_map.get(data, "⭐ Мой список")
        await query.message.reply_text(
            f"*{group}* — выбери монету:",
            parse_mode="Markdown",
            reply_markup=coin_keyboard(group)
        )

    elif data.startswith("coin_"):
        coin = data.replace("coin_", "")
        await query.message.reply_text(
            f"*{coin}/USDT* — выбери таймфрейм:",
            parse_mode="Markdown",
            reply_markup=interval_keyboard(coin)
        )

    elif data == "portfolio_main":
        loading = await query.message.reply_text("💼 Загружаю портфель...")
        await do_portfolio(loading)
        return

    elif data == "portfolio_balance":
        loading = await query.message.reply_text("💰 Загружаю баланс...")
        await do_balance(loading)
        return

    elif data == "portfolio_history":
        loading = await query.message.reply_text("📋 Загружаю историю...")
        await do_history(loading)
        return

    elif data.startswith("fundamental_"):
        coin = data.replace("fundamental_", "")
        loading = await query.message.reply_text(f"🏦 Загружаю фундаментальный анализ {coin}...")
        await do_fundamental(coin, loading, query)
        return

    elif data.startswith("multianalyze_"):
        coin = data.replace("multianalyze_", "")
        await do_multi_analysis(update, ctx, coin)

    elif data.startswith("analyze_"):
        parts = data.split("_")
        coin     = parts[1]
        interval = parts[2] if len(parts) > 2 else "60"
        await do_analysis(update, ctx, coin, interval)

    elif data.startswith("prices_"):
        which = data.replace("prices_", "")
        symbols = FAVE_COINS[:8] if which == "fave" else ["BTC","ETH","SOL","XRP","BNB","ADA","TON","DOGE"]
        loading = await query.message.reply_text("⏳ Загружаю цены с Bybit...")
        prices = await get_prices(symbols)
        if prices:
            await loading.edit_text(format_prices_msg(prices), parse_mode="Markdown",
                                     reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0))
        else:
            await loading.edit_text("⚠️ Ошибка загрузки цен.", reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0))

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Доступ закрыт.")
        return

    uid = update.effective_user.id
    text = update.message.text.strip()

    # Обработка ввода бюджета
    if ctx.user_data.get("awaiting_budget"):
        ctx.user_data["awaiting_budget"] = False
        try:
            amount = float(text.replace("$", "").replace(",", ".").strip())
            if amount < 1 or amount > 100000:
                raise ValueError
            set_budget(uid, amount)
            await update.message.reply_text(
                f"✅ Бюджет обновлён: *${amount:.0f}*\n\nТеперь все расчёты будут под эту сумму.",
                parse_mode="Markdown",
                reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0)
            )
        except:
            await update.message.reply_text(
                "⚠️ Неверный формат. Введи число, например: *35*",
                parse_mode="Markdown"
            )
        return

    # Если это тикер монеты
    upper = text.upper()
    if upper.isalpha() and 2 <= len(upper) <= 8:
        await do_analysis(update, ctx, upper, "60")
    else:
        await update.message.reply_text(
            "Напиши тикер монеты (например *ETH*) или используй меню 👇",
            parse_mode="Markdown",
            reply_markup=main_keyboard(update.effective_user.id if update.effective_user else 0)
        )


# ── ЗАПУСК ───────────────────────────────────────────────────────────────────
async def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("prices",  cmd_prices))
    app.add_handler(CommandHandler("fave",    cmd_fave))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    await app.bot.set_my_commands([
        BotCommand("start",   "Главное меню"),
        BotCommand("analyze", "Анализ монеты — /analyze ETH"),
        BotCommand("prices",  "Текущие цены"),
        BotCommand("fave",    "Мои монеты"),
        BotCommand("help",    "Помощь"),
    ])

    print("🔮 LUNA Bot запущен!")
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()  # держим бота живым
        await app.updater.stop()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
