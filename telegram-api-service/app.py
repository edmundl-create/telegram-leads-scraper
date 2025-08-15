import os
import asyncio
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER')

if not all([API_ID, API_HASH, PHONE_NUMBER]):
    raise ValueError("Missing one or more Telegram API credentials. Ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE_NUMBER are set.")

app = Flask(__name__)
client = TelegramClient('anon_session', int(API_ID), API_HASH)

# --- GLOBAL FLAG FOR CONNECTION STATUS ---
TELETHON_CLIENT_CONNECTED = False

# --- Telegram Client Connection and Authentication Utility ---
# This function will now be called only ONCE during application startup
async def startup_telethon_client():
    global TELETHON_CLIENT_CONNECTED
    print("Attempting to connect Telethon client during application startup...")
    try:
        # We only call client.start() once here. Telethon manages reconnections internally.
        if not await client.is_user_authorized():
            print(f"Telethon client not authorized, attempting session start for {PHONE_NUMBER}...")
            await client.start(phone=PHONE_NUMBER) # This loads session or authenticates
            print("Telethon client authorized successfully during startup.")
        else:
            print("Telethon client already authorized. Ensuring connection is active...")
            # Ensure client is connected if not already (e.g. after sleep)
            if not client.is_connected():
                await client.connect()
            
        print("Telethon client connection established for application lifetime.")
        TELETHON_CLIENT_CONNECTED = True
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to connect Telethon client during startup: {e}")
        # If client cannot connect at startup, future requests will fail.
        # Consider more robust error handling or shutdown.
        TELETHON_CLIENT_CONNECTED = False # Ensure flag is false on failure
        raise # Re-raise to fail early if connection critical

# --- Application Startup Hook for Hypercorn ---
# Hypercorn has its own startup/shutdown hooks
# We'll leverage a simple async task that runs at app start
# Flask has no simple @app.before_first_request for async functions
# So we run it as a background task.
@app.before_serving
async def setup_telethon_client_hook():
    # This hook runs AFTER the server is ready, but BEFORE it serves requests.
    # It might run in Hypercorn's event loop, so we run our client.start() as a task.
    # We should only attempt this if the client isn't already connected globally.
    if not TELETHON_CLIENT_CONNECTED:
        asyncio.create_task(startup_telethon_client())
        print("Telethon client startup task initiated.")

# --- API Endpoints ---

@app.route('/')
def home():
    if TELETHON_CLIENT_CONNECTED:
        return "Telegram Scraper API is running and Telethon client is connected!"
    else:
        return "Telegram Scraper API is running, but Telethon client is not connected.", 503 # Service Unavailable

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    if not TELETHON_CLIENT_CONNECTED:
        return jsonify({"error": "Telegram client not initialized or connected."}), 503

    data = request.json
    keyword = data.get('keyword')
    limit = int(data.get('limit', 5))

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    results = []
    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            title = getattr(entity, 'title', entity.first_name)
            username = getattr(entity, 'username', None)

            if keyword.lower() in title.lower() or (username and keyword.lower() in username.lower()):
                entity_type = "channel"
                if isinstance(entity, User):
                    entity_type = "user"
                elif isinstance(entity, Chat):
                    entity_type = "group"
                elif isinstance(entity, Channel) and entity.megagroup:
                    entity_type = "group"
                elif isinstance(entity, Channel) and entity.broadcast:
                    entity_type = "channel"
                else:
                    entity_type = "unknown"

                results.append({
                    "id": entity.id,
                    "title": title,
                    "username": username,
                    "link": f"https://t.me/{username}" if username else None,
                    "type": entity_type,
                    "is_public": getattr(entity, 'broadcast', False) or getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False)
                })
                if len(results) >= limit:
                    break
        
        if (keyword.startswith('@') or keyword.isdigit()) and not any(r['username'] == keyword or str(r['id']) == keyword for r in results):
            try:
                resolved_entity = await client.get_entity(keyword)
                entity_type = "channel"
                if isinstance(resolved_entity, User):
                    entity_type = "user"
                elif isinstance(resolved_entity, Chat):
                    entity_type = "group"
                elif isinstance(resolved_entity, Channel) and resolved_entity.megagroup:
                    entity_type = "group"
                elif isinstance(resolved_entity, Channel) and resolved_entity.broadcast:
                    entity_type = "channel"
                else:
                    entity_type = "unknown"
                
                results.append({
                    "id": resolved_entity.id,
                    "title": getattr(resolved_entity, 'title', resolved_entity.first_name),
                    "username": getattr(resolved_entity, 'username', None),
                    "link": f"https://t.me/{getattr(resolved_entity, 'username', None)}" if getattr(resolved_entity, 'username', None) else None,
                    "type": entity_type,
                    "is_public": getattr(resolved_entity, 'broadcast', False) or getattr(resolved_entity, 'megagroup', False) or getattr(resolved_entity, 'gigagroup', False)
                })
            except Exception as e:
                print(f"Could not resolve entity '{keyword}' directly: {e}")

    except Exception as e:
        print(f"Error in search_entities: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(results)

@app.route('/get_messages', methods=['POST'])
async def get_messages():
    if not TELETHON_CLIENT_CONNECTED:
        return jsonify({"error": "Telegram client not initialized or connected."}), 503

    data = request.json
    entity_identifier = data.get('entity_id') or data.get('entity_username')
    limit = int(data.get('limit', 10))
    offset_id = int(data.get('offset_id', 0))

    if not entity_identifier:
        return jsonify({"error": "Either entity_id or entity_username is required"}), 400

    messages_data = []
    try:
        entity = None
        if isinstance(entity_identifier, int) or (isinstance(entity_identifier, str) and entity_identifier.isdigit()):
            entity = await client.get_entity(int(entity_identifier))
        elif isinstance(entity_identifier, str):
            entity = await client.get_entity(entity_identifier)

        if not entity:
            return jsonify({"error": "Entity not found or could not be resolved"}), 404

        async for message in client.iter_messages(entity, limit=limit, offset_id=offset_id):
            sender_name = "Unknown"
            sender_id = None
            if message.sender:
                sender_name = getattr(message.sender, 'first_name', '')
                if getattr(message.sender, 'last_name', ''):
                    sender_name += ' ' + getattr(message.sender, 'last_name', '')
                if not sender_name:
                    sender_name = getattr(entity, 'title', 'Channel/Group')
                sender_id = getattr(message.sender, 'id', None)

            messages_data.append({
                "id": message.id,
                "text": message.text,
                "date": message.date.isoformat() if message.date else None,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "is_channel_post": message.post,
                "views": message.views,
                "replies": message.replies.replies if message.replies else 0,
                "link": message.url,
            })

    except Exception as e:
        print(f"Error in get_messages: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(messages_data)

@app.route('/get_members', methods=['POST'])
async def get_members():
    if not TELETHON_CLIENT_CONNECTED:
        return jsonify({"error": "Telegram client not initialized or connected."}), 503

    data = request.json
    entity_identifier = data.get('entity_id') or data.get('entity_username')
    limit = int(data.get('limit', 10))

    if not entity_identifier:
        return jsonify({"error": "Either entity_id or entity_username is required"}), 400

    members_data = []
    try:
        entity = None
        if isinstance(entity_identifier, int) or (isinstance(entity_identifier, str) and entity_identifier.isdigit()):
            entity = await client.get_entity(int(entity_identifier))
        elif isinstance(entity_identifier, str):
            entity = await client.get_entity(entity_identifier)

        if not entity:
            return jsonify({"error": "Entity not found or could not be resolved"}), 404

        if isinstance(entity, (Channel, Chat)) and (entity.megagroup or entity.gigagroup):
            async for participant in client.iter_participants(entity, limit=limit):
                members_data.append({
                    "id": participant.id,
                    "first_name": participant.first_name,
                    "last_name": participant.last_name,
                    "username": participant.username,
                    "phone": participant.phone,
                    "status": str(participant.status) if participant.status else "Unknown",
                    "is_bot": participant.bot
                })
        else:
            return jsonify({"error": "Cannot fetch members from this entity type (must be a group/channel you are a member of with permission) or it's a private chat"}), 400

    except Exception as e:
        print(f"Error in get_members: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(members_data)

# This part is commented out for Render deployment as Hypercorn manages the server.
# if __name__ == '__main__':
#     try:
#         loop = asyncio.get_event_loop()
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#     loop.run_until_complete(startup_telethon_client())
#     app.run(debug=True, port=8080, host='0.0.0.0') # For local testing, not for Render