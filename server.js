// server.js - Telegram Scraper Service
const express = require('express');
const cors = require('cors');
const { TelegramClient } = require('telethon');
const { StringSession } = require('telethon/sessions');
const { Api } = require('telethon/tl'); // Needed for some API types

const app = express();
const PORT = process.env.PORT || 10000;

// Get credentials from environment variables
const API_ID = parseInt(process.env.TELEGRAM_API_ID);
const API_HASH = process.env.TELEGRAM_API_HASH;
const SESSION_STRING = process.env.TELEGRAM_SESSION;

// Basic validation for credentials
if (!API_ID || !API_HASH || !SESSION_STRING) {
    console.error('ERROR: Telegram API credentials or session not found in environment variables!');
    console.error('Please set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_SESSION.');
    process.exit(1); // Exit if critical env vars are missing
}

const telegramSession = new StringSession(SESSION_STRING);
let client = null; // Telegram client instance

// Function to initialize and connect Telegram client
async function connectTelegramClient() {
    if (client && client.connected) {
        // console.log('Telegram client already connected.'); // Uncomment for debug
        return client;
    }

    console.log('Initializing Telegram client...');
    try {
        client = new TelegramClient(telegramSession, API_ID, API_HASH, {
            connectionRetries: 5, // Retry connection
        });

        await client.connect();
        if (!client.connected) {
            throw new Error('Telegram client failed to connect.');
        }
        console.log('Telegram client connected successfully!');
        const me = await client.getMe();
        console.log(`Logged in as: ${me.firstName} (@${me.username || 'no_username'})`);
        return client;
    } catch (error) {
        console.error('Failed to connect to Telegram API:', error.message);
        if (client) {
            await client.disconnect();
            client = null;
        }
        throw error;
    }
}

// Automatically connect on server start
connectTelegramClient().catch(err => {
    console.error('Initial Telegram connection failed, server might not function correctly:', err);
});

// Health check endpoint
app.get('/', (req, res) => {
  res.json({
    message: 'Telegram Scraper Service is Healthy and Ready!',
    status: client && client.connected ? 'telegram_connected' : 'telegram_disconnected'
  });
});

// --- Endpoint 1: Generic Search (Placeholder from previous structure) ---
// This is now less relevant if you're using /get-chat-messages
app.post('/scrape-douyin', async (req, res) => {
    // This endpoint is just a placeholder to keep consistency from previous Douyin context
    // You might remove it later if not needed
    res.status(400).json({ error: 'This endpoint is for general search, use /get-chat-messages for specific Telegram interactions.' });
});


// --- Endpoint 2: Get Messages from a Specific Target (User, Group, Channel) ---
app.post('/get-chat-messages', async (req, res) => {
    const { targetId, keyword = '', limit = 50 } = req.body; // targetId can be user ID, group ID, or channel username
    // You can adjust the default limit of 50 messages per call

    if (!targetId) {
        return res.status(400).json({ error: 'targetId (e.g., channel username, user ID, group ID) is required in the request body.' });
    }

    console.log(`Getting messages from target ${targetId} for keyword: "${keyword}" (limit: ${limit})`);

    let clientInstance;
    try {
        clientInstance = await connectTelegramClient(); // Ensure client is connected

        let entity;
        try {
            if (typeof targetId === 'string' && targetId.startsWith('@')) {
                // It's a username (e.g., @telegram, @someuser)
                entity = await clientInstance.getEntity(targetId);
            } else if (!isNaN(parseInt(targetId))) {
                // It's a numerical ID (user ID, group ID, channel ID)
                // Telethon needs BigInt for IDs in some cases, so parse as int and let telethon convert if needed.
                entity = await clientInstance.getEntity(parseInt(targetId));
            } else {
                throw new Error('Invalid targetId format. Must be @username or a numerical ID.');
            }
            console.log(`Resolved target entity: ${entity.className} (ID: ${entity.id.toString() || 'N/A'})`);
        } catch (error) {
            console.error(`Error resolving target entity for "${targetId}":`, error.message);
            return res.status(404).json({ error: `Could not find or access target with ID/Username: "${targetId}". Make sure your Telegram account is a participant in the group/chat/channel.` });
        }


        const result = await clientInstance.getMessages(entity, {
            search: keyword || undefined, // Pass keyword only if not empty, undefined means no search filter
            limit: limit,
        });

        const messages = result.map(msg => ({
            id: msg.id,
            peerType: entity.className || 'Unknown', // e.g., 'User', 'Chat', 'Channel'
            peerId: entity.id ? entity.id.toString() : null, // ID of the chat/group/channel
            senderId: msg.senderId ? msg.senderId.toString() : null,
            senderUsername: msg.sender ? (msg.sender.username || (msg.sender.firstName || '') + (msg.sender.lastName || '')).trim() : 'Unknown',
            date: msg.date ? new Date(msg.date * 1000).toISOString() : null, // Telegram date is Unix timestamp in seconds
            message: msg.message,
            views: msg.views, // For channels
            forwards: msg.forwards, // For channels
            // Check if message contains links (simple regex example)
            containsLink: msg.message ? /(https?:\/\/[^\s]+)/gi.test(msg.message) : false,
            // Extract first link (simple regex example, could be more robust)
            firstLink: msg.message ? (msg.message.match(/(https?:\/\/[^\s]+)/gi) || [null])[0] : null,
        }));

        console.log(`Found ${messages.length} messages in target ${targetId} for keyword: "${keyword}".`);
        res.json({ keyword, targetId, messages });

    } catch (error) {
        console.error('Telegram message search failed:', error);
        res.status(500).json({ error: 'Failed to search Telegram messages.', details: error.message });
    } finally {
        // The client should stay connected for subsequent requests, no disconnect here.
    }
});


// Start the server
app.listen(PORT, () => {
  console.log(`Telegram Scraper Service listening on port ${PORT}`);
});

// Handle graceful shutdown to disconnect Telegram client
process.on('SIGTERM', async () => {
    console.log('SIGTERM received, disconnecting Telegram client...');
    if (client && client.connected) {
        await client.disconnect();
        console.log('Telegram client disconnected.');
    }
    process.exit(0);
});
process.on('SIGINT', async () => {
    console.log('SIGINT received, disconnecting Telegram client...');
    if (client && client.connected) {
        await client.disconnect();
        console.log('Telegram client disconnected.');
    }
    process.exit(0);
});
