/**
 * 인스타그램 댓글/DM 자동응답 Cloudflare Worker.
 *
 * 흐름:
 *   1) 카드뉴스 발행 시 scheduler.py/publish_reel.py 가 media_id ↔ {keyword, topic}
 *      를 KV(DM_KEYWORDS)에 등록해둔다 (cardnews-system/dm_registry.py 참고).
 *   2) 새 댓글이 달리면 Meta 가 이 Worker 로 webhook POST.
 *   3) 댓글이 달린 media_id 로 KV 조회 → 댓글 텍스트에 등록된 키워드가
 *      포함돼 있으면 private reply(=그 사람 DM 함)로 자동응답.
 *   4) 사람이 DM 을 직접 보내도(echo 아닌 inbound 메시지) 웰컴 메시지로 자동응답.
 *
 * 필요 환경변수/secret (wrangler.toml [vars] 또는 `wrangler secret put`):
 *   META_ACCESS_TOKEN     (secret) IG 시스템 사용자 토큰. instagram_manage_comments,
 *                          instagram_manage_messages 스코프 필요.
 *   META_APP_SECRET       (secret) Meta 앱 시크릿 — 웹훅 서명(X-Hub-Signature-256) 검증용.
 *   WEBHOOK_VERIFY_TOKEN  (secret) Meta 웹훅 등록 시 넣는 임의 문자열. 직접 정하면 됨.
 *   IG_USER_ID            (var)    우리 IG 비즈니스 계정 ID. 자기 자신 echo 필터링용.
 *   COMMENT_REPLY_TEMPLATE (var)   댓글 키워드 매칭 시 보낼 private reply 템플릿. {topic} 치환.
 *   DM_WELCOME_TEMPLATE    (var)   DM 을 직접 보냈을 때 보낼 웰컴 메시지.
 *
 * KV 바인딩: DM_KEYWORDS (wrangler.toml [[kv_namespaces]])
 */

const GRAPH = "https://graph.facebook.com/v21.0";

const DEFAULT_COMMENT_REPLY =
  "안녕하세요! '{topic}' 관련해서 궁금해하신 내용 보내드릴게요 :) " +
  "어떤 상황이신지 편하게 말씀해주시면 제가 맞춤으로 답변 드릴게요!";
const DEFAULT_DM_WELCOME =
  "안녕하세요! 문의 주셔서 감사해요 :) 어떤 게 궁금하신지 편하게 남겨주시면 확인 후 답변 드릴게요!";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET") {
      return handleVerification(url, env);
    }
    if (request.method === "POST") {
      // 서명 검증에는 raw body 문자열이 필요 — JSON.parse 전에 먼저 텍스트로 읽는다.
      const rawBody = await request.text();
      const signature = request.headers.get("X-Hub-Signature-256") || "";
      const valid = await verifySignature(rawBody, signature, env.META_APP_SECRET);
      if (!valid) {
        console.log("[webhook] invalid signature, rejecting");
        return new Response("invalid signature", { status: 403 });
      }

      let payload;
      try {
        payload = JSON.parse(rawBody);
      } catch (e) {
        console.log("[webhook] JSON parse 실패", e);
        return new Response("bad json", { status: 400 });
      }

      // Meta 는 몇 초 안에 200 을 못 받으면 재전송한다. 실제 응답 발송은
      // waitUntil 로 뒤로 미루고 먼저 200 을 반환한다.
      ctx.waitUntil(processPayload(payload, env));
      return new Response("ok", { status: 200 });
    }

    return new Response("method not allowed", { status: 405 });
  },
};

function handleVerification(url, env) {
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");
  if (mode === "subscribe" && token === env.WEBHOOK_VERIFY_TOKEN && challenge) {
    return new Response(challenge, { status: 200 });
  }
  return new Response("verification failed", { status: 403 });
}

async function verifySignature(rawBody, signatureHeader, appSecret) {
  if (!appSecret || !signatureHeader.startsWith("sha256=")) return false;
  const expectedHex = signatureHeader.slice("sha256=".length);

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(appSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const computedHex = [...new Uint8Array(sigBuf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  return timingSafeEqual(computedHex, expectedHex);
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function processPayload(payload, env) {
  const entries = payload.entry || [];
  for (const entry of entries) {
    for (const change of entry.changes || []) {
      if (change.field === "comments") {
        await handleComment(change.value, env).catch((e) =>
          console.log("[webhook] handleComment 실패", e)
        );
      }
    }
    for (const msg of entry.messaging || []) {
      await handleMessage(msg, env).catch((e) =>
        console.log("[webhook] handleMessage 실패", e)
      );
    }
  }
}

async function alreadySeen(env, id) {
  if (!id || !env.DM_KEYWORDS) return false;
  const key = `seen:${id}`;
  const hit = await env.DM_KEYWORDS.get(key);
  if (hit) return true;
  await env.DM_KEYWORDS.put(key, "1", { expirationTtl: 3600 });
  return false;
}

async function handleComment(value, env) {
  if (!value || !value.text || !value.media || !value.media.id) return;
  if (value.from && env.IG_USER_ID && String(value.from.id) === String(env.IG_USER_ID)) {
    return; // 우리 계정이 단 댓글(예: public reply) — 무시
  }
  if (await alreadySeen(env, `comment:${value.id}`)) return;

  const info = await env.DM_KEYWORDS.get(value.media.id, { type: "json" });
  if (!info || !info.keyword) return;

  const keyword = info.keyword.replace(/['"]/g, "").trim();
  if (!keyword || !value.text.includes(keyword)) return;

  const template = env.COMMENT_REPLY_TEMPLATE || DEFAULT_COMMENT_REPLY;
  const replyText = template.replace("{topic}", info.topic || "이 내용");

  await sendPrivateReply(value.id, replyText, env);
}

async function handleMessage(msg, env) {
  if (!msg || !msg.message) return;
  if (msg.message.is_echo) return; // 우리 쪽에서 보낸 메시지 — 무시 (안 그러면 무한루프)
  if (!msg.sender || !msg.sender.id) return;
  if (await alreadySeen(env, `msg:${msg.message.mid || msg.sender.id + msg.timestamp}`)) return;

  const template = env.DM_WELCOME_TEMPLATE || DEFAULT_DM_WELCOME;
  await sendDirectMessage(msg.sender.id, template, env);
}

async function sendPrivateReply(commentId, message, env) {
  const body = new URLSearchParams({ message, access_token: env.META_ACCESS_TOKEN });
  const r = await fetch(`${GRAPH}/${commentId}/private_replies`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) {
    console.log(`[webhook] private_replies 실패 (comment=${commentId}): ${r.status} ${await r.text()}`);
  } else {
    console.log(`[webhook] private reply 전송 완료 (comment=${commentId})`);
  }
}

async function sendDirectMessage(recipientId, text, env) {
  const body = new URLSearchParams({
    recipient: JSON.stringify({ id: recipientId }),
    message: JSON.stringify({ text }),
    access_token: env.META_ACCESS_TOKEN,
  });
  const r = await fetch(`${GRAPH}/${env.IG_USER_ID}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) {
    console.log(`[webhook] messages 전송 실패 (to=${recipientId}): ${r.status} ${await r.text()}`);
  } else {
    console.log(`[webhook] DM 전송 완료 (to=${recipientId})`);
  }
}
