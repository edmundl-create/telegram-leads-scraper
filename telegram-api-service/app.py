import os
import asyncio
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER')

if not all([API_ID, API_HASH, PHONE_NUMBER]):
    raise ValueError("Missing one or more Telegram API credentials. Ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE_NUMBER are set.")

app = Flask(__name__)
client = TelegramClient('anon_session', int(API_ID), API_HASH)

# --- DIRECT TELETHON CLIENT INITIALIZATION AT MODULE LOAD ---
# This approach leverages the fact that Hypercorn will manage an event loop
# when the module is first loaded, allowing Telethon to connect once.
# The 'anon_session.session' file MUST be present for this to work without interaction.
try:
    # Run the client connection in a separate thread's event loop
    # or ensure it's awaited if Hypercorn provides a context.
    # For Hypercorn, this generally works because it establishes the loop on startup.
    loop = asyncio.get_event_loop() # Get Hypercorn's event loop
except RuntimeError:
    # If no loop is running (e.g., in a non-Hypercorn test environment, though not for Render)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Create a future to hold the connection status/client object
connection_future = asyncio.Future(loop=loop)

async def _connect_telethon_on_startup():
    print("Attempting to connect Telethon client directly on module load...")
    try:
        # Client.start() handles connection and authorization/session loading
        if not await client.is_user_authorized():
            print("Telethon client not authorized, attempting session start (should use session file)...")
            await client.start(phone=PHONE_NUMBER)
            print("Telethon client authorized successfully.")
        else:
            print("Telethon client already authorized. Ensuring connection is active...")
            if not client.is_connected():
                await client.connect()
        print("Telethon client connection established for application lifetime.")
        connection_future.set_result(client) # Indicate success
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to connect Telethon client on startup: {e}")
        connection_future.set_exception(e) # Indicate failure
        raise # Re-raise to crash early if connection is critical

# Schedule the connection task to run on the event loop
# This makes it run once when the module loads, managed by Hypercorn's event loop.
asyncio.create_task(_connect_telethon_on_startup())

# --- API Endpoints ---

@app.route('/')
def home():
    # Check if the connection attempt completed successfully
    if connection_future.done() and not connection_future.exception():
        return "Telegram Scraper API is running and Telethon client is connected!"
    else:
        # If connection failed at startup, report 503
        return "Telegram Scraper API is running, but Telethon client failed to connect at startup.", 503

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    # Wait for the client to be connected on the first request if it hasn't finished yet
    # This acts as a safeguard during potential race conditions at startup
    if not connection_future.done():
        try:
            await asyncio.wait_for(connection_future, timeout=30) # Wait up to 30 seconds for connection
        except asyncio.TimeoutError:
            return jsonify({"error": "Telegram client startup timed out."}), 503
        except Exception as e:
            return jsonify({"error": f"Telegram client startup failed: {e}"}), 503

    if connection_future.exception():
        return jsonify({"error": f"Telegram client failed to connect: {connection_future.exception()}"}), 503

    data = request.json
    keyword = data.get('keyword')
    limit = int(data.get('limit', 5))

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    results = []
    try:
        # All client calls are now directly awaited as they rely on the already-connected client
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
    if not connection_future.done():
        try:
            await asyncio.wait_for(connection_future, timeout=30)
        except asyncio.TimeoutError:
            return jsonify({"error": "Telegram client startup timed out."}), 503
        except Exception as e:
            return jsonify({"error": f"Telegram client startup failed: {e}"}), 503

    if connection_future.exception():
        return jsonify({"error": f"Telegram client failed to connect: {connection_future.exception()}"}), 503

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
    if not connection_future.done():
        try:
            await asyncio.wait_for(connection_future, timeout=30)
        except asyncio.TimeoutError:
            return jsonify({"error": "Telegram client startup timed out."}), 503
        except Exception as e:
            return jsonify({"error": f"Telegram client startup failed: {e}"}), 503

    if connection_future.exception():
        return jsonify({"error": f"Telegram client failed to connect: {connection_future.exception()}"}), 503

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