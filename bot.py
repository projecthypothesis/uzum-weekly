import os
import logging
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Состояния
WAITING_NUMBER = 1
WAITING_TURNOVER = 2
WAITING_AOV = 3

def get_usd_rate_cbu() -> float:
    fallback_rate = 1 / 12950.0
    try:
        url = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"
        response = requests.get(url, timeout=5)
        data = response.json()
        usd_info = next(item for item in data if item.get("Ccy") == "USD")
        rate_uzs = float(usd_info["Rate"])  # сколько сумов за 1 USD
        return 1 / rate_uzs                 # 1 UZS -> USD
    except Exception as e:
        logging.warning("Не удалось получить курс c cbu.uz: %s", e)
        return fallback_rate

def create_chart(
    csv_path: str,
    output_path: str,
    date_format: str = '%Y-%m-%d',  # <-- Указали реальный формат
    convert_currency: bool = False,
    exchange_rate: float = 1.0
):
    """
    - Читаем CSV (Date, Value).
    - Пытаемся парсить дату в формате '%Y-%m-%d'.
      Если при этом ValueError (формат не совпал) — fallback: даём pd.to_datetime без format.
    - Берём только день (df['Day']).
    - Если convert_currency=True, умножаем на exchange_rate (UZS->USD).
    - Рисуем столбцы + линию тренда + % изменения тренда.
    - Сохраняем PNG 550×370 px.
    """
    df = pd.read_csv(csv_path)
    df.columns = ['Date', 'Value']

    # Пробуем жёсткий формат '%Y-%m-%d', иначе fallback
    try:
        df['Date'] = pd.to_datetime(df['Date'], format=date_format)
    except ValueError:
        logging.warning("Дата не соответствует '%Y-%m-%d'. Пробуем угадать автоматически.")
        df['Date'] = pd.to_datetime(df['Date'])  # fallback

    df['Day'] = df['Date'].dt.day

    if convert_currency:
        df['Value'] = (df['Value'] * exchange_rate).round()

    plt.figure(figsize=(5.5, 3.7), dpi=100)
    plt.bar(df['Day'], df['Value'], color='#5B34C1', edgecolor='none', width=0.5)

    x_vals = np.arange(len(df))
    y_vals = df['Value'].values
    coeffs = np.polyfit(x_vals, y_vals, 1)
    trend_poly = np.poly1d(coeffs)
    trendline = trend_poly(x_vals)
    plt.plot(df['Day'], trendline, linestyle='--', color='black')

    trend_start = trend_poly(0)
    trend_end = trend_poly(len(df) - 1)
    if trend_start == 0:
        trend_pct = 0
    else:
        trend_pct = (trend_end - trend_start) / trend_start * 100
    sign = '+' if trend_pct > 0 else ''
    pct_string = f'{sign}{trend_pct:.0f}%'

    ax = plt.gca()
    xpos = df['Day'].iloc[-1]
    ypos = df['Value'].iloc[-1] + df['Value'].max() * 0.03
    plt.text(xpos, ypos, pct_string, color='#5B34C1',
             ha='center', va='bottom', fontweight='bold')

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()

# ====== Хендлеры и ConversationHandler как раньше ======

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я помогу тебе сделать картинки для Weekly отчёта. "
        "Нажми /create_chart, чтобы начать процесс."
    )

async def create_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Пришли CSV-файл для графика Number (Количество переводов).")
    return WAITING_NUMBER

async def handle_number_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document:
        await update.message.reply_text("Это не похоже на документ. Пришли CSV-файл.")
        return WAITING_NUMBER

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    number_path = "number_temp.csv"
    await file.download_to_drive(number_path)
    context.user_data["number_csv"] = number_path

    await update.message.reply_text("Отлично! Теперь пришли CSV-файл для Turnover (Оборот).")
    return WAITING_TURNOVER

async def handle_turnover_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document:
        await update.message.reply_text("Это не похоже на документ. Пришли CSV-файл.")
        return WAITING_TURNOVER

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    turnover_path = "turnover_temp.csv"
    await file.download_to_drive(turnover_path)
    context.user_data["turnover_csv"] = turnover_path

    await update.message.reply_text("Отлично! Теперь пришли CSV-файл для AOV (Средняя сумма).")
    return WAITING_AOV

async def handle_aov_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document:
        await update.message.reply_text("Это не похоже на документ. Пришли CSV-файл.")
        return WAITING_AOV

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    aov_path = "aov_temp.csv"
    await file.download_to_drive(aov_path)
    context.user_data["aov_csv"] = aov_path

    await update.message.reply_text("Супер, все три файла получены! Строю графики...")
    await build_and_send_charts(update, context)

    return ConversationHandler.END

async def build_and_send_charts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    number_csv = context.user_data["number_csv"]
    turnover_csv = context.user_data["turnover_csv"]
    aov_csv = context.user_data["aov_csv"]

    chart_number = "chart_number.png"
    chart_turnover = "chart_turnover.png"
    chart_aov = "chart_aov.png"

    rate = get_usd_rate_cbu()

    # Унифицируем: все три вызываем с date_format='%Y-%m-%d', fallback если не совпадёт
    create_chart(
        csv_path=number_csv,
        output_path=chart_number,
        date_format='%Y-%m-%d',   # <== Парсим как год-месяц-день, или fallback
        convert_currency=False,
        exchange_rate=1.0
    )

    create_chart(
        csv_path=turnover_csv,
        output_path=chart_turnover,
        date_format='%Y-%m-%d',
        convert_currency=True,
        exchange_rate=rate
    )

    create_chart(
        csv_path=aov_csv,
        output_path=chart_aov,
        date_format='%Y-%m-%d',
        convert_currency=True,
        exchange_rate=rate
    )

    await update.message.reply_text("Готово! Отправляю три графика...")
    chat_id = update.effective_chat.id
    await context.bot.send_document(chat_id=chat_id, document=open(chart_number, 'rb'), filename="chart_number.png")
    await context.bot.send_document(chat_id=chat_id, document=open(chart_turnover, 'rb'), filename="chart_turnover.png")
    await context.bot.send_document(chat_id=chat_id, document=open(chart_aov, 'rb'), filename="chart_aov.png")

    await update.message.reply_text("Все графики отправлены!")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

def main() -> None:
    load_dotenv()
    TOKEN = os.getenv('TOKEN')

    application = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("create_chart", create_chart_command)],
        states={
            WAITING_NUMBER: [MessageHandler(filters.Document.ALL, handle_number_file)],
            WAITING_TURNOVER: [MessageHandler(filters.Document.ALL, handle_turnover_file)],
            WAITING_AOV: [MessageHandler(filters.Document.ALL, handle_aov_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    application.run_polling()
    logging.info("Bot stopped.")

if __name__ == "__main__":
    main()
