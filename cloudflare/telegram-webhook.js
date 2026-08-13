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

    const callback = update?.callback_query;
    if (callback) return await handleCallback(callback, env);

    const message = update?.message;
    const chatId = message?.chat?.id;
    if (!chatId) return new Response("OK", { status: 200 });

    let fileId = null;
    let filename = "";
    let videoNote = "";
    let textInput = "";
    let inputType = "text";

    if (message.video) {
      fileId = message.video.file_id;
      filename = `telegram_${update.update_id}.mp4`;
      videoNote = String(message.caption || "").trim();
      inputType = "video";
    } else if (message.document && (message.document.mime_type || "").startsWith("video/")) {
      fileId = message.document.file_id;
      filename = message.document.file_name || `telegram_${update.update_id}.mp4`;
      videoNote = String(message.caption || "").trim();
      inputType = "video";
    } else if (message.text && message.text !== "/start") {
      textInput = String(message.text).trim();
      inputType = "text";
    } else {
      if (message.text === "/start") {
        await telegram(env, "sendMessage", { chat_id: chatId, text: "🤖 Reels Asistanı hazır.\n\n🎥 Video gönderirsen videoyu düzenleyip final videoyu üretirim.\n📝 Sadece metin gönderirsen ses + açıklama + başlık + Threads üretirim.\n\nVideoya açıklama/not eklemek için videoyu Telegram'da açıklamayla birlikte gönder." });
      }
      return new Response("OK", { status: 200 });
    }

    const updateId = String(update.update_id);
    const pendingPath = `data/pending/${updateId}.json`;
    const pending = { file_id: fileId, chat_id: String(chatId), filename, video_note: videoNote, text_input: textInput, input_type: inputType };

    const saved = await githubPut(env, pendingPath, JSON.stringify(pending, null, 2), `Queue Telegram ${inputType} ${updateId}`);
    if (!saved.ok) {
      const body = await saved.text();
      await telegram(env, "sendMessage", { chat_id: chatId, text: `❌ Girdi kuyruğa alınamadı.\n\n${body.slice(0, 1000)}` });
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

    const intro = inputType === "video"
      ? `📥 Videonu aldım.${videoNote ? `\n📝 Notunu da aldım: ${videoNote}` : ""}\n\n🎯 İçerik türünü seç, seçimine göre üretim başlayacak:`
      : "📝 Metnini aldım.\n\n🎯 İçerik türünü seç, seçimine göre ses + açıklama + başlık + Threads üretilecek:";
    await telegram(env, "sendMessage", { chat_id: chatId, text: intro, reply_markup: keyboard });

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

  try {
    const pendingResponse = await githubGet(env, pendingPath);
    if (!pendingResponse.ok) {
      await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: `Girdi beklemede değil (${pendingResponse.status}).` });
      await safeEdit(env, chatId, callback.message.message_id, `❌ Girdi bilgisi alınamadı.\n\nGitHub: ${pendingResponse.status}`);
      return new Response("Pending lookup failed", { status: 502 });
    }

    const pending = JSON.parse(await decodeGitHubContent(await pendingResponse.json()));
    const labels = { eglence: "🎭 Eğlence Ağırlıklı", dengeli: "⚖️ Dengeli", bilgi: "🧠 Bilgi Ağırlıklı", teknik: "📊 Teknik / Detaylı" };
    const isVideo = pending.input_type === "video" && !!pending.file_id;

    await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: `${labels[tone]} seçildi. Pipeline başlıyor.` });
    await safeEdit(env, chatId, callback.message.message_id, `${isVideo ? "🎥 Video" : "📝 Metin"}\n🎯 İçerik türü: ${labels[tone]}\n\n⏳ Reels pipeline başlatılıyor...\n\n🔄 GitHub Actions bekleniyor...`);

    const dispatch = await fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/actions/workflows/telegram-video.yml/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rlstg-asist-telegram-webhook"
      },
      body: JSON.stringify({ ref: "main", inputs: {
        telegram_file_id: pending.file_id || "",
        telegram_chat_id: String(pending.chat_id),
        telegram_filename: pending.filename || "",
        telegram_update_id: updateId,
        content_tone: tone,
        video_note: pending.video_note || "",
        text_input: pending.text_input || ""
      }})
    });

    if (!dispatch.ok) {
      const body = await dispatch.text();
      await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: `GitHub hatası ${dispatch.status}` });
      await safeEdit(env, chatId, callback.message.message_id, `❌ GitHub pipeline başlatılamadı.\n\nHTTP ${dispatch.status}\n${body.slice(0, 1500)}`);
      return new Response("GitHub dispatch failed", { status: 502 });
    }

    await safeEdit(env, chatId, callback.message.message_id, `${isVideo ? "🎥 Video" : "📝 Metin"}\n🎯 İçerik türü: ${labels[tone]}\n\n⏳ Reels pipeline çalışıyor...\n\n🟢 GitHub Actions tetiklendi.`);

    const pendingMeta = await githubGet(env, pendingPath);
    if (pendingMeta.ok) {
      const meta = await pendingMeta.json();
      const deleted = await githubDelete(env, pendingPath, meta.sha, `Remove processed Telegram input ${updateId}`);
      if (!deleted.ok) console.log("Pending cleanup failed", deleted.status, await deleted.text());
    }

    return new Response("OK", { status: 200 });
  } catch (error) {
    const detail = String(error?.message || error).slice(0, 1500);
    try {
      await telegram(env, "answerCallbackQuery", { callback_query_id: callback.id, text: "Pipeline başlatılırken hata oluştu." });
      await safeEdit(env, chatId, callback.message.message_id, `❌ PIPELINE BAŞLATILAMADI\n\n${detail}`);
    } catch (notifyError) {
      console.log("Callback error notification failed", String(notifyError));
    }
    console.log("Callback handler error", detail);
    return new Response("Callback handler failed", { status: 500 });
  }
}

async function safeEdit(env, chatId, messageId, text) {
  try {
    return await telegram(env, "editMessageText", { chat_id: chatId, message_id: messageId, text });
  } catch (error) {
    console.log("editMessageText failed", String(error));
    return null;
  }
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
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/contents/${path}`, { headers: await githubHeaders(env) });
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
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

async function decodeGitHubContent(json) {
  const binary = atob(String(json.content || "").replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

async function telegram(env, method, payload) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload)
  });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, description: text }; }
  if (!response.ok || !data.ok) throw new Error(`Telegram API ${response.status}: ${text}`);
  return data;
}
