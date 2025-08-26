> Мария:
import logging
import requests
from aiogram import Bot, Dispatcher, executor, types
import config

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.TELEGRAM_TOKEN)
dp = Dispatcher(bot)

# Временное хранилище данных маршрута
user_data = {}

# --- Геокодинг через Nominatim ---
def geocode(city: str):
    url = f"https://nominatim.openstreetmap.org/search"
    params = {"q": city, "format": "json", "limit": 1}
    r = requests.get(url, params=params)
    if r.ok and r.json():
        return float(r.json()[0]["lat"]), float(r.json()[0]["lon"])
    return None, None

# --- Построение маршрута через OpenRouteService ---
def get_route(start, end, api_key):
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": api_key}
    params = {
        "start": f"{start[1]},{start[0]}",
        "end": f"{end[1]},{end[0]}"
    }
    r = requests.get(url, headers=headers, params=params)
    return r.json() if r.ok else None

# --- Поиск объектов через OpenTripMap ---
def get_places(lat, lon, radius, kinds, api_key, limit=5):
    url = "https://api.opentripmap.com/0.1/en/places/radius"
    params = {
        "apikey": api_key,
        "radius": radius,
        "lon": lon,
        "lat": lat,
        "limit": limit,
        "kinds": kinds
    }
    r = requests.get(url, params=params)
    return r.json()["features"] if r.ok else []

# --- Команды бота ---
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer("Привет! 🚗 Я помогу спланировать маршрут.\n"
                         "Введи город отправления:")

@dp.message_handler(lambda m: "город" not in user_data.get(m.from_user.id, {}))
async def set_start_city(message: types.Message):
    lat, lon = geocode(message.text)
    if not lat:
        await message.answer("❌ Не удалось найти этот город, попробуй снова.")
        return
    user_data[message.from_user.id] = {"start_city": message.text, "start_coords": (lat, lon)}
    await message.answer("Отлично! Теперь введи город назначения:")

@dp.message_handler(lambda m: "end_city" not in user_data.get(m.from_user.id, {}))
async def set_end_city(message: types.Message):
    lat, lon = geocode(message.text)
    if not lat:
        await message.answer("❌ Не удалось найти этот город, попробуй снова.")
        return
    user_data[message.from_user.id]["end_city"] = message.text
    user_data[message.from_user.id]["end_coords"] = (lat, lon)
    await message.answer("Теперь введи средний расход топлива (л/100 км):")

@dp.message_handler(lambda m: "fuel" not in user_data.get(m.from_user.id, {}))
async def set_fuel(message: types.Message):
    try:
        fuel = float(message.text)
    except:
        await message.answer("Введите число, например: 7.5")
        return
    user_data[message.from_user.id]["fuel"] = fuel
    await message.answer("Теперь введи максимальное время в пути за день (в часах):")

@dp.message_handler(lambda m: "max_time" not in user_data.get(m.from_user.id, {}))
async def set_max_time(message: types.Message):
    try:
        t = float(message.text)
    except:
        await message.answer("Введите число, например: 6")
        return
    user_data[message.from_user.id]["max_time"] = t

    uid = message.from_user.id
    route = get_route(user_data[uid]["start_coords"], user_data[uid]["end_coords"], config.ORS_API_KEY)
    if not route:
        await message.answer("❌ Не удалось построить маршрут.")
        return

    distance_km = route["features"][0]["properties"]["segments"][0]["distance"] / 1000
    duration_h = route["features"][0]["properties"]["segments"][0]["duration"] / 3600
    fuel_needed = distance_km * user_data[uid]["fuel"] / 100

    user_data[uid]["route"] = route
    user_data[uid]["distance"] = distance_km
    user_data[uid]["duration"] = duration_h
    user_data[uid]["fuel_needed"] = fuel_needed

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Расход топлива", "Кафе каждые 100 км", "Отели каждые 100 км")
    keyboard.add("Сохранить маршрут")

