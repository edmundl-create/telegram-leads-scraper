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

async def ensure_telethon_connection():
    if not client.is_connected():
        print("Telethon client not connected, attempting to connect...")
        await client.connect()
        print("Telethon client reconnected.")

    if not await client.is_user_authorized():
        print(f"Telethon client not authorized. Attempting to start session for {PHONE_NUMBER}...")
        try:
            await client.start(phone=PHONE_NUMBER)
            print("Telethon client authorized successfully.")
        except Exception as e:
            print(f"Error during Telethon client authorization: {e}")
            raise

@app.route('/')
def home():
    return "Telegram Scraper API is running!"

@app.route('/search_entities', methods=['POST'])
async def search_entities():
    await ensure_telethon_connection()
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
    await ensure_telethon_connection()
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
    await ensure_telethon_connection()
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
# For running locally (for initial session generation or debugging):
# This part needs to be uncommented for local execution.
# if __name__ == '__main__':
#     # Ensure the event loop is created for async Flask and run initial connection
#     try:
#         loop = asyncio.get_event_loop()
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#
#     # Run the ensure_telethon_connection before starting the app
#     # This is where the Telegram authentication prompt will appear
#     loop.run_until_complete(ensure_telethon_connection())
#
#     # You can keep this line commented out for session generation,
#     # or uncomment if you want to test the local Flask server after session.
#     # If uncommented, you can access http://localhost:8080
#     # app.run(debug=True, port=8080, host='0.0.0.0')
#     print("Telethon session generated. Script will now exit unless app.run is uncommented.")
