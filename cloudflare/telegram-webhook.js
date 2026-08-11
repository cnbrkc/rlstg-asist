export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Bad JSON", { status: 400 });
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
      // Keep the bot responsive for commands/text without starting a pipeline.
      if (message.text === "/start") {
        await telegram(env, "sendMessage", { chat_id: chatId, text: "🤖 Reels Asistanı hazır. Bana bir video gönder." });
      }
      return new Response("OK", { status: 200 });
    }

    // Immediately acknowledge the upload. The heavy work stays in GitHub Actions.
    await telegram(env, "sendMessage", {
      chat_id: chatId,
      text: "📥 Videonu aldım. Reels pipeline başlatılıyor..."
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
            telegram_file_id: fileId,
            telegram_chat_id: String(chatId),
            telegram_filename: filename,
            telegram_update_id: String(update.update_id)
          }
        })
      }
    );

    if (!dispatch.ok) {
      const body = await dispatch.text();
      await telegram(env, "sendMessage", {
        chat_id: chatId,
        text: `❌ GitHub pipeline başlatılamadı.\n\n${body.slice(0, 1000)}`
      });
      return new Response("GitHub dispatch failed", { status: 502 });
    }

    return new Response("OK", { status: 200 });
  }
};

async function telegram(env, method, payload) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(`Telegram API ${response.status}`);
  return response;
}
