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
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
DASHBOARD_URL  = os.getenv("DASHBOARD_URL", "https://olgapshedromirskaya-sys.github.io/luna-crypto-api/dashboard.html")
BYBIT_BASE     = "https://api.bybit.com"
CLAUDE_URL     = "https://api.anthropic.com/v1/messages"

MA_SETTINGS = {
    "15m": {"interval": "15",  "fast": 15,  "slow": 30,  "label": "15 мин"},
    "1h":  {"interval": "60",  "fast": 25,  "slow": 99,  "label": "1 час"},
    "4h":  {"interval": "240", "fast": 30,  "slow": 90,  "label": "4 часа"},
    "1d":  {"interval": "D",   "fast": 180, "slow": 360, "label": "1 день"},
}

TOP_COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
             "DOGEUSDT","ADAUSDT","AVAXUSDT","DOTUSDT","LTCUSDT"]

MONITORING_CHATS: set = set()
WAITING_INPUT: dict = {}  # chat_id -> "calc1" | "calc2"

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


async def claude_ask(prompt: str, max_tokens: int = 800) -> str:
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
    return f"{p:.6f}"


# ─── CALCULATOR HELPERS ───────────────────────────────────────────────────────

async def get_usdt_rate() -> float:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BYBIT_BASE}/v5/market/tickers?category=spot&symbol=USDTRUB") as r:
                d = await r.json()
        return float(d["result"]["list"][0]["lastPrice"])
    except Exception:
        return 92.0


def fmt_rub(n: float) -> str:
    return "\u20bd" + f"{round(n):,}".replace(",", "\u00a0")


def fmt_usd_c(n: float) -> str:
    return f"${n:,.2f}"


def growth_table(dep_usd: float, total_days: int, pct: float, rate: float) -> str:
    checkpoints = [0] + [d for d in [7, 30, 90] if d < total_days] + [total_days]
    checkpoints = sorted(set(checkpoints))
    lines = []
    for day in checkpoints:
        bal = dep_usd * (1 + pct / 100) ** day
        dt  = (datetime.now() + timedelta(days=day)).strftime("%d.%m.%Y")
        lbl = "Сегодня" if day == 0 else ("\U0001f3af Цель" if day == total_days else f"Через {day} дн")
        lines.append(f"  `{lbl:<14}` {dt}  *{fmt_rub(bal * rate)}*  ({fmt_usd_c(bal)})")
    return "\n".join(lines)


# ─── ANALYSIS CORE ────────────────────────────────────────────────────────────

async def analyze_coin(symbol: str, tf_key: str = "1h") -> str:
    cfg = MA_SETTINGS[tf_key]
    klines = await bybit_klines(symbol, cfg["interval"], 400)
    closes = [float(k[4]) for k in klines]
    price  = closes[-1]

    ma_fast = calc_sma(closes, cfg["fast"])
    ma_slow = calc_sma(closes, cfg["slow"])
    rsi_arr = calc_rsi(closes)

    fl = ma_fast[-1]; sl = ma_slow[-1]
    fp = ma_fast[-2]; sp = ma_slow[-2]
    rsi = rsi_arr[-1] if rsi_arr else 50.0

    if fp is not None and sp is not None and fp < sp and fl > sl:
        cross = "\u2728 ЗОЛОТОЙ КРЕСТ формируется!"
    elif fp is not None and sp is not None and fp > sp and fl < sl:
        cross = "\U0001f480 МЁРТВЫЙ КРЕСТ формируется!"
    elif fl > sl:
        cross = "\U0001f4c8 Быстрая выше медленной"
    else:
        cross = "\U0001f4c9 Быстрая ниже медленной"

    rsi_zone = "\U0001f534 Перекупленность" if rsi >= 70 else "\U0001f7e2 Перепроданность" if rsi <= 30 else "\U0001f7e1 Нейтральная зона"

    header = (
        f"\U0001f4ca *{symbol}* | {cfg['label']}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4b0 Цена: `{fmt_price(price)}`\n\n"
        f"\U0001f4c8 MA{cfg['fast']}: `{fmt_price(fl)}`\n"
        f"\U0001f4c9 MA{cfg['slow']}: `{fmt_price(sl)}`\n"
        f"   {cross}\n\n"
        f"\U0001f4e1 RSI(14): `{rsi:.1f}` — {rsi_zone}\n"
    )

    if not CLAUDE_API_KEY:
        return header + "\n_Добавь CLAUDE\\_API\\_KEY для AI анализа_"

    prompt = (
        f"Ты профессиональный трейдер. Проанализируй {symbol} на {cfg['label']}.\n"
        f"Цена: {fmt_price(price)}, MA{cfg['fast']}: {fmt_price(fl)}, MA{cfg['slow']}: {fmt_price(sl)}, RSI: {rsi:.1f}\n"
        f"{cross}\n\n"
        f"Дай: 1) тренд 2) RSI 3) крест 4) ЛОНГ/ШОРТ+плечо+стоп+тейк 5) уверенность %\n"
        f"Кратко. На русском."
    )
    ai_text = await claude_ask(prompt, 500)
    return header + f"\n\U0001f916 *AI анализ:*\n{ai_text}"


# ─── KEYBOARDS ───────────────────────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f310 Открыть дашборд LUNA", url=DASHBOARD_URL)],
        [InlineKeyboardButton("\U0001f4ca Анализ монеты", callback_data="menu_analysis"),
         InlineKeyboardButton("\U0001f535 Скринер",       callback_data="menu_screener")],
        [InlineKeyboardButton("\U0001f4f0 Новости",        callback_data="menu_news"),
         InlineKeyboardButton("\U0001f9ee Калькулятор",    callback_data="menu_calc")],
        [InlineKeyboardButton("\U0001f514 Мониторинг",     callback_data="menu_monitor")],
    ])


def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("\u25c0\ufe0f Главное меню", callback_data="menu_back")]])


def coin_kb():
    coins = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LTC"]
    rows = []
    row = []
    for i, c in enumerate(coins):
        row.append(InlineKeyboardButton(c, callback_data=f"analyze_{c}USDT"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("\u25c0\ufe0f Назад", callback_data="menu_back")])
    return InlineKeyboardMarkup(rows)


def tf_kb(symbol: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(v["label"], callback_data=f"tf_{symbol}_{k}")
         for k, v in MA_SETTINGS.items()],
        [InlineKeyboardButton("\u25c0\ufe0f Назад", callback_data="menu_analysis")]
    ])


def calc_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4c8 Депозит \u2192 Цель",       callback_data="calc_mode1")],
        [InlineKeyboardButton("\U0001f3af Цель + Срок \u2192 Депозит", callback_data="calc_mode2")],
        [InlineKeyboardButton("\u25c0\ufe0f Назад",                    callback_data="menu_back")],
    ])


# ─── COMMANDS ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f319 *LUNA — Crypto Intelligence*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )


# ─── CALLBACK HANDLER ────────────────────────────────────────────────────────

async def send_or_edit(q, text: str, kb=None):
    try:
        await q.edit_message_text(text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = q.message.chat_id

    # ── Главное меню ──
    if data == "menu_back":
        await send_or_edit(q, "\U0001f319 *LUNA — Crypto Intelligence*\n\nВыбери раздел:", main_menu_kb())

    # ── Анализ: выбор монеты ──
    elif data == "menu_analysis":
        await send_or_edit(q, "\U0001f4ca *Анализ монеты*\n\nВыбери монету:", coin_kb())

    # ── Анализ: выбор таймфрейма ──
    elif data.startswith("analyze_"):
        symbol = data.split("_", 1)[1]
        await send_or_edit(q, f"\U0001f4ca *{symbol}*\n\nВыбери таймфрейм:", tf_kb(symbol))

    # ── Анализ: запуск ──
    elif data.startswith("tf_"):
        _, symbol, tf_key = data.split("_", 2)
        kb = tf_kb(symbol)
        await send_or_edit(q, f"\u23f3 Анализирую *{symbol}* ({MA_SETTINGS[tf_key]['label']})...", kb)
        try:
            text = await analyze_coin(symbol, tf_key)
            await send_or_edit(q, text, kb)
        except Exception as e:
            await send_or_edit(q, f"\u274c Ошибка: {e}", back_btn())

    # ── Скринер ──
    elif data == "menu_screener":
        await send_or_edit(q, "\u23f3 Сканирую 10 монет...", back_btn())
        results = []
        for coin in TOP_COINS:
            try:
                klines = await bybit_klines(coin, "60", 200)
                closes = [float(k[4]) for k in klines]
                price  = closes[-1]
                ma25 = calc_sma(closes, 25)
                ma99 = calc_sma(closes, 99)
                rsi_arr = calc_rsi(closes)
                rsi = rsi_arr[-1] if rsi_arr else 50
                fast = ma25[-1]; slow = ma99[-1]
                bull = fast > slow and rsi < 65
                bear = fast < slow and rsi > 35
                sig  = "ЛОНГ \U0001f4c8" if bull else "\u0428\u041e\u0420\u0422 \U0001f4c9" if bear else "\u2014"
                strength = min(95, 50 + abs(rsi - 50) * 0.8) if (bull or bear) else 25
                results.append((coin, price, rsi, sig, strength))
            except Exception:
                pass
        results.sort(key=lambda x: -x[4])
        lines = ["\U0001f535 *Скринер LUNA* — Топ сигналы\n"]
        for coin, price, rsi, sig, strength in results[:10]:
            bar = "\u2588" * int(strength / 10) + "\u2591" * (10 - int(strength / 10))
            lines.append(f"*{coin}* — {sig}\n  `{fmt_price(price)}` RSI:`{rsi:.1f}` `{bar}` {strength:.0f}%\n")
        await send_or_edit(q, "\n".join(lines), back_btn())

    # ── Новости ──
    elif data == "menu_news":
        await send_or_edit(q, "\U0001f4f0 Загружаю новости...", back_btn())
        try:
            text = await claude_ask(
                "Сгенерируй 6 актуальных крипто новостей для трейдера. "
                "Каждая: ЗАГОЛОВОК, 1-2 предложения, влияние \U0001f4c8/\U0001f4c9/\u27a1\ufe0f. На русском.", 1000)
            await send_or_edit(q, f"\U0001f4f0 *Новости рынка*\n\n{text}", back_btn())
        except Exception as e:
            await send_or_edit(q, f"\u274c Ошибка: {e}", back_btn())

    # ── Мониторинг ──
    elif data == "menu_monitor":
        if chat_id in MONITORING_CHATS:
            MONITORING_CHATS.discard(chat_id)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f514 Включить мониторинг", callback_data="menu_monitor")],
                [InlineKeyboardButton("\u25c0\ufe0f Главное меню", callback_data="menu_back")]
            ])
            await send_or_edit(q, "\U0001f515 *Мониторинг выключен*", kb)
        else:
            MONITORING_CHATS.add(chat_id)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f515 Выключить мониторинг", callback_data="menu_monitor")],
                [InlineKeyboardButton("\u25c0\ufe0f Главное меню", callback_data="menu_back")]
            ])
            await send_or_edit(q,
                "\U0001f514 *Мониторинг включён*\n\n"
                "Уведомлю при:\n"
                "\u2022 Золотом / мёртвом кресте\n"
                "\u2022 RSI > 75 или < 25\n\n"
                "Проверка каждые 30 минут.", kb)

    # ── Калькулятор: меню ──
    elif data == "menu_calc":
        await send_or_edit(q, "\U0001f9ee *Калькулятор LUNA*\n\nВыбери режим:", calc_kb())

    elif data == "calc_mode1":
        WAITING_INPUT[chat_id] = "calc1"
        await send_or_edit(q,
            "\U0001f9ee *Депозит \u2192 Цель*\n\n"
            "Напиши два числа через пробел:\n"
            "`депозит_\u20bd цель_\u20bd`\n\n"
            "\u041f\u0440\u0438\u043c\u0435\u0440: `100000 1000000`",
            InlineKeyboardMarkup([[InlineKeyboardButton("\u25c0\ufe0f Назад", callback_data="menu_calc")]]))

    elif data == "calc_mode2":
        WAITING_INPUT[chat_id] = "calc2"
        await send_or_edit(q,
            "\U0001f9ee *Цель + Срок \u2192 Депозит*\n\n"
            "Напиши два числа через пробел:\n"
            "`цель_\u20bd срок_дней`\n\n"
            "\u041f\u0440\u0438\u043c\u0435\u0440: `1000000 180`",
            InlineKeyboardMarkup([[InlineKeyboardButton("\u25c0\ufe0f Назад", callback_data="menu_calc")]]))


# ─── MESSAGE HANDLER (калькулятор) ────────────────────────────────────────────

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mode = WAITING_INPUT.get(chat_id)
    if not mode:
        return

    text = update.message.text.strip()
    parts = text.replace(",", ".").split()

    if len(parts) < 2:
        await update.message.reply_text(
            "\u274c Нужно два числа через пробел.\nПример: `100000 1000000`",
            parse_mode="Markdown")
        return

    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        await update.message.reply_text(
            "\u274c Введи числа, например: `100000 1000000`", parse_mode="Markdown")
        return

    WAITING_INPUT.pop(chat_id, None)
    msg = await update.message.reply_text("\u23f3 Считаю...")
    rate = await get_usdt_rate()

    result_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f504 Ещё расчёт", callback_data="menu_calc")],
        [InlineKeyboardButton("\u25c0\ufe0f Главное меню", callback_data="menu_back")]
    ])

    if mode == "calc1":
        dep_rub, goal_rub = a, b
        if goal_rub <= dep_rub:
            await msg.edit_text("\u274c Цель должна быть больше депозита")
            return
        dep_usd  = dep_rub / rate
        goal_usd = goal_rub / rate
        lines = [
            "\U0001f9ee *Калькулятор* — депозит \u2192 цель",
            f"Депозит: *{fmt_rub(dep_rub)}* ({fmt_usd_c(dep_usd)})",
            f"Цель:    *{fmt_rub(goal_rub)}* ({fmt_usd_c(goal_usd)})",
            f"Курс: {rate:.0f} \u20bd/USDT\n",
        ]
        for label, pct in SCENARIOS:
            days      = math.ceil(math.log(goal_usd / dep_usd) / math.log(1 + pct / 100))
            final_usd = dep_usd * (1 + pct / 100) ** days
            profit    = (final_usd - dep_usd) * rate
            day1      = dep_usd * pct / 100 * rate
            dt        = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
            table     = growth_table(dep_usd, days, pct, rate)
            lines.append(
                f"*{label}* — {pct}%/день\n"
                f"  Срок: *{days} дн* (до {dt})\n"
                f"  Прибыль: *+{fmt_rub(profit)}*\n"
                f"  День 1: *+{fmt_rub(day1)}*\n"
                f"{table}\n"
            )

    else:  # calc2
        goal_rub = a
        days = int(b)
        goal_usd = goal_rub / rate
        lines = [
            "\U0001f9ee *Калькулятор* — цель + срок \u2192 депозит",
            f"Цель:  *{fmt_rub(goal_rub)}* ({fmt_usd_c(goal_usd)})",
            f"Срок:  *{days} дней*",
            f"Курс: {rate:.0f} \u20bd/USDT\n",
        ]
        for label, pct in SCENARIOS:
            dep_usd = goal_usd / (1 + pct / 100) ** days
            dep_rub = dep_usd * rate
            profit  = (goal_usd - dep_usd) * rate
            d1      = dep_usd * pct / 100 * rate
            dL      = goal_usd * pct / 100 * rate
            table   = growth_table(dep_usd, days, pct, rate)
            lines.append(
                f"*{label}* — {pct}%/день\n"
                f"  Нужен депозит: *{fmt_rub(dep_rub)}* ({fmt_usd_c(dep_usd)})\n"
                f"  День 1: *+{fmt_rub(d1)}* | Посл.день: *+{fmt_rub(dL)}*\n"
                f"  Прибыль: *+{fmt_rub(profit)}*\n"
                f"{table}\n"
            )

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=result_kb)


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
                    ma25 = calc_sma(closes, 25)
                    ma99 = calc_sma(closes, 99)
                    rsi_arr = calc_rsi(closes)
                    rsi = rsi_arr[-1] if rsi_arr else 50
                    fl = ma25[-1]; sl = ma99[-1]
                    fp = ma25[-2]; sp = ma99[-2]
                    if fp < sp and fl > sl:
                        alerts.append(f"\u2728 *{coin}* — ЗОЛОТОЙ КРЕСТ | `{fmt_price(price)}`")
                    elif fp > sp and fl < sl:
                        alerts.append(f"\U0001f480 *{coin}* — МЁРТВЫЙ КРЕСТ | `{fmt_price(price)}`")
                    if rsi >= 78:
                        alerts.append(f"\U0001f534 *{coin}* — RSI `{rsi:.1f}` перекупленность!")
                    elif rsi <= 22:
                        alerts.append(f"\U0001f7e2 *{coin}* — RSI `{rsi:.1f}` перепроданность!")
                except Exception:
                    pass

            if alerts:
                msg = "\U0001f6a8 *LUNA Мониторинг*\n\n" + "\n".join(alerts)
                for chat_id in list(MONITORING_CHATS):
                    try:
                        await app.bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except Exception:
                        MONITORING_CHATS.discard(chat_id)

        await asyncio.sleep(1800)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    loop = asyncio.get_event_loop()
    loop.create_task(monitoring_loop(app))

    print("\U0001f319 LUNA Bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
