import os
import logging
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import sys
import openpyxl

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Константы для кнопок
P2P_PHONE = "P2P по номеру телефона"
DEBIT_CARD = "Дебетовая карта"
CUSTOM_CHART = "Свой график"
CANCEL_BUTTON = "Отменить"
VIRTUAL_CARD = "Виртуальная карта"
PLASTIC_CARD = "Пластиковая карта"

# Константы для состояний пользователя
STATE_KEY = "state"
PRODUCT_KEY = "product"

# P2P-сценарий: нужно 3 CSV
NUMBER_CSV_KEY = "number_csv"
TURNOVER_CSV_KEY = "turnover_csv"
AOV_CSV_KEY = "aov_csv"

# DEBIT-сценарий: нужно 2 XLSX (виртуалка и пластик)
DEBIT_VIRT_KEY = "debit_virt_xlsx"     # Виртуалка
DEBIT_PLASTIC_KEY = "debit_plastic_xlsx"  # Пластик

# Состояния
CHOOSING_PRODUCT = "choosing_product"
CHOOSING_DEBIT_TYPE = "choosing_debit_type"

# P2P flow
WAITING_NUMBER = "waiting_number"
WAITING_TURNOVER = "waiting_turnover"
WAITING_AOV = "waiting_aov"

# DEBIT flow
WAITING_DEBIT_VIRT = "waiting_debit_virt"
WAITING_DEBIT_PLASTIC = "waiting_debit_plastic"

# CUSTOM CHART flow
WAITING_DATES = "waiting_dates"
WAITING_VALUES = "waiting_values"

# Ключи для сохранения текстовых данных для "Свой график"
CUSTOM_DATES_KEY = "custom_dates"
CUSTOM_VALUES_KEY = "custom_values"

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


def read_dates_column(df, date_col='Date', date_format='%Y-%m-%d'):
    """
    Универсальная функция: пытается распарсить столбец df[date_col]
    форматом date_format, если не вышло -> fallback pd.to_datetime без format.
    """
    try:
        df[date_col] = pd.to_datetime(df[date_col], format=date_format)
    except ValueError:
        logging.warning(f"Дата не соответствует '{date_format}'. Пробуем автоматически.")
        df[date_col] = pd.to_datetime(df[date_col])
    return df


def create_chart_single_series(
    xlsx_path: str,
    output_path: str,
    date_col='Date',
    val_col='Value',
    date_format='%Y-%m-%d',
    convert_currency: bool=False,
    exchange_rate: float=1.0,
    color_bar='#5B34C1',
    label_for_percent=None
):
    """
    Обычный "односерийный" график (как для Number или виртуалки).
    - Читаем XLSX (или CSV), ожидая 2 колонки: date_col, val_col
    - Парсим даты
    - Столбцы (color_bar), линия тренда без процента изменения тренда
    - Сохраняем PNG размером 525x310
    """
    try:
        # Проверяем существование файла
        if not os.path.exists(xlsx_path):
            logging.error(f"Файл не найден: {xlsx_path}")
            return None, f"Файл не найден: {xlsx_path}"
            
        # Пробуем прочитать файл
        try:
            # Сначала попробуем прочитать с заголовками
            df = pd.read_excel(xlsx_path)
            logging.info(f"Прочитан файл {xlsx_path}, колонки: {df.columns.tolist()}")
            
            # Проверяем количество колонок
            if len(df.columns) < 2:
                logging.error(f"Недостаточно колонок в файле. Ожидается минимум 2, получено {len(df.columns)}")
                return None, f"Неверный формат файла: ожидается минимум 2 колонки, получено {len(df.columns)}"
            
            # Если первая строка содержит даты, возможно, у нас нет заголовков
            if isinstance(df.iloc[0, 0], (pd.Timestamp, np.datetime64)) or str(df.iloc[0, 0]).startswith('20'):
                # Файл без заголовков, первая строка - это данные
                logging.info("Файл без заголовков, используем первые две колонки как дату и значение")
                df.columns = [f"col{i}" for i in range(len(df.columns))]
                date_col_actual = "col0"
                val_col_actual = "col1"
            else:
                # У файла есть заголовки, пытаемся найти колонки с датой и значением
                if 'Date' in df.columns or 'Дата' in df.columns:
                    date_col_actual = 'Date' if 'Date' in df.columns else 'Дата'
                else:
                    date_col_actual = df.columns[0]  # Берем первую колонку как дату
                
                # Для значения берем вторую колонку
                val_col_actual = df.columns[1]
                
            logging.info(f"Используем колонки: {date_col_actual} (дата) и {val_col_actual} (значение)")
            
            # Преобразуем даты
            df = read_dates_column(df, date_col=date_col_actual, date_format=date_format)
            
            if convert_currency:
                df[val_col_actual] = (df[val_col_actual] * exchange_rate).round()
            
            # Берём день и месяц для формата "dd.mm"
            df['Day'] = df[date_col_actual].dt.day
            df['Month'] = df[date_col_actual].dt.month
            df['Label'] = df['Day'].apply(lambda x: f"{x:02d}") + '.' + df['Month'].apply(lambda x: f"{x:02d}")
            
            # Новый размер 848x502
            plt.figure(figsize=(8.48, 5.02), dpi=100)
            plt.bar(df['Label'], df[val_col_actual], color=color_bar, edgecolor='none', width=0.5)
            
            x_vals = np.arange(len(df))
            y_vals = df[val_col_actual].values
            coeffs = np.polyfit(x_vals, y_vals, 1)
            trend_poly = np.poly1d(coeffs)
            trendline = trend_poly(x_vals)
            plt.plot(df['Label'], trendline, linestyle='--', color='black')
            
            # Если много дат, поворачиваем подписи
            if len(df) > 7:
                plt.xticks(rotation=45, ha='right')
            
            ax = plt.gca()
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.tick_params(left=False)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=100)
            plt.close()
            
            return True, None
            
        except ImportError:
            logging.error("Не удалось прочитать Excel-файл. Отсутствует библиотека openpyxl.")
            return None, "Отсутствует библиотека openpyxl"
        except Exception as e:
            logging.error(f"Ошибка при чтении Excel-файла: {e}")
            return None, f"Ошибка при чтении файла: {str(e)}"
            
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при создании графика: {e}")
        return None, f"Непредвиденная ошибка: {str(e)}"


def create_chart_two_series(
    xlsx_path: str,
    output_path: str,
    date_col='Date',
    col1='Ordered',
    col2='Issued',
    date_format='%Y-%m-%d',
    color1='#5B34C1',
    color2='#FF259E',
):
    """
    Групповой бар-чарт для пластика: 2 столбца на каждую дату
    + 2 линии тренда без подписей % роста
    + Легенда снизу с квадратными маркерами
    Ожидаем, что XLSX имеет колонки [Date, Ordered, Issued] (или иные имена).
    """
    try:
        # Проверяем существование файла
        if not os.path.exists(xlsx_path):
            logging.error(f"Файл не найден: {xlsx_path}")
            return None, f"Файл не найден: {xlsx_path}"
            
        # Пробуем прочитать файл
        try:
            df = pd.read_excel(xlsx_path)
            logging.info(f"Прочитан файл {xlsx_path}, колонки: {df.columns.tolist()}")
            
            # Проверяем количество колонок
            if len(df.columns) < 3:
                logging.error(f"Недостаточно колонок в файле. Ожидается минимум 3, получено {len(df.columns)}")
                return None, f"Неверный формат файла: ожидается минимум 3 колонки, получено {len(df.columns)}"
            
            # Определяем колонки
            if 'Date' in df.columns or 'Дата' in df.columns:
                date_col_actual = 'Date' if 'Date' in df.columns else 'Дата'
            else:
                date_col_actual = df.columns[0]  # Берем первую колонку как дату
            
            # Для значений берем вторую и третью колонки
            col1_actual = df.columns[1]
            col2_actual = df.columns[2]
            
            logging.info(f"Используем колонки: {date_col_actual} (дата), {col1_actual} (заказанные), {col2_actual} (выданные)")
            
            # Преобразуем даты
            df = read_dates_column(df, date_col=date_col_actual, date_format=date_format)
            
            # Берём день и месяц для формата "dd.mm"
            df['Day'] = df[date_col_actual].dt.day
            df['Month'] = df[date_col_actual].dt.month
            df['Label'] = df['Day'].apply(lambda x: f"{x:02d}") + '.' + df['Month'].apply(lambda x: f"{x:02d}")
            
            x_vals = np.arange(len(df))  # 0..n-1 для polyfit
            # Для построения grouped bars, смещение
            bar_width = 0.4
            
            # Фигура с новым размером 848x502
            plt.figure(figsize=(8.48, 5.02), dpi=100)
            
            # Рисуем 2 столбца на каждую дату (со смещением)
            plt.bar(np.arange(len(df)) - bar_width/2, df[col1_actual], color=color1, width=bar_width, label="заказанные")
            plt.bar(np.arange(len(df)) + bar_width/2, df[col2_actual], color=color2, width=bar_width, label="выданные")
            
            # Линия тренда для col1 (фиолетовая)
            y1 = df[col1_actual].values
            coeffs1 = np.polyfit(x_vals, y1, 1)
            trend_poly1 = np.poly1d(coeffs1)
            trendline1 = trend_poly1(x_vals)
            # Рисуем линию тренда черным цветом
            plt.plot(x_vals, trendline1, linestyle='--', color='black')
            
            # Линия тренда для col2 (розовая)
            y2 = df[col2_actual].values
            coeffs2 = np.polyfit(x_vals, y2, 1)
            trend_poly2 = np.poly1d(coeffs2)
            trendline2 = trend_poly2(x_vals)
            # Рисуем линию тренда розовым цветом (как столбцы)
            plt.plot(x_vals, trendline2, linestyle='--', color=color2)
            
            # Устанавливаем метки с правильным форматом даты
            plt.xticks(np.arange(len(df)), df['Label'])
            
            # Если слишком много дат, поворачиваем подписи
            if len(df) > 7:
                plt.xticks(rotation=45, ha='right')
            
            # Убираем рамки
            ax = plt.gca()
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.tick_params(left=False)
            
            # Создаем квадратные маркеры для легенды
            from matplotlib.patches import Rectangle
            legend_elements = [
                Rectangle((0, 0), width=1, height=1, facecolor=color1, edgecolor='none', label='заказанные'),
                Rectangle((0, 0), width=1, height=1, facecolor=color2, edgecolor='none', label='выданные')
            ]
            
            # Настраиваем легенду с квадратными маркерами
            plt.legend(
                handles=legend_elements,
                loc='upper center', 
                bbox_to_anchor=(0.5, -0.16),
                ncol=2,
                frameon=False,
                handletextpad=0.5,
                columnspacing=1.0,
                # Делаем размер маркеров одинаковым
                handlelength=1.5,
                handleheight=1.5
            )
            
            plt.tight_layout(pad=2.0)  # Увеличиваем отступы для легенды
            plt.savefig(output_path, dpi=100, bbox_inches='tight')  # Сохраняем с учетом легенды
            plt.close()
            
            return True, None
            
        except ImportError:
            logging.error("Не удалось прочитать Excel-файл. Отсутствует библиотека openpyxl.")
            return None, "Отсутствует библиотека openpyxl"
        except Exception as e:
            logging.error(f"Ошибка при чтении Excel-файла: {e}")
            return None, f"Ошибка при чтении файла: {str(e)}"
            
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при создании графика: {e}")
        return None, f"Непредвиденная ошибка: {str(e)}"


async def show_product_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает клавиатуру с выбором продукта"""
    keyboard = [
        [DEBIT_CARD, P2P_PHONE],
        [CUSTOM_CHART]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📊 Выбери продукт, для которого нужно создать графики:",
        reply_markup=reply_markup
    )
    context.user_data[STATE_KEY] = CHOOSING_PRODUCT

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало работы с ботом"""
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе создать графики для еженедельного отчёта.\n"
    )
    await show_product_selection(update, context)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /cancel и кнопки Отменить"""
    await update.message.reply_text(
        "❌ Операция отменена. Начинаем заново.", 
        reply_markup=ReplyKeyboardRemove()
    )
    await show_product_selection(update, context)

async def build_and_send_charts_p2p(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Старый сценарий: 3 CSV (Number, Turnover, AOV).
    """
    number_csv = context.user_data[NUMBER_CSV_KEY]
    turnover_csv = context.user_data[TURNOVER_CSV_KEY]
    aov_csv = context.user_data[AOV_CSV_KEY]

    chart_number = "chart_number.png"
    chart_turnover = "chart_turnover.png"
    chart_aov = "chart_aov.png"

    rate = get_usd_rate_cbu()

    await update.message.reply_text("⏳ Создаю графики P2P (Number/Turnover/AOV)...")

    # Можно переиспользовать "single series" логику, но тут CSV, не XLSX -> 
    # Оставим старый вариант, если CSV форматы не поменялись
    # Для простоты: read_csv + fallback. (Или вы по-прежнему date_format='%Y-%m-%d')

    # ... (ниже — ваш старый create_chart или аналог)
    # Здесь, чтобы не расписывать всё заново, сделаем упрощённо:
    # Если нужно — замените на полноценную функцию, как раньше.

    # 1) Number
    create_chart_for_p2p_csv(
        csv_path=number_csv,
        output_path=chart_number,
        date_format='%Y-%m-%d',
        convert_currency=False,
        exchange_rate=1.0
    )

    # 2) Turnover
    create_chart_for_p2p_csv(
        csv_path=turnover_csv,
        output_path=chart_turnover,
        date_format='%Y-%m-%d',
        convert_currency=True,
        exchange_rate=rate
    )

    # 3) AOV
    create_chart_for_p2p_csv(
        csv_path=aov_csv,
        output_path=chart_aov,
        date_format='%Y-%m-%d',
        convert_currency=True,
        exchange_rate=rate
    )

    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ Графики P2P готовы! Отправляю...")

    await context.bot.send_document(chat_id=chat_id, document=open(chart_number, 'rb'), filename=chart_number)
    await context.bot.send_document(chat_id=chat_id, document=open(chart_turnover, 'rb'), filename=chart_turnover)
    await context.bot.send_document(chat_id=chat_id, document=open(chart_aov, 'rb'), filename=chart_aov)

    await update.message.reply_text("🎉 Готово! Хочешь создать графики для другого продукта?")
    await show_product_selection(update, context)


def create_chart_for_p2p_csv(csv_path, output_path, date_format, convert_currency, exchange_rate):
    """
    Упрощённая функция для P2P, читающая CSV. 
    Логика аналогична, как было: single series, color #5B34C1, 
    плюс тренд без процента прироста.
    """
    df = pd.read_csv(csv_path)
    df.columns = ['Date', 'Value']
    try:
        df['Date'] = pd.to_datetime(df['Date'], format=date_format)
    except ValueError:
        df['Date'] = pd.to_datetime(df['Date'])
    if convert_currency:
        df['Value'] = (df['Value'] * exchange_rate).round()
    df['Day'] = df['Date'].dt.day
    df['Month'] = df['Date'].dt.month
    df['Label'] = df['Day'].apply(lambda x: f"{x:02d}") + '.' + df['Month'].apply(lambda x: f"{x:02d}")

    # Новый размер 525x310 (как в других графиках)
    plt.figure(figsize=(5.25, 3.1), dpi=100)
    plt.bar(df['Label'], df['Value'], color='#5B34C1', edgecolor='none', width=0.5)

    x_vals = np.arange(len(df))
    y_vals = df['Value'].values
    coeffs = np.polyfit(x_vals, y_vals, 1)
    trend_poly = np.poly1d(coeffs)
    trendline = trend_poly(x_vals)
    plt.plot(df['Label'], trendline, linestyle='--', color='black')

    # Если много дат, поворачиваем подписи
    if len(df) > 7:
        plt.xticks(rotation=45, ha='right')

    ax = plt.gca()
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()


async def build_and_send_charts_debit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Для дебетовой карты: 
    1) Виртуальная карта (одна серия, xlsx), 
    2) Пластиковая карта (две серии, xlsx).
    """
    virt_xlsx = context.user_data[DEBIT_VIRT_KEY]
    plast_xlsx = context.user_data[DEBIT_PLASTIC_KEY]

    chart_virt = "virt_card_chart.png"
    chart_plast = "plastic_card_chart.png"

    rate = get_usd_rate_cbu()

    await update.message.reply_text("⏳ Создаю графики для дебетовой карты...")

    # 1) Виртуалка: single series (фиолетовый #5B34C1)
    result_virt, error_virt = create_chart_single_series(
        xlsx_path=virt_xlsx,
        output_path=chart_virt,
        date_format='%Y-%m-%d',
        convert_currency=False,        # по аналогии с "Number"
        exchange_rate=1.0,
        color_bar='#5B34C1',
        label_for_percent="Виртуалка"
    )
    
    if result_virt is None:
        await update.message.reply_text(f"❌ Ошибка при обработке файла виртуальной карты: {error_virt}")
        return

    # 2) Пластик: два столбца ("Заказанные", "Выданные")
    result_plast, error_plast = create_chart_two_series(
        xlsx_path=plast_xlsx,
        output_path=chart_plast,
        date_format='%Y-%m-%d',
        color1='#5B34C1',  # "Заказанные"
        color2='#FF259E',  # "Выданные"
    )
    
    if result_plast is None:
        await update.message.reply_text(f"❌ Ошибка при обработке файла пластиковой карты: {error_plast}")
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ Графики дебетовой карты готовы! Отправляю...")

    # Отправляем
    await context.bot.send_document(
        chat_id=chat_id,
        document=open(chart_virt, 'rb'),
        filename=chart_virt,
        caption="Виртуальная карта (один столбец)"
    )

    await context.bot.send_document(
        chat_id=chat_id,
        document=open(chart_plast, 'rb'),
        filename=chart_plast,
        caption="Пластиковая карта: Заказанные и Выданные"
    )

    await update.message.reply_text("🎉 Готово! Хочешь создать графики для другого продукта?")
    await show_product_selection(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Основной обработчик сообщений, маршрутизирует в зависимости от состояния"""
    state = context.user_data.get(STATE_KEY, CHOOSING_PRODUCT)
    user_text = update.message.text

    # Проверяем, не нажата ли кнопка "Отменить"
    if user_text == CANCEL_BUTTON:
        return await cancel_command(update, context)

    # Сценарий: если мы в выборе продукта
    if state == CHOOSING_PRODUCT:
        await handle_product_choice(update, context)
        return

    # Если выбираем тип дебетовой карты
    if state == CHOOSING_DEBIT_TYPE:
        await handle_product_choice(update, context)
        return

    product = context.user_data.get(PRODUCT_KEY, None)

    # Если выбрали P2P
    if product == P2P_PHONE:
        if state == WAITING_NUMBER:
            await handle_number_file(update, context)
        elif state == WAITING_TURNOVER:
            await handle_turnover_file(update, context)
        elif state == WAITING_AOV:
            await handle_aov_file(update, context)
        else:
            await show_product_selection(update, context)

    # Если выбрали Debit
    elif product == DEBIT_CARD:
        if state == WAITING_DEBIT_VIRT:
            await handle_debit_virt_file(update, context)
        elif state == WAITING_DEBIT_PLASTIC:
            await handle_debit_plastic_file(update, context)
        else:
            await show_product_selection(update, context)

    # Если выбрали Custom Chart
    elif product == CUSTOM_CHART:
        if state == WAITING_DATES:
            await handle_dates_text(update, context)
        elif state == WAITING_VALUES:
            await handle_values_text(update, context)
        else:
            await show_product_selection(update, context)

    else:
        # Нет продукта — возвращаемся в меню
        await show_product_selection(update, context)

async def handle_product_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор продукта пользователем"""
    user_choice = update.message.text
    
    if user_choice == P2P_PHONE:
        context.user_data[PRODUCT_KEY] = P2P_PHONE
        # Готовим флоу на 3 CSV
        keyboard = [[CANCEL_BUTTON]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ Ты выбрал P2P по номеру телефона\n"
            "📎 Пришли CSV-файл для графика Number (количество переводов).",
            reply_markup=reply_markup
        )
        context.user_data[STATE_KEY] = WAITING_NUMBER

    elif user_choice == DEBIT_CARD:
        context.user_data[PRODUCT_KEY] = DEBIT_CARD
        keyboard = [[VIRTUAL_CARD, PLASTIC_CARD], [CANCEL_BUTTON]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ Ты выбрал «Дебетовая карта»\n\n"
            "Выбери тип карты, для которой хочешь создать отчёт:",
            reply_markup=reply_markup
        )
        context.user_data[STATE_KEY] = CHOOSING_DEBIT_TYPE

    elif user_choice == CUSTOM_CHART:
        context.user_data[PRODUCT_KEY] = CUSTOM_CHART
        keyboard = [[CANCEL_BUTTON]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ Ты выбрал «Свой график»\n\n"
            "Пришли список дат в одном сообщении, разделённых пробелами.\n"
            "Формат даты: ДД.ММ.ГГГГ\n"
            "Пример: 28.02.2025 01.03.2025 02.03.2025 03.03.2025",
            reply_markup=reply_markup
        )
        context.user_data[STATE_KEY] = WAITING_DATES
    
    elif user_choice == VIRTUAL_CARD and context.user_data.get(STATE_KEY) == CHOOSING_DEBIT_TYPE:
        keyboard = [[CANCEL_BUTTON]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ Ты выбрал «Виртуальная карта»\n\n"
            "Пришли XLSX-файл с данными по виртуальной карте.\n"
            "Ожидаем формат: [Date, Value].\n"
            "Пример: 2025-03-01, 1123",
            reply_markup=reply_markup
        )
        context.user_data[STATE_KEY] = WAITING_DEBIT_VIRT

    elif user_choice == PLASTIC_CARD and context.user_data.get(STATE_KEY) == CHOOSING_DEBIT_TYPE:
        keyboard = [[CANCEL_BUTTON]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ Ты выбрал «Пластиковая карта»\n\n"
            "Пришли XLSX-файл с данными по пластиковой карте.\n"
            "Ожидаем формат: [Date, Заказанные, Выданные].",
            reply_markup=reply_markup
        )
        context.user_data[STATE_KEY] = WAITING_DEBIT_PLASTIC

    else:
        # Ничего не выбрано
        await update.message.reply_text(
            "❓ Пожалуйста, выбери один из доступных продуктов, используя кнопки на клавиатуре."
        )

# ==== P2P HANDLERS ====
async def handle_number_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает загрузку Number.csv"""
    if not update.message.document:
        await update.message.reply_text(
            "❌ Это не документ. Пришли CSV-файл для Number.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        return

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    number_path = "number_temp.csv"
    await file.download_to_drive(number_path)
    context.user_data[NUMBER_CSV_KEY] = number_path

    await update.message.reply_text(
        "✅ Файл Number загружен!\n\n"
        "Теперь пришли CSV-файл Turnover.",
        reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
    )
    context.user_data[STATE_KEY] = WAITING_TURNOVER

async def handle_turnover_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает загрузку Turnover.csv"""
    if not update.message.document:
        await update.message.reply_text(
            "❌ Это не документ. Пришли CSV-файл для Turnover.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        return

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    turnover_path = "turnover_temp.csv"
    await file.download_to_drive(turnover_path)
    context.user_data[TURNOVER_CSV_KEY] = turnover_path

    await update.message.reply_text(
        "✅ Файл Turnover загружен!\n\n"
        "Теперь пришли CSV-файл AOV.",
        reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
    )
    context.user_data[STATE_KEY] = WAITING_AOV

async def handle_aov_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает загрузку AOV.csv"""
    if not update.message.document:
        await update.message.reply_text(
            "❌ Это не документ. Пришли CSV-файл для AOV.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        return

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    aov_path = "aov_temp.csv"
    await file.download_to_drive(aov_path)
    context.user_data[AOV_CSV_KEY] = aov_path

    await update.message.reply_text("✅ Все файлы P2P (Number, Turnover, AOV) получены!")
    await build_and_send_charts_p2p(update, context)

# ==== DEBIT HANDLERS ====
async def handle_debit_virt_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получаем XLSX для виртуальной карты"""
    if not update.message.document:
        await update.message.reply_text(
            "❌ Это не документ. Пришли XLSX-файл для виртуальной карты.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        return
    
    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    virt_path = "debit_virt.xlsx"
    await file.download_to_drive(virt_path)
    context.user_data[DEBIT_VIRT_KEY] = virt_path

    # Создаем отчет только для виртуальной карты
    await build_and_send_virt_chart(update, context)

async def handle_debit_plastic_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получаем XLSX для пластиковой карты (2 столбца)"""
    if not update.message.document:
        await update.message.reply_text(
            "❌ Это не документ. Пришли XLSX-файл для пластиковой карты.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        return

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)

    plast_path = "debit_plastic.xlsx"
    await file.download_to_drive(plast_path)
    context.user_data[DEBIT_PLASTIC_KEY] = plast_path

    # Создаем отчет только для пластиковой карты
    await build_and_send_plastic_chart(update, context)

async def build_and_send_virt_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Создает и отправляет только график для виртуальной карты"""
    virt_xlsx = context.user_data[DEBIT_VIRT_KEY]
    chart_virt = "virt_card_chart.png"
    
    await update.message.reply_text("⏳ Создаю график для виртуальной карты...")

    result_virt, error_virt = create_chart_single_series(
        xlsx_path=virt_xlsx,
        output_path=chart_virt,
        date_format='%Y-%m-%d',
        convert_currency=False,
        exchange_rate=1.0,
        color_bar='#5B34C1',
        label_for_percent="Виртуалка"
    )
    
    if result_virt is None:
        await update.message.reply_text(f"❌ Ошибка при обработке файла виртуальной карты: {error_virt}")
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ График виртуальной карты готов! Отправляю...")

    # Отправляем
    await context.bot.send_document(
        chat_id=chat_id,
        document=open(chart_virt, 'rb'),
        filename=chart_virt,
        caption="Виртуальная карта (один столбец)"
    )

    await update.message.reply_text("🎉 Готово! Хочешь создать другой график?")
    await show_product_selection(update, context)

async def build_and_send_plastic_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Создает и отправляет только график для пластиковой карты"""
    plast_xlsx = context.user_data[DEBIT_PLASTIC_KEY]
    chart_plast = "plastic_card_chart.png"
    
    await update.message.reply_text("⏳ Создаю график для пластиковой карты...")

    result_plast, error_plast = create_chart_two_series(
        xlsx_path=plast_xlsx,
        output_path=chart_plast,
        date_format='%Y-%m-%d',
        color1='#5B34C1',  # "Заказанные"
        color2='#FF259E',  # "Выданные"
    )
    
    if result_plast is None:
        await update.message.reply_text(f"❌ Ошибка при обработке файла пластиковой карты: {error_plast}")
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ График пластиковой карты готов! Отправляю...")

    # Отправляем
    await context.bot.send_document(
        chat_id=chat_id,
        document=open(chart_plast, 'rb'),
        filename=chart_plast,
        caption="Пластиковая карта: Заказанные и Выданные"
    )

    await update.message.reply_text("🎉 Готово! Хочешь создать другой график?")
    await show_product_selection(update, context)

# ==== CUSTOM CHART HANDLERS ====
async def handle_dates_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает сообщение с датами для построения собственного графика"""
    try:
        dates_text = update.message.text
        dates_list = dates_text.strip().split()
        
        if not dates_list:
            await update.message.reply_text(
                "❌ Не удалось распознать даты. Пожалуйста, убедись, что даты разделены пробелами.",
                reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
            )
            return
        
        # Сохраняем даты в контексте
        context.user_data[CUSTOM_DATES_KEY] = dates_list
        
        await update.message.reply_text(
            "✅ Даты получены!\n\n"
            "Теперь пришли значения (числа, разделённые пробелами).\n"
            "Количество значений должно совпадать с количеством дат.",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )
        context.user_data[STATE_KEY] = WAITING_VALUES
        
    except Exception as e:
        logging.error(f"Ошибка при обработке дат: {e}")
        await update.message.reply_text(
            f"❌ Произошла ошибка: {str(e)}",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )

async def handle_values_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает сообщение со значениями для построения собственного графика"""
    try:
        values_text = update.message.text
        values_list = values_text.strip().split()
        
        if not values_list:
            await update.message.reply_text(
                "❌ Не удалось распознать значения. Пожалуйста, убедись, что значения разделены пробелами.",
                reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
            )
            return
        
        # Преобразуем значения в числа
        try:
            values_list = [float(val) for val in values_list]
        except ValueError:
            await update.message.reply_text(
                "❌ Некоторые значения не являются числами. Пожалуйста, проверь введённые данные.",
                reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
            )
            return
        
        # Проверяем, что количество значений совпадает с количеством дат
        dates_list = context.user_data.get(CUSTOM_DATES_KEY, [])
        if len(values_list) != len(dates_list):
            await update.message.reply_text(
                f"❌ Количество значений ({len(values_list)}) не совпадает с количеством дат ({len(dates_list)}).",
                reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
            )
            return
        
        # Сохраняем значения в контексте
        context.user_data[CUSTOM_VALUES_KEY] = values_list
        
        await update.message.reply_text("✅ Значения получены! Создаю график...")
        await build_custom_chart(update, context)
        
    except Exception as e:
        logging.error(f"Ошибка при обработке значений: {e}")
        await update.message.reply_text(
            f"❌ Произошла ошибка: {str(e)}",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )

async def build_custom_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Создаёт и отправляет график на основе пользовательских данных"""
    try:
        dates_list = context.user_data.get(CUSTOM_DATES_KEY, [])
        values_list = context.user_data.get(CUSTOM_VALUES_KEY, [])
        
        # Создаём DataFrame из данных
        data = {
            'Date': dates_list,
            'Value': values_list
        }
        
        # Создаём временный CSV-файл для построения графика
        custom_csv_path = "custom_chart_data.csv"
        df = pd.DataFrame(data)
        
        # Конвертируем даты в правильный формат
        try:
            # Пробуем разные форматы дат
            for date_format in ['%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y']:
                try:
                    df['Date'] = pd.to_datetime(df['Date'], format=date_format)
                    break
                except:
                    continue
            
            # Если ни один формат не подошёл, пробуем без формата
            if not pd.api.types.is_datetime64_dtype(df['Date']):
                df['Date'] = pd.to_datetime(df['Date'])
        except Exception as e:
            logging.error(f"Ошибка при конвертации дат: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при обработке дат: {str(e)}",
                reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
            )
            return
        
        # Сортируем DataFrame по датам
        df = df.sort_values('Date')
        
        # Создаём файл с графиком
        custom_chart_path = "custom_chart.png"
        
        # Используем функцию create_chart_for_p2p_csv для создания графика
        create_custom_chart_from_data(
            df=df,
            output_path=custom_chart_path
        )
        
        # Отправляем график пользователю
        chat_id = update.effective_chat.id
        await update.message.reply_text("✅ График готов! Отправляю...")
        
        await context.bot.send_document(
            chat_id=chat_id,
            document=open(custom_chart_path, 'rb'),
            filename=custom_chart_path,
            caption="Пользовательский график"
        )
        
        await update.message.reply_text("🎉 Готово! Хочешь создать ещё один график?")
        await show_product_selection(update, context)
        
    except Exception as e:
        logging.error(f"Ошибка при создании графика: {e}")
        await update.message.reply_text(
            f"❌ Произошла ошибка при создании графика: {str(e)}",
            reply_markup=ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True)
        )

def create_custom_chart_from_data(df, output_path):
    """Создаёт график на основе DataFrame с данными"""
    try:
        # Добавляем столбец с днями для оси X
        df['Day'] = df['Date'].dt.day
        df['Month'] = df['Date'].dt.month
        
        # Создаём подписи дат в формате "день.месяц"
        df['Label'] = df['Day'].apply(lambda x: f"{x:02d}") + '.' + df['Month'].apply(lambda x: f"{x:02d}")
        
        # Создаём график с размером 525x310 пикселей
        plt.figure(figsize=(5.25, 3.1), dpi=100)
        plt.bar(df['Label'], df['Value'], color='#5B34C1', edgecolor='none', width=0.5)
        
        # Добавляем линию тренда
        x_vals = np.arange(len(df))
        y_vals = df['Value'].values
        coeffs = np.polyfit(x_vals, y_vals, 1)
        trend_poly = np.poly1d(coeffs)
        trendline = trend_poly(x_vals)
        plt.plot(df['Label'], trendline, linestyle='--', color='black')
        
        # Убираем рамки
        ax = plt.gca()
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(left=False)
        
        # Если слишком много дат, поворачиваем подписи
        if len(df) > 7:
            plt.xticks(rotation=45, ha='right')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=100)
        plt.close()
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при создании графика: {e}")
        raise

def main() -> None:
    load_dotenv()
    TOKEN = os.getenv('TOKEN')

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Основной обработчик сообщений
    application.add_handler(MessageHandler(
        filters.TEXT | filters.Document.ALL,
        handle_message
    ))

    application.run_polling()
    logging.info("Bot stopped.")


if __name__ == "__main__":
    main()
