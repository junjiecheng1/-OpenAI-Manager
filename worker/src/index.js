import PostalMime from 'postal-mime';

const DEFAULT_TTL_SECONDS = 300;
const DEFAULT_CODE_REGEX = String.raw`(?<!\d)(\d{4,8})(?!\d)`;
const KEYWORD_REGEXES = [
  /(?:verification code|security code|login code|auth(?:entication)? code|one[ -]?time code|one[ -]?time passcode|otp|passcode|验证码|校验码|动态码|認証コード|コード)[^A-Z0-9]{0,24}([A-Z0-9]{4,10})/i,
  /([A-Z0-9]{4,10})[^A-Z0-9]{0,24}(?:is your verification code|is your security code|is your login code|is your otp|验证码|校验码|动态码|認証コード)/i,
];

function json(data, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set('content-type', 'application/json; charset=utf-8');
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers,
  });
}

function normalizeEmail(value) {
  return String(value || '').trim().toLowerCase();
}

function maskEmail(value) {
  const normalized = normalizeEmail(value);
  const at = normalized.indexOf('@');
  if (at <= 1) {
    return normalized;
  }
  return `${normalized.slice(0, 2)}***${normalized.slice(at)}`;
}

function stripHtml(html) {
  return String(html || '')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseCsvPatterns(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function wildcardToRegExp(pattern) {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`, 'i');
}

function senderAllowed(sender, patterns) {
  if (!patterns.length) {
    return true;
  }

  const normalized = normalizeEmail(sender);
  return patterns.some((pattern) => wildcardToRegExp(pattern).test(normalized));
}

function loadConfig(env) {
  const ttl = Number.parseInt(env.OTP_TTL_SECONDS || `${DEFAULT_TTL_SECONDS}`, 10);
  const token = String(env.API_TOKEN || '').trim();
  if (!token) {
    throw new Error('API_TOKEN is required');
  }

  return {
    apiToken: token,
    ttlSeconds: Number.isFinite(ttl) && ttl > 0 ? ttl : DEFAULT_TTL_SECONDS,
    allowedSenders: parseCsvPatterns(env.ALLOWED_SENDERS),
    rejectUntrustedSenders: String(env.REJECT_UNTRUSTED_SENDERS || 'false').toLowerCase() === 'true',
    allowAlphanumericCodes: String(env.ALLOW_ALPHANUMERIC_CODES || 'false').toLowerCase() === 'true',
    codeRegex: new RegExp(env.OTP_CODE_REGEX || DEFAULT_CODE_REGEX, 'i'),
  };
}

function extractOtpFromText(parts, config) {
  const text = parts.filter(Boolean).join('\n');
  for (const pattern of KEYWORD_REGEXES) {
    const keywordMatch = text.match(pattern);
    if (keywordMatch?.[1]) {
      return keywordMatch[1].toUpperCase();
    }
  }

  const directMatch = text.match(config.codeRegex);
  if (directMatch?.[1]) {
    return directMatch[1].toUpperCase();
  }

  if (config.allowAlphanumericCodes) {
    const alphaMatch = text.match(/(?<![A-Z0-9])([A-Z0-9]{6,10})(?![A-Z0-9])/i);
    if (alphaMatch?.[1]) {
      return alphaMatch[1].toUpperCase();
    }
  }

  return null;
}

async function parseEmail(message) {
  const parser = new PostalMime();
  const parsed = await parser.parse(message.raw);
  const subject = String(parsed.subject || '').trim();
  const text = String(parsed.text || '').trim();
  const html = stripHtml(parsed.html || '');

  return {
    from: normalizeEmail(parsed.from?.address || message.from),
    to: normalizeEmail(message.to),
    subject,
    text,
    html,
  };
}

function buildStorageKey(email) {
  return `otp:${normalizeEmail(email)}`;
}

function unauthorized() {
  return json({ error: 'unauthorized' }, { status: 401 });
}

function notFound(email) {
  return json({ error: 'otp_not_found', email: normalizeEmail(email) }, { status: 404 });
}

async function readRequestBody(request) {
  const contentType = request.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return request.json();
  }
  return {};
}

function isAuthorized(request, config) {
  const header = request.headers.get('authorization') || '';
  return header === `Bearer ${config.apiToken}`;
}

function toPublicRecord(record) {
  return {
    email: record.email,
    code: record.code,
    subject: record.subject,
    sender: record.sender,
    receivedAt: record.receivedAt,
    expiresAt: record.expiresAt,
  };
}

async function handleFetch(request, env) {
  const config = loadConfig(env);
  const url = new URL(request.url);

  if (url.pathname === '/health') {
    return json({ ok: true, service: 'email-otp-worker' });
  }

  if (!isAuthorized(request, config)) {
    return unauthorized();
  }

  if (url.pathname === '/otp' && request.method === 'GET') {
    const email = normalizeEmail(url.searchParams.get('email'));
    if (!email) {
      return json({ error: 'email query parameter is required' }, { status: 400 });
    }

    const raw = await env.OTP_CODES.get(buildStorageKey(email));
    if (!raw) {
      return notFound(email);
    }

    return json({ ok: true, otp: toPublicRecord(JSON.parse(raw)) });
  }

  if (url.pathname === '/otp' && request.method === 'DELETE') {
    const email = normalizeEmail(url.searchParams.get('email'));
    if (!email) {
      return json({ error: 'email query parameter is required' }, { status: 400 });
    }

    await env.OTP_CODES.delete(buildStorageKey(email));
    return json({ ok: true, deleted: email });
  }

  if (url.pathname === '/otp/consume' && request.method === 'POST') {
    const body = await readRequestBody(request);
    const email = normalizeEmail(body.email);
    if (!email) {
      return json({ error: 'email is required' }, { status: 400 });
    }

    const key = buildStorageKey(email);
    const raw = await env.OTP_CODES.get(key);
    if (!raw) {
      return notFound(email);
    }

    await env.OTP_CODES.delete(key);
    return json({ ok: true, otp: toPublicRecord(JSON.parse(raw)), consumed: true });
  }

  return json({ error: 'not_found' }, { status: 404 });
}

async function handleEmail(message, env) {
  const config = loadConfig(env);
  const parsed = await parseEmail(message);

  if (!senderAllowed(parsed.from, config.allowedSenders)) {
    console.log(`Ignored inbound mail from ${maskEmail(parsed.from)} to ${maskEmail(parsed.to)}`);
    if (config.rejectUntrustedSenders) {
      message.setReject('Sender is not allowlisted for OTP ingestion');
    }
    return;
  }

  const code = extractOtpFromText([parsed.subject, parsed.text, parsed.html], config);
  if (!code) {
    console.log(`No OTP extracted for ${maskEmail(parsed.to)} from ${maskEmail(parsed.from)}`);
    return;
  }

  const receivedAt = new Date().toISOString();
  const expiresAt = new Date(Date.now() + config.ttlSeconds * 1000).toISOString();
  const record = {
    email: parsed.to,
    code,
    subject: parsed.subject,
    sender: parsed.from,
    receivedAt,
    expiresAt,
  };

  await env.OTP_CODES.put(buildStorageKey(parsed.to), JSON.stringify(record), {
    expirationTtl: config.ttlSeconds,
  });

  console.log(`Stored OTP for ${maskEmail(parsed.to)} from ${maskEmail(parsed.from)}`);
}

export {
  buildStorageKey,
  extractOtpFromText,
  loadConfig,
  normalizeEmail,
  parseCsvPatterns,
  senderAllowed,
  stripHtml,
};

export default {
  email(message, env, ctx) {
    ctx.waitUntil(handleEmail(message, env));
  },
  fetch(request, env) {
    return handleFetch(request, env);
  },
};
