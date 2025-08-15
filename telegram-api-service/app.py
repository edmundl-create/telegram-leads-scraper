import os
import asyncio
import sys # Import sys for stdout flushing
import traceback # Import traceback for printing exceptions
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession # Import StringSession
from telethon.tl.types import Channel, Chat, User
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
TELETHON_STRING_SESSION = os.getenv('TELETHON_STRING_SESSION') # New env var

# Basic validation
if not all([API_ID, API_HASH, TELETHON_STRING_SESSION]):
    raise ValueError("Missing one or more Telegram API credentials. Ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELETHON_STRING_SESSION are set.")

app = Flask(__name__)

# Global client and connection management variables
# client will be initialized/re-initialized as needed
client = None # Start with client as None
_telethon_client_startup_future = asyncio.Future() # Tracks if ANY client startup has succeeded
_startup_task = None # To hold the reference to the initial background task

async def initialize_and_connect_telethon_client():
    """
    Initializes a new TelethonClient and attempts to connect it.
    This function creates a *new* client instance each time it's called.
    """
    global client
    print("LOG: initialize_and_connect_telethon_client: Creating new TelethonClient instance.", flush=True)
    try:
        # Create a brand new client instance for this connection attempt
        client = TelegramClient(StringSession(TELETHON_STRING_SESSION), int(API_ID), API_HASH)
        
        print("LOG: initialize_and_connect_telethon_client: Attempting client.start() with StringSession...", flush=True)
        await client.start() # This handles connection and authorization with StringSession
        print("LOG: initialize_and_connect_telethon_client: client.start() completed.", flush=True)
        
        if await client.is_user_authorized():
            print("LOG: initialize_and_connect_telethon_client: Telethon client authorized and connected successfully.", flush=True)
            return True # Success
        else:
            print("CRITICAL ERROR: initialize_and_connect_telethon_client: Telethon client connected but not authorized unexpectedly.", flush=True)
            return False # Failed authorization
            
    except Exception as e:
        print(f"CRITICAL ERROR: initialize_and_connect_telethon_client: Failed to connect Telethon client: {e}", flush=True)
        print("CRITICAL ERROR: Printing full traceback for connection failure:", flush=True)
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return False # Failed to connect


async def ensure_telethon_client_ready():
    """
    Ensures the Telethon client is connected and ready.
    This function is called by each API endpoint. It now handles:
    1. Initial global client setup.
    2. Re-connecting/re-initializing the client if it becomes disconnected.
    """
    global client, _startup_task

    # Step 1: Handle initial client setup if it hasn't happened yet
    if client is None:
        print("LOG: ensure_telethon_client_ready: Client is None. Initiating first-time connection.", flush=True)
        _startup_task = asyncio.create_task(initialize_and_connect_telethon_client())
        _telethon_client_startup_future.set_result(None) # Mark future as started, actual result set by task
        print("LOG: ensure_telethon_client_ready: Initial client connection scheduled as background task.", flush=True)
        
        try:
            # Wait for the initial connection task to complete.
            success = await asyncio.wait_for(_startup_task, timeout=120) 
            if not success:
                print("CRITICAL ERROR: ensure_telethon_client_ready: Initial client connection failed (task returned False).", flush=True)
                raise RuntimeError("Initial Telegram client connection failed.")
            print("LOG: ensure_telethon_client_ready: Initial client connection task completed successfully.", flush=True)
        except asyncio.TimeoutError:
            print("CRITICAL ERROR: ensure_telethon_client_ready: Initial Telegram client connection timed out.", flush=True)
            raise RuntimeError("Initial Telegram client connection timed out.")
        except Exception as e:
            print(f"CRITICAL ERROR: ensure_telethon_client_ready: Unexpected error during initial client connection: {e}", flush=True)
            raise RuntimeError(f"Unexpected error during initial client connection: {e}")

    # Step 2: For subsequent requests (or if initial client setup needs re-checking):
    # Check if the client is connected and authorized. If not, re-initialize and connect.
    try:
        # Check if the client is not connected OR if it exists but is not authorized
        if not (client and client.is_connected() and await client.is_user_authorized()):
            print("LOG: ensure_telethon_client_ready: Client either not connected or not authorized. Attempting re-initialization and reconnection...", flush=True)
            success = await initialize_and_connect_telethon_client() # Recreate and reconnect
            if not success:
                print("CRITICAL ERROR: ensure_telethon_client_ready: Re-connection/re-initialization failed.", flush=True)
                raise RuntimeError("Telegram client failed to reconnect/re-initialize.")
            print("LOG: ensure_telethon_client_ready: Client re-initialized and reconnected successfully.", flush=True)
        
    except Exception as e:
        print(f"CRITICAL ERROR: ensure_telethon_client_ready: Failed to ensure client is connected/authorized for request: {e}", flush=True)
        traceback.print_exc(file=sys.stdout) # Print full traceback
        sys.stdout.flush()
        # Ensure the future is marked as failed so subsequent calls fail fast
        if not _telethon_client_startup_future.done():
            _telethon_client_startup_future.set_exception(e)
        raise RuntimeError(f"Telegram client failed to reconnect or verify authorization: {e}")

    # If we reached here, the client should be ready
    print("LOG: Telethon client confirmed ready for request.", flush=True)


# --- API Endpoints ---
@app.route('/')
async def home():
    print("LOG: Received request to /", flush=True)
    try:
        await ensure_telethon_client_ready()
        return "Telegram Scraper API is running and Telethon client is connected!"
    except Exception as e:
        print(f"LOG: Error at / endpoint: {e}", flush=True)
        return f"Telegram Scraper API is running, but Telethon client is not connected: {e}", 503

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    print("LOG: Received request to /search_entities", flush=True)
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        print(f"LOG: Error in search_entities during client readiness check: {e}", flush=True)
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

    data = request.json
    keyword = data.get('keyword')
    limit = int(data.get('limit', 5))
    print(f"LOG: search_entities - Keyword: {keyword}, Limit: {limit}", flush=True)

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
                print(f"LOG: Could not resolve entity '{keyword}' directly: {e}", flush=True)

    except Exception as e:
        print(f"ERROR: Error in search_entities: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

    return jsonify(results)


@app.route('/get_messages', methods=['POST'])
async def get_messages():
    print("LOG: Received request to /get_messages", flush=True)
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        print(f"LOG: Error in get_messages during client readiness check: {e}", flush=True)
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

    data = request.json
    entity_identifier = data.get('entity_id') or data.get('entity_username')
    limit = int(data.get('limit', 10))
    offset_id = int(data.get('offset_id', 0))
    print(f"LOG: get_messages - Entity: {entity_identifier}, Limit: {limit}, Offset: {offset_id}", flush=True)

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
        print(f"ERROR: Error in get_messages: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

    return jsonify(messages_data)


@app.route('/get_members', methods=['POST'])
async def get_members():
    print("LOG: Received request to /get_members", flush=True)
    try:
        await ensure_telethon_client_ready()
    except Exception as e:
        print(f"LOG: Error in get_members during client readiness check: {e}", flush=True)
        return jsonify({"error": f"Telegram client not ready: {e}"}), 503

    data = request.json
    entity_identifier = data.get('entity_id') or data.get('entity_username')
    limit = int(data.get('limit', 10))
    print(f"LOG: get_members - Entity: {entity_identifier}, Limit: {limit}", flush=True)

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
        print(f"ERROR: Error in get_members: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

    return jsonify(members_data)

# This part is commented out for Render deployment as Hypercorn manages the server.
# if __name__ == '__main__':
#     try:
#         loop = asyncio.get_event_loop()
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#     app.run(debug=True, port=8080, host='0.0.0.0') # For local testing, not for Render