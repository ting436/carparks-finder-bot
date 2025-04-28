from dotenv import load_dotenv
import os
import telebot
from telebot import types
import time
import threading
import requests
from math import radians, cos, sin, sqrt, atan2
from pyproj import Transformer

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

bot = telebot.TeleBot(BOT_TOKEN)

user_locations = {}
EXPIRY_SECONDS = 600

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    share_location_button = types.KeyboardButton("📍 Share Location", request_location=True)
    markup.add(share_location_button)
    bot.send_message(message.chat.id,
                    "Welcome to the Location & Carpark Bot!\n\n"
                    "Please share your location to obtain the availabilities of the nearest carparks around you.", 
                    reply_markup=markup)


@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
    /start - Start the bot and get a welcome message.
    /help - Get information on how to use the bot.
    📍 Share your location to find nearby carparks.
    🅿️ Check carpark availability and more.
    """
    bot.reply_to(message, help_text)

@bot.message_handler(content_types=['location'])
def handle_location(message):
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)    
    carpark_button = types.KeyboardButton("🅿️ Carpark Availability")
    markup.add(carpark_button)

    user_id = message.from_user.id
    latitude = message.location.latitude
    longitude = message.location.longitude

    user_locations[user_id] = {
        'latitude': latitude,
        'longitude': longitude,
        'timestamp': time.time()
    }

    bot.send_message(message.chat.id,
                    "Thank you for sharing your location!\n\n" \
                    "Please select the carpark availability option below to check the nearest carparks around you.", 
                    reply_markup=markup)

def haversine(lat1, lon1, lat2, lon2):
    # Calculate distance between 2 lat/lng points (in meters)
    R = 6371000  # Radius of Earth in meters
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def fetch_carpark_info(offset=0, limit=100):
    url = f"https://data.gov.sg/api/action/datastore_search?resource_id=d_23f946fa557947f93a8043bbef41dd09&offset={offset}&limit={limit}"
    response = requests.get(url)

    if response.status_code != 200:
        return []

    data = response.json()
    return data['result']['records']


def fetch_carpark_availability():
    # Fetch live carpark availability
    url = f"https://api.data.gov.sg/v1/transport/carpark-availability/"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()['items'][0]['carpark_data']

@bot.message_handler(func=lambda message: message.text == "🅿️ Carpark Availability")
def show_carpark_availability(message):
    user_id = message.from_user.id
    if user_id not in user_locations:
        bot.send_message(message.chat.id, "Please share your location first by using /start.")
        return

    user_lat = user_locations[user_id]['latitude']
    user_lon = user_locations[user_id]['longitude']
    carpark_metadata = []
    try:
        offset = 0
        limit = 100
        while True:
            batch = fetch_carpark_info(offset, limit)
            if not batch:
                break
            carpark_metadata.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        carpark_availability = fetch_carpark_availability()
    except Exception as e:
        bot.send_message(message.chat.id, "Failed to fetch carpark data.")
        print(e)
        return

    transformer = Transformer.from_crs('EPSG:3414', 'EPSG:4326', always_xy=True)

    carpark_list = []

    for carpark in carpark_metadata:
        try:
            x = float(carpark['x_coord'])
            y = float(carpark['y_coord'])
            lng, lat = transformer.transform(x, y)

            distance = haversine(user_lat, user_lon, lat, lng)

            matched = next((c for c in carpark_availability if c['carpark_number'].strip().upper() == carpark['car_park_no'].strip().upper()), None)

            if matched:
                car_lots = [info for info in matched['carpark_info']]
                free_parking    = carpark.get('free_parking', 'Unknown')  
                if car_lots:
                    total_lots = sum(int(info['lots_available']) for info in car_lots if info['lot_type'] == 'C')
                    carpark_list.append({
                        'car_park_no': carpark['car_park_no'],
                        'address': carpark['address'],
                        'lat': lat,
                        'lng': lng,
                        'distance': distance,
                        'available_lots': total_lots,
                        'free_parking': free_parking
                    })

        except Exception as e:
            continue  # Skip any invalid records

    carpark_list = sorted(carpark_list, key=lambda x: x['distance'])

    nearest = carpark_list[:5]

    reply = "Here are the nearest carparks:\n\n"
    for idx, carpark in enumerate(nearest, start=1):
        reply += (f"{idx}. {carpark['address']} ({carpark['car_park_no']})\n"
                  f"Distance: {int(carpark['distance'])} meters\n"
                  f"Free Parking: {carpark['free_parking']}\n"
                  f"Available lots: {carpark['available_lots']}\n\n")

    bot.send_message(message.chat.id, reply)


def cleanup_locations():
    while True:
        current_time = time.time()
        expired_users = []

        for user_id, data in user_locations.items():
            if current_time - data['timestamp'] > EXPIRY_SECONDS:
                expired_users.append(user_id)

        for user_id in expired_users:
            del user_locations[user_id]
            print(f"Removed expired location for user {user_id}")

        time.sleep(60)


bot.set_my_commands([
    types.BotCommand("start", "🏠 Start the bot and share location"),
    types.BotCommand("help",  "❓ Show usage instructions"),
])

cleanup_thread = threading.Thread(target=cleanup_locations, daemon=True)
cleanup_thread.start()

bot.infinity_polling()