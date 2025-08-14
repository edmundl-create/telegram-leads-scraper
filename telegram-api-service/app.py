import os
import asyncio # Required for running async functions properly
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User # Import specific types for better identification
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
# On Render, these will be injected directly as environment variables
load_dotenv()

# --- Configuration ---
# Telegram API credentials from environment variables
# IMPORTANT: These must be set as environment variables on Render
# and in a .env file locally for testing.
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER') # Your phone number with country code, e.g., +12345678900

# Basic validation for credentials
if not all([API_ID, API_HASH, PHONE_NUMBER]):
    raise ValueError("Missing one or more Telegram API credentials. Ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE_NUMBER are set.")

app = Flask(__name__)
# 'anon_session' is the session file name. Telethon will create 'anon_session.session'
# Make sure to generate this file locally and commit it to your GitHub repo for Render.
client = TelegramClient('anon_session', int(API_ID), API_HASH)

# --- Telegram Client Connection and Authentication ---
async def connect_client():
    """Connects the Telegram client and handles authentication."""
    print("Connecting to Telegram client...")
    try:
        if not client.is_connected():
            await client.connect()
            print("Telegram client connected.")

        if not await client.is_user_authorized():
            print(f"Authorizing client for {PHONE_NUMBER}...")
            # This part requires interaction on the first run (e.g., entering a code).
            # For Render, you should pre-generate the 'anon_session.session' file locally
            # and commit it to your repository.
            await client.start(phone=PHONE_NUMBER)
            print("Client authorized successfully.")
        else:
            print("Client already authorized.")
    except Exception as e:
        print(f"Error connecting or authorizing Telegram client: {e}")
        # Depending on the error, you might want to raise it to stop the service
        # or handle it gracefully. For now, we'll let it raise to prevent
        # the service from running if it can't connect.
        raise

# This ensures the client is connected before any routes are called.
# Using a background task for connection is safer for Flask's sync request handling.
@app.before_first_request
def setup_client():
    # Run the async connection in a separate thread/task for Flask
    # This is a common pattern for Flask with async libraries
    # In a real-world app, consider a more robust setup like Flask-Executor
    # or ensure your WSGI server (Hypercorn) manages the event loop correctly.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # If no loop is running (e.g., Flask development server), create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(connect_client())


# --- API Endpoints ---

@app.route('/')
def home():
    return "Telegram Scraper API is running!"

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    """
    Searches for Telegram entities (channels, groups, users) by keyword.
    It primarily searches through entities the authenticated user has dialogs with
    or can resolve by exact username/ID. Global search for public entities is limited
    via this type of client.
    Request body: {"keyword": "your_search_term", "limit": 10}
    """
    data = request.json
    keyword = data.get('keyword')
    limit = int(data.get('limit', 5)) # Default to 5 results for efficiency

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    results = []
    try:
        # Search global messages/entities the client has access to
        # This iterates through all dialogs (chats, channels, groups) the user is part of
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            title = getattr(entity, 'title', entity.first_name) # For users, it's first_name
            username = getattr(entity, 'username', None)

            # Check if keyword is in title or username
            if keyword.lower() in title.lower() or (username and keyword.lower() in username.lower()):
                entity_type = "channel"
                if isinstance(entity, User):
                    entity_type = "user"
                elif isinstance(entity, Chat): # Group chat (old style)
                    entity_type = "group"
                elif isinstance(entity, Channel) and entity.megagroup: # Mega group (modern group)
                    entity_type = "group"
                elif isinstance(entity, Channel) and entity.broadcast: # Channel (broadcast)
                    entity_type = "channel"
                else: # Default catch-all
                    entity_type = "unknown"

                results.append({
                    "id": entity.id,
                    "title": title,
                    "username": username,
                    "link": f"https://t.me/{username}" if username else None,
                    "type": entity_type,
                    "is_public": getattr(entity, 'broadcast', False) or getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False) # Simplified public check
                })
                if len(results) >= limit:
                    break
        
        # Also try to resolve the keyword directly if it looks like a username or ID
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
    """
    Fetches messages from a specified Telegram entity (channel, group, or user chat).
    Request body: {"entity_id": 12345, "limit": 10, "offset_id": 0}
    OR {"entity_username": "mychannel", "limit": 10, "offset_id": 0}
    """
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
                if not sender_name: # In case of channel posts without explicit sender
                    sender_name = getattr(entity, 'title', 'Channel/Group') # Use entity title as sender for channel posts
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
                "link": message.url, # Gets public link if available (for channels/public groups)
            })

    except Exception as e:
        print(f"Error in get_messages: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(messages_data)

@app.route('/get_members', methods=['POST'])
async def get_members():
    """
    Fetches members from a public Telegram group or channel.
    Note: Requires the client to be a member of the group/channel, or it to be public
    and the client has sufficient permissions. Private groups or channels
    where the client is not a member cannot have members fetched this way.
    Request body: {"entity_id": 12345, "limit": 10}
    OR {"entity_username": "mychannel", "limit": 10}
    """
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

        # Check if the entity is a Channel or Chat (group) that supports member listing
        # And if the client has permission to view members
        if isinstance(entity, (Channel, Chat)) and (entity.megagroup or entity.gigagroup):
            # Try to get participants. Telethon might throw an error if not allowed.
            async for participant in client.iter_participants(entity, limit=limit):
                members_data.append({
                    "id": participant.id,
                    "first_name": participant.first_name,
                    "last_name": participant.last_name,
                    "username": participant.username,
                    "phone": participant.phone, # Only visible if user has shared it or it's a mutual contact
                    "status": str(participant.status) if participant.status else "Unknown", # Online, Offline, etc.
                    "is_bot": participant.bot
                })
        else:
            return jsonify({"error": "Cannot fetch members from this entity type (must be a group/channel you are a member of with permission) or it's a private chat"}), 400

    except Exception as e:
        print(f"Error in get_members: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(members_data)

# For running locally (for initial session generation or debugging):
# if __name__ == '__main__':
#     # Ensure the event loop is created for async Flask
#     try:
#         loop = asyncio.get_event_loop()
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
    
#     # Connect client before running app
#     loop.run_until_complete(connect_client())
#     app.run(debug=True, port=8080) # Use 0.0.0.0 for external access in containers
