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

# --- Telethon Client Connection Management ---
# A Future to track the client's connection status and hold the client itself (or an exception)
_telethon_client_startup_future = asyncio.Future()

async def _connect_telethon_on_startup_task():
    """Internal task to handle the actual Telethon connection and authorization."""
    print("Attempting Telethon client connection and authorization...")
    try:
        if not await client.is_user_authorized():
            print(f"Telethon client not authorized, attempting session start for {PHONE_NUMBER}...")
            await client.start(phone=PHONE_NUMBER) # This loads session or authenticates
            print("Telethon client authorized successfully.")
        else:
            print("Telethon client already authorized. Ensuring connection is active...")
            if not client.is_connected():
                await client.connect() # Ensure connection if it's currently disconnected
            print("Telethon client connection established for application lifetime.")
        _telethon_client_startup_future.set_result(client) # Store the connected client upon success
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to connect Telethon client: {e}")
        _telethon_client_startup_future.set_exception(e) # Store the exception upon failure

# A global variable to hold the background startup task reference
_startup_task = None

async def ensure_telethon_client_ready():
    """
    Ensures the Telethon client startup task is initiated and awaited.
    This function should be called at the beginning of each async API route.
    """
    global _startup_task
    
    # If the startup process hasn't completed yet
    if not _telethon_client_startup_future.done():
        if _startup_task is None:
            # Only create the task once. The first request that hits this will initiate it.
            _startup_task = asyncio.create_task(_connect_telethon_on_startup_task())
            print("Scheduled Telethon client startup as a background task via first request.")
        
        # Wait for the startup task to complete.
        # This will wait for the _startup_task if it's still running,
        # or immediately return if it's already completed.
        try:
            await asyncio.wait_for(_startup_task, timeout=90) # Increased timeout to 90 seconds for client startup
        except asyncio.TimeoutError:
            print("Telethon client startup task timed out waiting.")
            raise RuntimeError("Telegram client startup timed out.")
    
    # After waiting, check if the future holds an exception from the startup attempt
    if _telethon_client_startup_future.exception():
        raise _telethon_client_startup_future.exception() # Re-raise the original exception

# --- API Endpoints ---
@app.route('/')
async def home(): # Make home route async too to use ensure_telethon_client_ready
    try:
        await ensure_telethon_client_ready()
        return "Telegram Scraper API is running and Telethon client is connected!"
    except Exception as e:
        return f"Telegram Scraper API is running, but Telethon client is not connected: {e}", 503

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

    data = request.json
    keyword = data.get('keyword')
    limit = int(data.get('limit', 5))

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    results = []
    try:
        # All client calls are now directly awaited as they rely on the already-connected client
        # client = _telethon_client_startup_future.result() # You can get the client from the future, but global 'client' is the same instance
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
        
        # Direct lookup for exact matches (e.g., @username or ID) if not found in dialogs
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
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

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
                if not sender_name: # Fallback if no first/last name
                    sender_name = getattr(entity, 'title', 'Channel/Group') # Use entity title if sender name empty
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
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

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
        
        # Only fetch members from groups/channels
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
#     # We don't call client.start() here because Hypercorn handles the event loop.
#     app.run(debug=True, port=8080, host='0.0.0.0') # For local testing, not for Render