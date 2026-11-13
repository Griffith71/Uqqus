// Files in end-to-end encrypted chats (1:1 chats and groups): pictures, videos, sound, voice notes and any other file.
// The browser encrypts the file the way Matrix does (an EncryptedFile: AES-CTR with a 256-bit key, a 128-bit counter
// whose last 64 bits start at zero, and the SHA-256 of the encrypted bytes), uploads the encrypted bytes to the sender's
// own linked storage through the site (helpers/media/rules.py CHAT: only the chat's members may fetch them), and sends
// the key inside the encrypted message. The site never sees the key or what the file holds. A reader's browser fetches
// the encrypted bytes, checks their hash, decrypts them and shows them. A picture is drawn again first, which drops
// what its camera wrote into it (the place it was taken). Everything here is set as text or as a blob address.

const MAX_PICTURE_SIDE = 4096;

function b64(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/=+$/, "");
}

function unb64(text) {
  const clean = String(text || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = clean + "===".slice((clean.length + 3) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

// An EncryptedFile without its url: { file, data } where data is the encrypted bytes
export async function encryptFile(plain) {
  const key = await crypto.subtle.generateKey({ name: "AES-CTR", length: 256 }, true, ["encrypt", "decrypt"]);
  const iv = new Uint8Array(16);
  crypto.getRandomValues(iv.subarray(0, 8));             // the counter's last 64 bits start at zero (the Matrix spec)
  const data = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-CTR", counter: iv, length: 64 }, key, plain));
  const jwk = await crypto.subtle.exportKey("jwk", key);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  return {
    data,
    file: {
      v: "v2",
      key: { kty: "oct", key_ops: ["encrypt", "decrypt"], alg: "A256CTR", k: jwk.k, ext: true },
      iv: b64(iv),
      hashes: { sha256: b64(hash) },
    },
  };
}

export class FileGone extends Error {}

// The plain bytes of an EncryptedFile's encrypted `data`, or a thrown error when its hash doesn't match (the bytes were
// changed: never shown)
export async function decryptFile(data, file) {
  if (!file || !file.key || file.key.alg !== "A256CTR" || !file.iv || !file.hashes || !file.hashes.sha256) {
    throw new Error("not an encrypted file");
  }
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  if (b64(hash) !== String(file.hashes.sha256).replace(/=+$/, "")) throw new Error("hash mismatch");
  const key = await crypto.subtle.importKey("jwk", { kty: "oct", alg: "A256CTR", k: file.key.k, ext: true },
    { name: "AES-CTR" }, false, ["decrypt"]);
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-CTR", counter: unb64(file.iv), length: 64 }, key, data));
}

// A picture drawn again (nothing but its pixels), as { blob, w, h, type }; null when it can't be (not a picture this
// browser draws): it is then sent as a file, never as itself
export async function redrawPicture(file) {
  if (!/^image\/(jpeg|png|webp)$/.test(file.type) || typeof createImageBitmap !== "function") return null;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_PICTURE_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    if (bitmap.close) bitmap.close();
    const type = file.type === "image/png" ? "image/png" : "image/jpeg";
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, type, 0.9));
    return blob ? { blob, w: canvas.width, h: canvas.height, type } : null;
  } catch (e) {
    return null;
  }
}

// what kind of message a file makes, from its type (a type nobody can play inline is a plain file)
function msgtypeOf(type, voice) {
  if (voice) return "m.audio";
  if (/^image\/(jpeg|png|webp|gif)$/.test(type)) return "m.image";
  if (/^video\/(mp4|webm)$/.test(type)) return "m.video";
  if (/^audio\/(mpeg|mp4|ogg|webm|wav|flac|aac)$/.test(type)) return "m.audio";
  return "m.file";
}

// The message content for a file: encrypted, uploaded through the site (window.MediaUpload, kind "chat", into this
// room), never sent until the upload is checked. `onProgress(fraction)`, `voice` for a recorded voice note
// ({ duration } in ms). A picture that can't be drawn again is sent as a plain file.
export async function fileContent(file, roomId, { onProgress, voice = null, name = null } = {}) {
  let bytes = file;
  let type = file.type || "application/octet-stream";
  const info = {};
  if (!voice && /^image\//.test(type) && type !== "image/gif") {
    const drawn = await redrawPicture(file);
    if (drawn) {
      bytes = drawn.blob;
      type = drawn.type;
      info.w = drawn.w;
      info.h = drawn.h;
    } else {
      type = "application/octet-stream";               // never the original picture, which may say where it was taken
    }
  }
  const plain = new Uint8Array(await bytes.arrayBuffer());
  const { data, file: encrypted } = await encryptFile(plain);
  const asset = await window.MediaUpload.upload(new File([data], "file.bin", { type: "application/octet-stream" }), "chat",
    { fields: { room: roomId }, onProgress });
  if (!asset || typeof asset.path !== "string" || !asset.path.startsWith("/media/")) throw new Error(window.tr("The upload did not go through. Try again."));
  info.mimetype = type;
  info.size = plain.length;
  if (voice && voice.duration) info.duration = Math.round(voice.duration);
  const content = {
    msgtype: msgtypeOf(type, !!voice),
    body: name || file.name || window.tr("File"),
    info,
    file: { ...encrypted, url: asset.path, mimetype: type },
  };
  if (voice) content["org.matrix.msc3245.voice"] = {};
  return content;
}

// --- showing ----------------------------------------------------------------------------------------------------

// a message that carries an encrypted file of this site: { msgtype, name, size, type, file, voice }, else null
export function attachmentOf(content) {
  if (!content || !["m.image", "m.video", "m.audio", "m.file"].includes(content.msgtype)) return null;
  const file = content.file;
  if (!file || typeof file.url !== "string" || !/^\/media\/[0-9a-z]{1,13}\/[A-Za-z0-9_-]{16,64}\.bin$/.test(file.url)) return null;
  const info = content.info && typeof content.info === "object" ? content.info : {};
  return {
    msgtype: content.msgtype,
    name: typeof content.body === "string" ? content.body.slice(0, 200) : "",
    size: Number(info.size) || 0,
    type: typeof info.mimetype === "string" ? info.mimetype : "",
    w: Number(info.w) || 0,
    h: Number(info.h) || 0,
    file,
    voice: !!content["org.matrix.msc3245.voice"],
  };
}

// what a blob is shown as: only these types are given to the page as themselves; anything else is a download
const SHOWN = /^(image\/(jpeg|png|webp|gif)|video\/(mp4|webm)|audio\/(mpeg|mp4|ogg|webm|wav|flac|aac))$/;

const opened = new Map();          // url -> Promise<{ url } | { gone } | { failed }>: each file fetched and decrypted once a page

export function openFile(attachment) {
  const key = attachment.file.url;
  if (!opened.has(key)) {
    opened.set(key, (async () => {
      const resp = await fetch(attachment.file.url, { credentials: "same-origin" });
      if (resp.status === 404 || resp.status === 410) return { gone: true };
      if (!resp.ok) throw new Error(String(resp.status));
      const plain = await decryptFile(new Uint8Array(await resp.arrayBuffer()), attachment.file);
      const type = SHOWN.test(attachment.type) ? attachment.type : "application/octet-stream";
      return { url: URL.createObjectURL(new Blob([plain], { type })) };
    })().catch(() => {
      opened.delete(key);                // a failure (offline) is asked again next time; a gone file is said
      return { failed: true };
    }));
  }
  return opened.get(key);
}

// A received file as a File again (fetched and decrypted here), to send on: a forward is encrypted afresh and kept in the
// forwarder's own storage, with the new chat (the site serves a chat file only to its own chat's members). Throws FileGone.
export async function fileAgain(attachment) {
  const resp = await fetch(attachment.file.url, { credentials: "same-origin" });
  if (resp.status === 404 || resp.status === 410) throw new FileGone(window.tr("This file is no longer available."));
  if (!resp.ok) throw new Error(window.tr("The file couldn't be sent. Try again."));
  const plain = await decryptFile(new Uint8Array(await resp.arrayBuffer()), attachment.file);
  return new File([plain], attachment.name || "file", { type: attachment.type || "application/octet-stream" });
}

function sizeText(bytes) {
  if (bytes >= 1024 * 1024) return window.tr("%(size)s MB", { size: (bytes / 1024 / 1024).toFixed(1) });
  return window.tr("%(size)s KB", { size: Math.max(1, Math.round(bytes / 1024)) });
}

// The attachment drawn into `box`: a picture, a player or a file, each with a Download link, once its bytes are open
export function drawAttachment(box, attachment) {
  box.classList.add("chat-file");
  const note = document.createElement("div");
  note.className = "small text-muted";
  note.textContent = window.tr("Opening the file…");
  box.replaceChildren(note);
  openFile(attachment).then((result) => {
    if (!box.isConnected && !box.parentNode) return;
    if (result.gone) {
      note.textContent = window.tr("This file is no longer available.");
      return;
    }
    if (result.failed) {
      note.textContent = window.tr("This file couldn't be opened. Try again later.");
      return;
    }
    const shown = SHOWN.test(attachment.type);
    let media = null;
    if (shown && attachment.msgtype === "m.image") {
      media = document.createElement("img");
      media.alt = attachment.name;
      media.className = "chat-file-picture";
      if (attachment.w && attachment.h) {
        media.width = attachment.w;
        media.height = attachment.h;
      }
    } else if (shown && attachment.msgtype === "m.video") {
      media = document.createElement("video");
      media.controls = true;
      media.preload = "metadata";
      media.className = "chat-file-video";
    } else if (shown && attachment.msgtype === "m.audio") {
      media = document.createElement("audio");
      media.controls = true;
      media.preload = "metadata";
      media.className = "chat-file-audio";
    }
    if (media) media.src = result.url;
    const line = document.createElement("div");
    line.className = "small d-flex align-items-center flex-wrap chat-file-line";
    const name = document.createElement("span");
    name.className = "text-truncate mr-2 chat-file-name";
    name.textContent = attachment.voice ? window.tr("Voice note") : attachment.name;
    const size = document.createElement("span");
    size.className = "text-muted mr-2";
    size.textContent = attachment.size ? sizeText(attachment.size) : "";
    const save = document.createElement("a");
    save.href = result.url;
    save.download = attachment.name || "file";
    save.textContent = window.tr("Download");
    line.append(name, size, save);
    box.replaceChildren(...(media ? [media, line] : [line]));
  });
}

// --- voice notes --------------------------------------------------------------------------------------------------

// A recording from the microphone (Opus in WebM or Ogg, as the browser makes it). `start()` asks for the microphone and
// begins; `stop()` resolves { file, duration }; `cancel()` drops it. The microphone is let go either way.
export function recorder() {
  let stream = null;
  let rec = null;
  let chunks = [];
  let began = 0;
  const release = () => { if (stream) stream.getTracks().forEach((t) => t.stop()); stream = null; };
  return {
    async start() {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
      rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      chunks = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.start();
      began = Date.now();
    },
    stop() {
      return new Promise((resolve) => {
        if (!rec) { release(); resolve(null); return; }
        rec.onstop = () => {
          release();
          const base = (rec.mimeType || "audio/webm").split(";")[0];
          const ext = base === "audio/ogg" ? "ogg" : "webm";
          resolve({ file: new File(chunks, `voice-note.${ext}`, { type: base }), duration: Date.now() - began });
        };
        rec.stop();
      });
    },
    cancel() {
      try { if (rec && rec.state !== "inactive") rec.stop(); } catch (e) { /* already stopped */ }
      release();
    },
  };
}
