import os
import math
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "YOUR_CLAUDE_API_KEY")
BYBIT_BASE     = "https://api.bybit.com"
CLAUDE_URL     = "https://api.anthropic.com/v1/messages"

MA_SETTINGS = {
    "15m": {"interval": "15",  "fast": 15,  "slow": 30,  "label": "15 мин"},
    "1h":  {"interval": "60",  "fast": 25,  "slow": 99,  "label": "1 час"},
    "4h":  {"interval": "240", "fast": 30,  "slow": 90,  "label": "4 часа"},
    "1d":  {"interval": "D",   "fast": 180, "slow": 360, "label": "1 день"},
}

TOP_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
             "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LTCUSDT"]

MONITORING_CHATS: set = set()

SCENARIOS = [
    ("🔵 Консервативный", 0.5),
    ("🟡 Умеренный",      1.0),
    ("🟢 Активный",       2.0),
    ("🔴 Агрессивный",    3.0),
]

# ─── UTILS ───────────────────────────────────────────────────────────────────

async def bybit_klines(symbol: str, interval: str, limit: int = 400) -> list:
    url = f"{BYBIT_BASE}/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            d = await r.json()
    if d.get("retCode") != 0:
        raise ValueError(d.get("retMsg", "Bybit error"))
    return list(reversed(d["result"]["list"]))


def calc_sma(closes: list, period: int) -> list:
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def calc_rsi(closes: list, period: int = 14) -> list:
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    if len(changes) < period:
        return []
    gains = sum(max(0, c) for c in changes[:period]) / period
    losses = sum(max(0, -c) for c in changes[:period]) / period
    rsi = []
    for i in range(period, len(changes)):
        gains = (gains * (period - 1) + max(0, changes[i])) / period
        losses = (losses * (period - 1) + max(0, -changes[i])) / period
        rs = gains / losses if losses != 0 else 100
        rsi.append(100 - 100 / (1 + rs))
    return rsi


async def claude_ask(prompt: str, max_tokens: int = 1000) -> str:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(CLAUDE_URL, headers=headers, json=body) as r:
            d = await r.json()
    if "content" in d and d["content"]:
        return d["content"][0]["text"]
    raise ValueError(str(d.get("error", d)))


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    elif p >= 1:
        return f"{p:.4f}"
    else:
        return f"{p:.6f}"


# ─── CALCULATOR HELPERS ───────────────────────────────────────────────────────

async def get_usdt_rate() -> float:
    """Fetch USDT/RUB rate from Bybit, fallback to 92."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BYBIT_BASE}/v5/market/tickers?category=spot&symbol=USDTRUB") as r:
                d = await r.json()
        price = d["result"]["list"][0]["lastPrice"]
        return float(price)
    except Exception:
        return 92.0


def fmt_rub(n: float) -> str:
    return "₽" + f"{round(n):,}".replace(",", " ")


def fmt_usd_c(n: float) -> str:
    return f"${n:,.2f}"


def growth_table(dep_usd: float, total_days: int, pct: float, rate: float) -> str:
    """Returns rows: Сегодня / Через 7дн / Через 30дн / Через 90дн / Цель."""
    checkpoints = [0]
    for d in [7, 30, 90]:
        if d < total_days:
            checkpoints.append(d)
    checkpoints.append(total_days)
    checkpoints = sorted(set(checkpoints))

    lines = []
    for day in checkpoints:
        bal = dep_usd * (1 + pct / 100) ** day
        dt  = (datetime.now() + timedelta(days=day)).strftime("%d.%m.%Y")
        if day == 0:
            label = "Сегодня"
        elif day == total_days:
            label = "🎯 Цель"
        else:
            label = f"Через {day} дн"
        lines.append(f"  `{label:<14}` {dt}  *{fmt_rub(bal * rate)}*  ({fmt_usd_c(bal)})")
    return "\n".join(lines)


# ─── ANALYSIS CORE ────────────────────────────────────────────────────────────

async def analyze_coin(symbol: str, tf_key: str = "1h", use_ai: bool = True) -> str:
    cfg = MA_SETTINGS[tf_key]
    klines = await bybit_klines(symbol, cfg["interval"], 400)
    closes = [float(k[4]) for k in klines]
    price  = closes[-1]

    ma_fast = calc_sma(closes, cfg["fast"])
    ma_slow = calc_sma(closes, cfg["slow"])
    rsi_arr = calc_rsi(closes)

    fast_last = ma_fast[-1]
    slow_last = ma_slow[-1]
    fast_prev = ma_fast[-2]
    slow_prev = ma_slow[-2]
    rsi = rsi_arr[-1] if rsi_arr else 50.0

    golden = fast_prev is not None and slow_prev is not None and fast_prev < slow_prev and fast_last > slow_last
    death  = fast_prev is not None and slow_prev is not None and fast_prev > slow_prev and fast_last < slow_last

    if golden:
        cross_txt = "✨ ЗОЛОТОЙ КРЕСТ формируется!"
    elif death:
        cross_txt = "💀 МЁРТВЫЙ КРЕСТ формируется!"
    elif fast_last > slow_last:
        cross_txt = "📈 Быстрая выше медленной"
    else:
        cross_txt = "📉 Быстрая ниже медленной"

    if rsi >= 70:
        rsi_zone = "🔴 Перекупленность"
    elif rsi <= 30:
        rsi_zone = "🟢 Перепроданность"
    else:
        rsi_zone = "🟡 Нейтральная зона"

    header = (
        f"📊 *{symbol}* | {cfg['label']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: `{fmt_price(price)}`\n\n"
        f"📈 *Скользящие средние:*\n"
        f"  MA{cfg['fast']}: `{fmt_price(fast_last)}`\n"
        f"  MA{cfg['slow']}: `{fmt_price(slow_last)}`\n"
        f"  {cross_txt}\n\n"
        f"📡 *RSI (14):* `{rsi:.1f}` — {rsi_zone}\n"
    )

    if not use_ai:
        return header

    prompt = (
        f"Ты профессиональный трейдер. Проанализируй {symbol} на таймфрейме {cfg['label']}.\n\n"
        f"ДАННЫЕ:\n"
        f"- Цена: {price:.6f}\n"
        f"- MA{cfg['fast']} (быстрая): {fast_last:.6f}\n"
        f"- MA{cfg['slow']} (медленная): {slow_last:.6f}\n"
        f"- RSI(14): {rsi:.2f}\n"
        f"- Крест: {cross_txt}\n\n"
        f"Дай анализ:\n"
        f"1. Текущий тренд (1 предложение)\n"
        f"2. Что значит RSI сейчас\n"
        f"3. Крест — есть или нет\n"
        f"4. Рекомендация: СПОТ или ФЬЮЧ, ЛОНГ или ШОРТ, плечо x?, вход, стоп, тейк\n"
        f"5. Уверенность: X%\n\n"
        f"Кратко и по делу. На русском."
    )
    ai_text = await claude_ask(prompt, max_tokens=600)
    return header + f"\n🤖 *AI анализ:*\n{ai_text}"


# ─── COMMANDS ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 Анализ монеты", callback_data="menu_analysis"),
         InlineKeyboardButton("🔵 Скринер",       callback_data="menu_screener")],
        [InlineKeyboardButton("📰 Новости",        callback_data="menu_news"),
         InlineKeyboardButton("🧮 Калькулятор",    callback_data="menu_calc")],
        [InlineKeyboardButton("🔔 Мониторинг ON",  callback_data="menu_monitor")],
    ]
    await update.message.reply_text(
        "🌙 *LUNA — Crypto Intelligence*\n\n"
        "Выбери раздел или используй команды:\n"
        "/analysis — анализ монеты\n"
        "/screener — скринер сигналов\n"
        "/news — новости рынка\n"
        "/calc — калькулятор\n"
        "/monitor — автомониторинг\n",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cmd_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args   = ctx.args
    symbol = args[0].upper() if args else "BTCUSDT"
    tf_key = args[1].lower() if len(args) > 1 else "1h"
    if tf_key not in MA_SETTINGS:
        tf_key = "1h"

    kb = [[InlineKeyboardButton(v["label"], callback_data=f"tf_{symbol}_{k}")
           for k, v in MA_SETTINGS.items()]]
    msg = await update.message.reply_text(
        f"⏳ Анализирую *{symbol}*...", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    try:
        text = await analyze_coin(symbol, tf_key)
        await msg.edit_text(text, parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


async def cmd_screener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔵 Сканирую рынок...")
    results = []
    for coin in TOP_COINS:
        try:
            klines = await bybit_klines(coin, "60", 200)
            closes = [float(k[4]) for k in klines]
            price  = closes[-1]
            ma25   = calc_sma(closes, 25)
            ma99   = calc_sma(closes, 99)
            rsi_arr = calc_rsi(closes)
            rsi    = rsi_arr[-1] if rsi_arr else 50
            fast   = ma25[-1]; slow = ma99[-1]
            bullish = fast > slow and rsi < 65
            bearish = fast < slow and rsi > 35
            signal  = "ЛОНГ 📈" if bullish else "ШОРТ 📉" if bearish else "—"
            strength = min(95, 50 + abs(rsi - 50) * 0.8) if (bullish or bearish) else 30
            results.append((coin, price, rsi, signal, strength))
        except Exception:
            pass

    results.sort(key=lambda x: -x[4])
    lines = ["🔵 *Скринер LUNA* — Топ сигналы\n"]
    for coin, price, rsi, signal, strength in results[:10]:
        bar = "█" * int(strength / 10) + "░" * (10 - int(strength / 10))
        lines.append(f"*{coin}* — {signal}\n  Цена: `{fmt_price(price)}` | RSI: `{rsi:.1f}` | `{bar}` {strength:.0f}%\n")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📰 Загружаю новости через Claude...")
    try:
        prompt = (
            "Сгенерируй 6 актуальных крипто новостей для трейдера. "
            "Формат каждой: ЗАГОЛОВОК, 1-2 предложения, влияние (📈 Рост / 📉 Падение / ➡️ Нейтрально). "
            "Темы: листинги, регуляции, партнёрства, макро. На русском."
        )
        text = await claude_ask(prompt, max_tokens=1000)
        await msg.edit_text(f"📰 *Новости рынка*\n\n{text}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


async def cmd_calc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧮 *Калькулятор LUNA* — суммы в рублях\n\n"
        "Режим 1 — депозит → цель:\n"
        "`/calc 100000 1000000`\n\n"
        "Режим 2 — цель + срок → нужный депозит:\n"
        "`/calc_need 1000000 180`\n\n"
        "_Курс USDT/RUB подтягивается автоматически с Bybit_",
        parse_mode="Markdown"
    )


async def cmd_calc_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /calc [deposit_rub] [goal_rub]"""
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Использование: `/calc 100000 1000000`\n_(депозит ₽ → цель ₽)_",
            parse_mode="Markdown"
        )
        return
    dep_rub  = float(args[0])
    goal_rub = float(args[1])
    if goal_rub <= dep_rub:
        await update.message.reply_text("❌ Цель должна быть больше депозита")
        return

    msg  = await update.message.reply_text("⏳ Считаю...")
    rate = await get_usdt_rate()
    dep_usd  = dep_rub  / rate
    goal_usd = goal_rub / rate

    lines = [
        "🧮 *Калькулятор* — депозит → цель",
        f"Депозит: *{fmt_rub(dep_rub)}* ({fmt_usd_c(dep_usd)})",
        f"Цель:    *{fmt_rub(goal_rub)}* ({fmt_usd_c(goal_usd)})",
        f"Курс: {rate:.0f} ₽/USDT\n",
    ]
    for label, pct in SCENARIOS:
        days       = math.ceil(math.log(goal_usd / dep_usd) / math.log(1 + pct / 100))
        final_usd  = dep_usd * (1 + pct / 100) ** days
        profit_rub = (final_usd - dep_usd) * rate
        day1_rub   = dep_usd * pct / 100 * rate
        target_dt  = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
        table      = growth_table(dep_usd, days, pct, rate)
        lines.append(
            f"*{label}* — {pct}%/день\n"
            f"  Срок: *{days} дней* (до {target_dt})\n"
            f"  Прибыль за весь срок: *+{fmt_rub(profit_rub)}*\n"
            f"  Прибыль в первый день: *+{fmt_rub(day1_rub)}*\n"
            f"{table}\n"
        )
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_calc_need(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /calc_need [goal_rub] [days]"""
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Использование: `/calc_need 1000000 180`\n_(цель ₽ + срок → нужный депозит)_",
            parse_mode="Markdown"
        )
        return
    goal_rub = float(args[0])
    days     = int(args[1])

    msg      = await update.message.reply_text("⏳ Считаю...")
    rate     = await get_usdt_rate()
    goal_usd = goal_rub / rate

    lines = [
        "🧮 *Калькулятор* — цель + срок → депозит",
        f"Цель:  *{fmt_rub(goal_rub)}* ({fmt_usd_c(goal_usd)})",
        f"Срок:  *{days} дней*",
        f"Курс: {rate:.0f} ₽/USDT\n",
    ]
    for label, pct in SCENARIOS:
        dep_usd    = goal_usd / (1 + pct / 100) ** days
        dep_rub    = dep_usd * rate
        profit_rub = (goal_usd - dep_usd) * rate
        day1_rub   = dep_usd * pct / 100 * rate
        dayL_rub   = goal_usd * pct / 100 * rate
        table      = growth_table(dep_usd, days, pct, rate)
        lines.append(
            f"*{label}* — {pct}%/день\n"
            f"  Нужен депозит: *{fmt_rub(dep_rub)}* ({fmt_usd_c(dep_usd)})\n"
            f"  Прибыль первый день: *+{fmt_rub(day1_rub)}*\n"
            f"  Прибыль последний день: *+{fmt_rub(dayL_rub)}*\n"
            f"  Итого прибыль: *+{fmt_rub(profit_rub)}*\n"
            f"{table}\n"
        )
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in MONITORING_CHATS:
        MONITORING_CHATS.discard(chat_id)
        await update.message.reply_text("🔕 Автомониторинг *выключен*", parse_mode="Markdown")
    else:
        MONITORING_CHATS.add(chat_id)
        await update.message.reply_text(
            "🔔 Автомониторинг *включён*\n"
            "Буду уведомлять при:\n"
            "• Золотом / мёртвом кресте\n"
            "• RSI > 75 или < 25\n"
            "Проверка каждые 30 минут.",
            parse_mode="Markdown"
        )


# ─── CALLBACK HANDLER ─────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("tf_"):
        _, symbol, tf_key = data.split("_", 2)
        kb = [[InlineKeyboardButton(v["label"], callback_data=f"tf_{symbol}_{k}")
               for k, v in MA_SETTINGS.items()]]
        await q.edit_message_text(f"⏳ Анализирую *{symbol}* ({MA_SETTINGS[tf_key]['label']})...",
                                   parse_mode="Markdown")
        try:
            text = await analyze_coin(symbol, tf_key)
            await q.edit_message_text(text, parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await q.edit_message_text(f"❌ Ошибка: {e}")

    elif data == "menu_analysis":
        kb = [[InlineKeyboardButton(c, callback_data=f"tf_{c}_1h") for c in TOP_COINS[:5]],
              [InlineKeyboardButton(c, callback_data=f"tf_{c}_1h") for c in TOP_COINS[5:]]]
        await q.edit_message_text("Выбери монету:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_screener":
        await q.edit_message_text("⏳ Сканирую...")
        results = []
        for coin in TOP_COINS:
            try:
                klines = await bybit_klines(coin, "60", 200)
                closes = [float(k[4]) for k in klines]
                price  = closes[-1]
                ma25   = calc_sma(closes, 25); ma99 = calc_sma(closes, 99)
                rsi_arr = calc_rsi(closes); rsi = rsi_arr[-1] if rsi_arr else 50
                fast   = ma25[-1]; slow = ma99[-1]
                bullish = fast > slow and rsi < 65
                bearish = fast < slow and rsi > 35
                signal  = "ЛОНГ 📈" if bullish else "ШОРТ 📉" if bearish else "—"
                strength = min(95, 50 + abs(rsi - 50) * 0.8) if (bullish or bearish) else 30
                results.append((coin, price, rsi, signal, strength))
            except Exception:
                pass
        results.sort(key=lambda x: -x[4])
        lines = ["🔵 *Скринер LUNA*\n"]
        for coin, price, rsi, signal, strength in results[:8]:
            lines.append(f"*{coin}* — {signal} | RSI `{rsi:.1f}` | `{strength:.0f}%`")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown")

    elif data == "menu_news":
        await q.edit_message_text("📰 Загружаю новости...")
        try:
            text = await claude_ask(
                "Сгенерируй 6 актуальных крипто новостей для трейдера. "
                "Каждая: заголовок, 1-2 предложения, влияние. На русском.", 1000)
            await q.edit_message_text(f"📰 *Новости рынка*\n\n{text}", parse_mode="Markdown")
        except Exception as e:
            await q.edit_message_text(f"❌ Ошибка: {e}")

    elif data == "menu_calc":
        await q.edit_message_text(
            "🧮 *Калькулятор* — суммы в рублях\n\n"
            "`/calc 100000 1000000` — депозит → цель\n"
            "`/calc_need 1000000 180` — цель + срок → депозит",
            parse_mode="Markdown"
        )

    elif data == "menu_monitor":
        chat_id = q.message.chat_id
        if chat_id in MONITORING_CHATS:
            MONITORING_CHATS.discard(chat_id)
            await q.edit_message_text("🔕 Автомониторинг выключен")
        else:
            MONITORING_CHATS.add(chat_id)
            await q.edit_message_text("🔔 Автомониторинг включён (каждые 30 мин)")


# ─── AUTO MONITORING LOOP ─────────────────────────────────────────────────────

async def monitoring_loop(app: Application):
    await asyncio.sleep(60)
    while True:
        if MONITORING_CHATS:
            alerts = []
            for coin in TOP_COINS:
                try:
                    klines = await bybit_klines(coin, "60", 100)
                    closes = [float(k[4]) for k in klines]
                    price  = closes[-1]
                    ma25   = calc_sma(closes, 25); ma99 = calc_sma(closes, 99)
                    rsi_arr = calc_rsi(closes); rsi = rsi_arr[-1] if rsi_arr else 50
                    fl = ma25[-1]; sl = ma99[-1]; fp = ma25[-2]; sp = ma99[-2]
                    if fp < sp and fl > sl:
                        alerts.append(f"✨ *{coin}* — ЗОЛОТОЙ КРЕСТ на 1ч | `{fmt_price(price)}`")
                    elif fp > sp and fl < sl:
                        alerts.append(f"💀 *{coin}* — МЁРТВЫЙ КРЕСТ на 1ч | `{fmt_price(price)}`")
                    if rsi >= 78:
                        alerts.append(f"🔴 *{coin}* — RSI `{rsi:.1f}` перекупленность!")
                    elif rsi <= 22:
                        alerts.append(f"🟢 *{coin}* — RSI `{rsi:.1f}` перепроданность!")
                except Exception:
                    pass

            if alerts:
                msg = "🚨 *LUNA Мониторинг*\n\n" + "\n".join(alerts)
                for chat_id in list(MONITORING_CHATS):
                    try:
                        await app.bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except Exception:
                        MONITORING_CHATS.discard(chat_id)

        await asyncio.sleep(1800)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("analysis",  cmd_analysis))
    app.add_handler(CommandHandler("screener",  cmd_screener))
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("calc",      cmd_calc_deposit))
    app.add_handler(CommandHandler("calc_need", cmd_calc_need))
    app.add_handler(CommandHandler("monitor",   cmd_monitor))
    app.add_handler(CallbackQueryHandler(on_callback))

    loop = asyncio.get_event_loop()
    loop.create_task(monitoring_loop(app))

    print("🌙 LUNA Bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
