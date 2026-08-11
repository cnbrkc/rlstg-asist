export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/setup") {
      const webhookUrl = `${url.origin}/`;
      const response = await telegram(env, "setWebhook", {
        url: webhookUrl,
        secret_token: env.TELEGRAM_WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"],
        drop_pending_updates: true
      });
      return new Response(
        response.ok
          ? "OK - Telegram webhook aktif.\n" + JSON.stringify(response.result)
          : "ERROR - " + JSON.stringify(response),
        { status: response.ok ? 200 : 500, headers: { "content-type": "text/plain; charset=utf-8" } }
      );
    }

    if (request.method !== "POST") return new Response("RLSTG Telegram Webhook OK", { status: 200 });

    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try { update = await request.json(); }
    catch { return new Response("Bad JSON", { status: 400 }); }

    // User selected the content style.
    const callback = update?.callback_query;
    if (callback) {
      return await handleCallback(callback, env);
    }

    const message = update?.message;
    const chatId = message?.chat?.id;
    if (!chatId) return new Response("OK", { status: 200 });

    let fileId = null;
    let filename = "telegram_video.mp4";

    if (message.video) {
      fileId = message.video.file_id;
      filename = `telegram_${update.update_id}.mp4`;
    } else if (message.document && (message.document.mime_type || "").startsWith("video/")) {
      fileId = message.document.file_id;
      filename = message.document.file_name || `telegram_${update.update_id}.mp4`;
    } else {
      if (message.text === "/start") {
        await telegram(env, "sendMessage", {
          chat_id: chatId,
          text: "🤖 Reels Asistanı hazır. Bana bir video gönder."
        });
      }
      return new Response("OK", { status: 200 });
    }

    const updateId = String(update.update_id);
    const pendingPath = `data/pending/${updateId}.json`;
    const pending = { file_id: fileId, chat_id: String(chatId), filename };

    const saved = await githubPut(env, pendingPath, JSON.stringify(pending, null, 2), `Queue Telegram video ${updateId}`);
    if (!saved.ok) {
      const body = await saved.text();
      await telegram(env, "sendMessage", {
        chat_id: chatId,
        text: `❌ Video kuyruğa alınamadı.\n\n${body.slice(0, 1000)}`
      });
      return new Response("Pending save failed", { status: 502 });
    }

    const keyboard = {
      inline_keyboard: [
        [
          { text: "🎭 Eğlenceli", callback_data: `mode:eglence:${updateId}` },
          { text: "⚖️ Dengeli", callback_data: `mode:dengeli:${updateId}` }
        ],
        [
          { text: "🧠 Bilgi Ağırlıklı", callback_data: `mode:bilgi:${updateId}` },
          { text: "📊 Teknik / Detaylı", callback_data: `mode:teknik:${updateId}` }
        ]
      ]
    };

    await telegram(env, "sendMessage", {
      chat_id: chatId,
      text: "📥 Videonu aldım.\n\n🎯 İçerik türünü seç, seçimine göre ilgili prompt kuralları uygulanarak üretim başlayacak:",
      reply_markup: keyboard
    });

    return new Response("OK", { status: 200 });
  }
};

async function handleCallback(callback, env) {
  const chatId = callback?.message?.chat?.id;
  const data = String(callback?.data || "");
  const match = data.match(/^mode:(eglence|dengeli|bilgi|teknik):(\d+)$/);

  if (!chatId || !match) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: "Geçersiz seçim." });
    return new Response("OK", { status: 200 });
  }

  const tone = match[1];
  const updateId = match[2];
  const pendingPath = `data/pending/${updateId}.json`;
  const pendingResponse = await githubGet(env, pendingPath);

  if (!pendingResponse.ok) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: "Bu video artık beklemede değil." });
    return new Response("OK", { status: 200 });
  }

  const pending = JSON.parse(await decodeGitHubContent(await pendingResponse.json()));
  const labels = {
    eglence: "🎭 Eğlence Ağırlıklı",
    dengeli: "⚖️ Dengeli",
    bilgi: "🧠 Bilgi Ağırlıklı",
    teknik: "📊 Teknik / Detaylı"
  };

  await telegram(env, "answerCallbackQuery", {
    callback_query_id: callback.id,
    text: `${labels[tone]} seçildi. Pipeline başlıyor.`
  });

  await telegram(env, "editMessageText", {
    chat_id: chatId,
    message_id: callback.message.message_id,
    text: `🎯 İçerik türü: ${labels[tone]}\n\n⏳ Reels pipeline başlatılıyor...`
  });

  const dispatch = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPOSITORY}/actions/workflows/telegram-video.yml/dispatches`,
    {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rlstg-asist-telegram-webhook"
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          telegram_file_id: pending.file_id,
          telegram_chat_id: String(pending.chat_id),
          telegram_filename: pending.filename,
          telegram_update_id: updateId,
          content_tone: tone
        }
      })
    }
  );

  if (!dispatch.ok) {
    const body = await dispatch.text();
    await telegram(env, "editMessageText", {
      chat_id: chatId,
      message_id: callback.message.message_id,
      text: `❌ GitHub pipeline başlatılamadı.\n\n${body.slice(0, 1000)}`
    });
    return new Response("GitHub dispatch failed", { status: 502 });
  }

  // Delete the one-time pending record after successful dispatch.
  const pendingMeta = await githubGet(env, pendingPath);
  if (pendingMeta.ok) {
    const meta = await pendingMeta.json();
    await githubDelete(env, pendingPath, meta.sha, `Remove processed Telegram video ${updateId}`);
  }

  return new Response("OK", { status: 200 });
}

async function githubHeaders(env) {
  return {
    "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "rlstg-asist-telegram-webhook"
  };
}

async function githubGet(env, path) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/contents/${path}`, {
    headers: await githubHeaders(env)
  });
}

async function githubPut(env, path, text, message) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/contents/${path}`, {
    method: "PUT",
    headers: { ...await githubHeaders(env), "content-type": "application/json" },
    body: JSON.stringify({ message, content: base64Encode(text), branch: "main" })
  });
}

async function githubDelete(env, path, sha, message) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/contents/${path}`, {
    method: "DELETE",
    headers: { ...await githubHeaders(env), "content-type": "application/json" },
    body: JSON.stringify({ message, sha, branch: "main" })
  });
}

function base64Encode(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function decodeGitHubContent(json) {
  const binary = atob(String(json.content || "").replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

async function telegram(env, method, payload) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, description: text }; }
  if (!response.ok || !data.ok) throw new Error(`Telegram API ${response.status}: ${text}`);
  return data;
}
