import type { TelegramUpdate } from './types';

interface APIGatewayProxyEvent {
  httpMethod: string;
  path: string;
  headers: Record<string, string | undefined>;
  body: string | null;
  isBase64Encoded: boolean;
}

interface APIGatewayProxyResult {
  statusCode: number;
  headers?: Record<string, string>;
  body: string;
}

/**
 * Telegram Webhook Lambda Handler for YusufClaw.
 *
 * Responsibilities:
 * 1. Validate optional secret token header (x-telegram-bot-api-secret-token)
 * 2. Parse Telegram update event
 * 3. Log metadata for observability
 * 4. Respond with 200 OK immediately to satisfy Telegram's webhook timeout contract
 */
export async function handler(event: APIGatewayProxyEvent): Promise<APIGatewayProxyResult> {
  const method = event.httpMethod?.toUpperCase();

  // Handle health checks / GET
  if (method === 'GET') {
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: 'healthy',
        service: 'yusufclaw-telegram-webhook',
        timestamp: new Date().toISOString(),
      }),
    };
  }

  // Telegram webhook updates must be POST
  if (method !== 'POST') {
    return {
      statusCode: 405,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: 'Method Not Allowed' }),
    };
  }

  // Validate Secret Token if configured in environment
  const expectedSecret = process.env.TELEGRAM_SECRET_HEADER;
  if (expectedSecret) {
    const incomingSecret =
      event.headers['x-telegram-bot-api-secret-token'] ||
      event.headers['X-Telegram-Bot-Api-Secret-Token'];

    if (incomingSecret !== expectedSecret) {
      console.warn('Unauthorized webhook invocation: secret header mismatch');
      return {
        statusCode: 403,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ error: 'Forbidden' }),
      };
    }
  }

  if (!event.body) {
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: 'Empty request body' }),
    };
  }

  try {
    const rawBody = event.isBase64Encoded
      ? Buffer.from(event.body, 'base64').toString('utf-8')
      : event.body;

    const update: TelegramUpdate = JSON.parse(rawBody);

    console.info('Received Telegram Update:', {
      update_id: update.update_id,
      chat_id: update.message?.chat.id ?? update.callback_query?.message?.chat.id,
      from: update.message?.from?.username ?? update.callback_query?.from?.username,
      has_text: Boolean(update.message?.text),
      has_document: Boolean(update.message?.document),
      has_photo: Boolean(update.message?.photo),
      is_callback: Boolean(update.callback_query),
    });

    // Telegram requires a 200 OK fast response
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, update_id: update.update_id }),
    };
  } catch (err: unknown) {
    console.error('Failed to parse or process update:', err);
    // Return 200 to prevent Telegram retry loop on unrecoverable bad payloads
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'Malformed payload' }),
    };
  }
}
