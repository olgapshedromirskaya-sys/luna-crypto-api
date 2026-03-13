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

def format_analysis(data: dict, budget_usd: float = 22.0) -> str:
    sym  = data["symbol"]
    pair = data["pair"]
    price = data["price"]
    chg  = data["change_24h"]
    ind  = data["indicators"]
    sig  = data["signal"]
    trd  = data["trade"]
    lvl  = data["levels"]

    se = signal_emoji(sig["signal"])
    ce = change_emoji(chg)

    # Сигнал
    signal_line = f"{se} *{sig['label']}* — уверенность {sig['confidence']}%"

    # RSI расшифровка
    rsi = ind["rsi"]
    if rsi < 30:
        rsi_txt = f"{rsi} 🔵 перепроданность (хорошо для покупки)"
    elif rsi > 70:
        rsi_txt = f"{rsi} 🔴 перекупленность (осторожно)"
    else:
        rsi_txt = f"{rsi} ⚪ нейтральная зона"

    # MACD расшифровка
    macd = ind["macd"]
    macd_txt = "🟢 бычий — импульс роста" if macd["cross"] == "bullish" else "🔴 медвежий — импульс снижения"

    # Тренд EMA
    ema50, ema200 = ind["ema50"], ind["ema200"]
    if price > ema50 > ema200:
        trend_txt = "📈 Устойчивый рост (цена выше EMA50 и EMA200)"
    elif price < ema50 < ema200:
        trend_txt = "📉 Нисходящий тренд (цена ниже EMA50 и EMA200)"
    elif price > ema50:
        trend_txt = "↗️ Краткосрочный рост (выше EMA50)"
    else:
        trend_txt = "↘️ Краткосрочное снижение (ниже EMA50)"

    # Уровни
    sup = " / ".join([format_price(s) for s in lvl["support"]]) or "не найдены"
    res = " / ".join([format_price(r) for r in lvl["resistance"]]) or "не найдены"

    # Расчёт прибыли для бюджета
    invest = min(budget_usd * 0.7, budget_usd - 5)  # вкладываем 70%, остаток резерв
    coins_qty = invest / price if price > 0 else 0

    tp1_profit = coins_qty * (trd["tp1"] - price)
    tp2_profit = coins_qty * (trd["tp2"] - price)
    sl_loss    = coins_qty * (trd["stop_loss"] - price)

    usd_to_rub = 90  # примерный курс

    # Сборка сообщения
    msg = f"""🔮 *LUNA — Анализ {pair}*
━━━━━━━━━━━━━━━━━━━━
💰 Цена: *{format_price(price)}* {ce} {chg:+.2f}% за 24ч

{signal_line}

📊 *Индикаторы:*
• RSI: {rsi_txt}
• MACD: {macd_txt}
• Тренд: {trend_txt}
• Bollinger: цена на {round(ind['bollinger']['position']*100)}% от нижней к верхней полосе

📍 *Уровни:*
• Поддержка: {sup}
• Сопротивление: {res}

🎯 *Сделка:*
• Вход: {format_price(trd['entry'])}
• Стоп-лосс: {format_price(trd['stop_loss'])} ({trd['stop_pct']:+.1f}%) — _автопродажа при убытке_
• Тейк-профит 1: {format_price(trd['tp1'])} ({trd['tp1_pct']:+.1f}%) — _фиксируй первую прибыль_
• Тейк-профит 2: {format_price(trd['tp2'])} ({trd['tp2_pct']:+.1f}%) — _если рост продолжится_

💵 *Расчёт для ${invest:.0f} (из бюджета ${budget_usd:.0f}):*
• Купишь: {coins_qty:.4f} {sym}
• При ТП1: *+${tp1_profit:.2f}* (+{tp1_profit*usd_to_rub:.0f} ₽)
• При ТП2: *+${tp2_profit:.2f}* (+{tp2_profit*usd_to_rub:.0f} ₽)
• Стоп-лосс: *{sl_loss:.2f}$* ({sl_loss*usd_to_rub:.0f} ₽)
• Резерв: ${budget_usd - invest:.0f} держи в USDT

⚠️ _Не является финансовым советом. Крипто — высокий риск._"""

    return msg


def format_prices_msg(prices: dict) -> str:
    lines = ["💹 *Текущие цены (Bybit)*\n"]
    for ticker, info in prices.items():
        emoji = "📈" if info["change_24h"] >= 0 else "📉"
        lines.append(f"{emoji} *{ticker}*: {format_price(info['price'])}  {info['change_24h']:+.2f}%")
    lines.append("\n_Данные с Bybit через luna-crypto-api_")
    return "\n".join(lines)


# ── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Мой список монет", callback_data="group_fave")],
        [InlineKeyboardButton("🔥 Топ монеты",       callback_data="group_top"),
         InlineKeyboardButton("⚡ Альткоины",        callback_data="group_alt")],
        [InlineKeyboardButton("🆕 Новинки",          callback_data="group_new")],
        [InlineKeyboardButton("💹 Цены топ-5",       callback_data="prices_top"),
         InlineKeyboardButton("❓ Помощь",            callback_data="help")],
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
        row,
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
    ])

def after_analysis_keyboard(coin: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔄 Обновить {coin}", callback_data=f"analyze_{coin}_60"),
         InlineKeyboardButton("📊 4ч таймфрейм",    callback_data=f"analyze_{coin}_240")],
        [InlineKeyboardButton("📋 Другая монета",   callback_data="back_main"),
         InlineKeyboardButton("💹 Все цены",        callback_data="prices_fave")],
    ])


# ── ХЭНДЛЕРЫ ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "трейдер"
    text = f"""🔮 *Привет, {name}!*

Я *LUNA* — твой персональный аналитик крипторынка на Bybit.

Получаю *реальные данные* прямо с биржи и считаю:
📊 RSI, MACD, EMA50/200, Bollinger Bands
🎯 Точку входа, стоп-лосс и тейк-профит
💰 Прибыль под твой бюджет в рублях

Выбери монету для анализа 👇"""
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Укажи монету. Пример: /analyze ETH")
        return
    coin = args[0].upper()
    interval = args[1] if len(args) > 1 else "60"
    await do_analysis(update, ctx, coin, interval)

async def cmd_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю цены с Bybit...")
    prices = await get_prices(["BTC","ETH","SOL","XRP","BNB","ADA","TON","DOGE"])
    if prices:
        await msg.edit_text(format_prices_msg(prices), parse_mode="Markdown")
    else:
        await msg.edit_text("⚠️ Не удалось получить цены. Попробуй позже.")

async def cmd_fave(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю цены твоих монет...")
    prices = await get_prices(FAVE_COINS[:8])
    if prices:
        await msg.edit_text(
            format_prices_msg(prices),
            parse_mode="Markdown",
            reply_markup=coin_keyboard("⭐ Мой список")
        )
    else:
        await msg.edit_text("⚠️ Ошибка загрузки.", reply_markup=main_keyboard())

async def do_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE, coin: str, interval: str):
    # Определяем куда слать — в сообщение или callback
    if update.message:
        msg = await update.message.reply_text(f"⏳ Анализирую {coin}/USDT с Bybit...")
    else:
        msg = await update.callback_query.message.reply_text(f"⏳ Анализирую {coin}/USDT с Bybit...")

    data = await get_analysis(coin, interval)

    if not data or "signal" not in data:
        await msg.edit_text(f"⚠️ Не удалось получить данные по {coin}. Проверь тикер или попробуй позже.")
        return

    iv_label = {v: k for k, v in INTERVALS.items()}.get(interval, interval)
    text = f"_Таймфрейм: {iv_label} | Данные с Bybit в реальном времени_\n\n" + format_analysis(data)

    await msg.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=after_analysis_keyboard(coin)
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_main":
        await query.message.reply_text("Выбери монету 👇", reply_markup=main_keyboard())

    elif data == "help":
        await query.message.reply_text(
            "📖 Напиши /help для списка всех команд.",
            reply_markup=main_keyboard()
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
                                     reply_markup=main_keyboard())
        else:
            await loading.edit_text("⚠️ Ошибка загрузки цен.", reply_markup=main_keyboard())

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Если пользователь просто написал тикер монеты — анализируем"""
    text = update.message.text.strip().upper()
    # Если это похоже на тикер монеты
    if text.isalpha() and 2 <= len(text) <= 8:
        await do_analysis(update, ctx, text, "60")
    else:
        await update.message.reply_text(
            "Напиши тикер монеты (например *ETH*) или используй меню 👇",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )


# ── ЗАПУСК ───────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",   "Главное меню"),
        BotCommand("analyze", "Анализ монеты — /analyze ETH"),
        BotCommand("prices",  "Текущие цены"),
        BotCommand("fave",    "Мои монеты"),
        BotCommand("help",    "Помощь"),
    ])

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("prices",  cmd_prices))
    app.add_handler(CommandHandler("fave",    cmd_fave))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🔮 LUNA Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
