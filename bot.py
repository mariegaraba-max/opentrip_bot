import logging, math, sqlite3, time, os, requests
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_TOKEN, ORS_API_KEY, OPENTRIPMAP_KEY

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)

DB = "routes.sqlite"

def init_db():
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute(""
CREATE TABLE IF NOT EXISTS routes (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
origin TEXT,
destination TEXT,
consumption REAL,
max_hours REAL,
created_at INTEGER
)
"")
conn.commit()
conn.close()

init_db()
sessions = {}

# ---------------- Utilities ----------------
def haversine(a, b):
lat1, lon1 = a
lat2, lon2 = b
R = 6371000.0
phi1 = math.radians(lat1); phi2 = math.radians(lat2)
dphi = math.radians(lat2 - lat1)
dlambda = math.radians(lon2 - lon1)
x = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2.0)**2
c = 2 * math.atan2(math.sqrt(x), math.sqrt(1-x))
return R * c

def geocode_place(query):
url = "https://nominatim.openstreetmap.org/search"
params = {"q": query, "format": "json", "limit": 1}
headers = {"User-Agent": "opentrip-bot-example/1.0 (contact@example.com)"}
r = requests.get(url, params=params, headers=headers, timeout=10)
data = r.json()
if not data:
return None, None
return float(data[0]["lat"]), float(data[0]["lon"])

def ors_route(lat1, lon1, lat2, lon2):
url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
body = {"coordinates": [[lon1, lat1], [lon2, lat2]]}
r = requests.post(url, json=body, headers=headers, timeout=30)
return r.json()

def extract_coords_from_route(geojson):
try:
geom = geojson["features"][0]["geometry"]["coordinates"]
coords = [(c[1], c[0]) for c in geom]
props = geojson["features"][0]["properties"]
summary = props.get("summary", {})
distance_km = summary.get("distance", 0) / 1000.0
duration_s = summary.get("duration", 0)
return coords, distance_km, duration_s/3600.0
except Exception as e:
logging.exception("extract_coords_from_route failed")
return [], 0.0, 0.0

def cumulative_distances(coords):
cum = 0.0
out = []
prev = coords[0]
out.append((coords[0][0], coords[0][1], 0.0))
for p in coords[1:]:
d = haversine(prev, p)
cum += d
out.append((p[0], p[1], cum))
prev = p
return out

def interpolate_point(coords, target_m):
if not coords:
return None
acc = cumulative_distances(coords)
if target_m <= 0:
return (acc[0][0], acc[0][1])
if target_m >= acc[-1][2]:
return (acc[-1][0], acc[-1][1])
prev = acc[0]
for curr in acc[1:]:
if curr[2] >= target_m:
cum_prev = prev[2]; cum_curr = curr[2]
if cum_curr == cum_prev:
return (curr[0], curr[1])
ratio = (target_m - cum_prev) / (cum_curr - cum_prev)
lat = prev[0] + (curr[0] - prev[0]) * ratio
lon = prev[1] + (curr[1] - prev[1]) * ratio
return (lat, lon)
prev = curr
return (acc[-1][0], acc[-1][1])

def points_every_km(coords, km_step=100):
if not coords:
return []
acc = cumulative_distances(coords)
total = acc[-1][2]
points = []
cur = km_step * 1000.0
while cur < total and len(points) < 30:
p = interpolate_point(coords, cur)
if p:
points.append(p)
cur += km_step * 1000.0
return points

def opentripmap_places(lat, lon, kinds="restaurants", radius=5000, limit=10):
url = "https://api.opentripmap.com/0.1/en/places/radius"
params = {
"apikey":
OPENTRIPMAP_KEY,
"radius": radius,
"lon": lon,
"lat": lat,
"kinds": kinds,
"limit": limit,
"format": "json"
}
r = requests.get(url, params=params, timeout=10)
items = r.json() if r.status_code == 200 else []
out = []
for it in items:
name = it.get("name") or "—"
xid = it.get("xid")
dist = it.get("dist", 0)
maps_url = f"https://opentripmap.com/en/card/{xid}" if xid else ""
out.append({"name": name, "distance_m": dist, "maps_url": maps_url})
out_sorted = sorted(out, key=lambda x: x["distance_m"])
return out_sorted[:limit]

# ---------------- Handlers ----------------
@dp.message_handler(commands=['start','help'])
async def start(m: types.Message):
sessions.pop(m.from_user.id, None)
await m.answer("Привет! Я бот для планирования поездок.\n\nОтправьте маршрут в формате: Город1 - Город2")

@dp.message_handler(lambda msg: '-' in msg.text and msg.text.count('-')==1)
async def route_input(m: types.Message):
uid = m.from_user.id
origin, destination = [p.strip() for p in m.text.split('-', maxsplit=1)]
sessions[uid] = {'origin': origin, 'destination': destination}
await m.answer(f"Маршрут: {origin} → {destination}\n\nВведите средний расход топлива (л/100км), например: 7.5")

@dp.message_handler(lambda msg: sessions.get(msg.from_user.id) and 'consumption' not in sessions[msg.from_user.id])
async def consumption_input(m: types.Message):
uid = m.from_user.id
try:
val = float(m.text.replace(',', '.'))
if val <= 0: raise ValueError()
except:
await m.reply('Неверный формат. Введите число, например 7.5')
return
sessions[uid]['consumption'] = val
await m.answer('Введите макс. время в пути за один день (часы), например: 8')

@dp.message_handler(lambda msg: sessions.get(msg.from_user.id) and 'max_hours' not in sessions[msg.from_user.id])
async def max_hours_input(m: types.Message):
uid = m.from_user.id
try:
val = float(m.text.replace(',', '.'))
if val <= 0: raise ValueError()
except:
await m.reply('Неверный формат. Введите число часов, например 8')
return
sessions[uid]['max_hours'] = val
await build_route(uid, m)

async def build_route(uid, m):
sess = sessions[uid]
origin = sess['origin']
destination = sess['destination']
await m.answer('Строю маршрут...')

lat1, lon1 = geocode_place(origin)
lat2, lon2 = geocode_place(destination)
if lat1 is None or lat2 is None:
await m.answer('Не удалось геокодировать места. Попробуйте уточнить названия.')
return

geo = ors_route(lat1, lon1, lat2, lon2)
coords, dist_km, duration_h = extract_coords_from_route(geo)
if not coords:
await m.answer('Не удалось построить маршрут.')
return

sess['coords'] = coords
sess['distance_km'] = dist_km
sess['duration_h'] = duration_h
days = max(1, int(math.ceil(duration_h / sess['max_hours'])))
km_per_day = dist_km / days
sess['days'] = days
sess['km_per_day'] = km_per_day

kb = InlineKeyboardMarkup(row_width=2)
kb.add(InlineKeyboardButton('🔋 Расход топлива', callback_data='calc_fuel'),
InlineKeyboardButton('🥗 Кафе каждые 100 км', callback_data='cafes_100'))
kb.add(InlineKeyboardButton('🏨 Отели каждые 100 км', callback_data='hotels_100'),
InlineKeyboardButton('💾 Сохранить маршрут', callback_data='save_route'))
kb.add(InlineKeyboardButton('🗺️ Открыть маршрут (OSM)', callback_data='open_maps'))

await m.answer(f"Маршрут: {origin} → {destination}\nДистанция: {dist_km:.1f} км, время ≈ {duration_h:.1f} ч\nДней: {days}, км/день ≈ {km_per_day:.1f}", reply_markup=kb)

@dp.callback_query_handler(lambda c: True)
async def callbacks(call: types.CallbackQuery):
uid = call.from_user.id
data = call.data
sess = sessions.get(uid)
await call.answer()
if not sess:
await
call.message.answer('Сначала задайте маршрут.')
return
if data == 'calc_fuel':
km_day = sess.get('km_per_day',0.0)
cons = sess.get('consumption',0.0)
fuel = (km_day*cons)/100.0
await call.message.answer(f"Потребуется ≈ {fuel:.1f} л топлива в день.")
elif data in ('cafes_100','hotels_100'):
coords = sess.get('coords',[])
points = points_every_km(coords, km_step=100)
kinds = 'restaurants' if data=='cafes_100' else 'accomodations'
for lat, lon in points:
places = opentripmap_places(lat, lon, kinds=kinds, radius=5000, limit=5)
if not places: continue
kb = InlineKeyboardMarkup(row_width=1)
for p in places:
kb.add(InlineKeyboardButton(f"{p['name']} ({int(p['distance_m'])} m)", url=p['maps_url']))
await call.message.answer('Места около точки:', reply_markup=kb)
elif data == 'save_route':
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute('INSERT INTO routes (user_id, origin, destination, consumption, max_hours, created_at) VALUES (?,?,?,?,?,?)',
(uid, sess['origin'], sess['destination'], sess.get('consumption',0), sess.get('max_hours',0), int(time.time())))
conn.commit(); conn.close()
await call.message.answer('Маршрут сохранён.')
elif data == 'open_maps':
origin = sess['origin'].replace(' ','+')
destination = sess['destination'].replace(' ','+')
url = f'https://www.openstreetmap.org/directions?from={origin}&to={destination}'
kb = InlineKeyboardMarkup().add(InlineKeyboardButton('Open in OSM', url=url))
await call.message.answer('Открыть маршрут в OpenStreetMap:', reply_markup=kb)
else:
await call.message.answer('Неизвестная команда.')

@dp.message_handler(commands=['myroutes'])
async def myroutes(m: types.Message):
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute('SELECT id, origin, destination, consumption, max_hours, created_at FROM routes WHERE user_id=? ORDER BY created_at DESC LIMIT 20', (m.from_user.id,))
rows = cur.fetchall()
conn.close()
if not rows:
await m.answer('У вас нет сохранённых маршрутов.')
return
import datetime
lines = [f"#{r[0]} {r[1]} → {r[2]} | расход {r[3]} л/100км | {r[4]} ч | {datetime.datetime.fromtimestamp(r[5]).strftime('%Y-%m-%d %H:%M')}" for r in rows]
await m.answer('\n'.join(lines))

@dp.message_handler()
async def fallback(m: types.Message):
await m.reply("Я не понял. Отправьте маршрут в формате: Город1 - Город2 или /start")

if __name__ == '__main__':
executor.start_polling(dp, skip_updates=True)
