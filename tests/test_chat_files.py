"""Files in end-to-end encrypted chats (CLAUDE.md "Files in chats"): the media rules for the `chat` kind
(helpers/media/rules.py), the browser's encryption (assets/chat_src/src/files.js, run under node with the real WebCrypto:
Matrix's EncryptedFile, a changed byte never decrypts), and the wiring that keeps the promises: a chat file is never part
of a post, only a member of its chat fetches it, it is never cached, the microphone is the chat page's alone."""
from pathlib import Path

import pytest

from chat_node_harness import SRC, node, run
from ruqqus.helpers import device_access
from ruqqus.helpers.media import rules

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules ------------------------------------------------------------------------------------------------------

def test_a_chat_file_is_any_file_up_to_its_own_limit_and_is_kept_as_bytes():
    assert rules.check_request(rules.CHAT, "file.bin", 1) == "bin"
    assert rules.check_request(rules.CHAT, "holiday.exe", rules.CHAT_SIZE_MAX) == "bin"         # the name never matters
    with pytest.raises(rules.MediaError) as too_big:
        rules.check_request(rules.CHAT, "file.bin", rules.CHAT_SIZE_MAX + 1)
    assert too_big.value.code == 413
    with pytest.raises(rules.MediaError):
        rules.check_request(rules.CHAT, "file.bin", 0)
    assert rules.content_type("bin") == "application/octet-stream" and rules.CHAT not in rules.KINDS
    # where the member's pictures go: Drive or OneDrive (and the test storage), never a video site
    assert {s: rules.provider_for(s, rules.CHAT) for s in ("google", "microsoft", "dailymotion", "dev")} == \
        {"google": "gdrive", "microsoft": "onedrive", "dailymotion": None, "dev": "dev"}


def test_the_microphone_is_the_chat_pages_alone_and_never_the_camera():
    assert "microphone=(self)" in device_access.permissions_policy("/chat")
    assert "camera=()" in device_access.permissions_policy("/chat")
    assert "microphone=()" in device_access.permissions_policy("/")


# --- the browser's encryption ---------------------------------------------------------------------------------------

FILES = """
const F = await import(SRC + "/files.js");
"""


@node
def test_a_file_encrypts_as_matrix_does_and_only_its_own_bytes_decrypt():
    got = run(FILES + """
const plain = new TextEncoder().encode("the words of a private file");
const { data, file } = await F.encryptFile(plain);
const back = new TextDecoder().decode(await F.decryptFile(data, file));
const iv = Uint8Array.from(atob(file.iv + "=".repeat((4 - file.iv.length % 4) % 4)), (c) => c.charCodeAt(0));
let changed = "decrypted";
const bad = data.slice(); bad[0] ^= 1;
try { await F.decryptFile(bad, file); } catch (e) { changed = e.message; }
let other = "decrypted";
const second = await F.encryptFile(plain);
try { await F.decryptFile(data, second.file); } catch (e) { other = e.message; }
report({ back, alg: file.key.alg, v: file.v, ivZero: Array.from(iv.slice(8)).every((b) => b === 0), ivLen: iv.length,
         differs: Array.from(data).join() !== Array.from(plain).join(), changed, other, k: file.key.k.length });
""")
    assert got["back"] == "the words of a private file" and got["differs"]
    assert (got["alg"], got["v"], got["ivLen"], got["ivZero"], got["k"]) == ("A256CTR", "v2", 16, True, 43)
    assert got["changed"] == "hash mismatch" and got["other"] == "hash mismatch"


@node
def test_only_this_sites_own_chat_file_addresses_are_shown():
    got = run(FILES + """
const file = { url: "/media/1a/AAAAAAAAAAAAAAAAAAAAAAAA.bin", key: {}, iv: "x", hashes: { sha256: "y" } };
const ok = F.attachmentOf({ msgtype: "m.image", body: "a.jpg", info: { mimetype: "image/jpeg", size: 3, w: 2, h: 1 }, file });
const offsite = F.attachmentOf({ msgtype: "m.image", body: "a", file: { ...file, url: "https://evil.example/x.bin" } });
const mxc = F.attachmentOf({ msgtype: "m.file", body: "a", file: { ...file, url: "mxc://server/abc" } });
const picture = F.attachmentOf({ msgtype: "m.image", body: "a", file: { ...file, url: "/media/1a/AAAAAAAAAAAAAAAAAAAAAAAA.jpg" } });
const text = F.attachmentOf({ msgtype: "m.text", body: "hi" });
const voice = F.attachmentOf({ msgtype: "m.audio", body: "Voice note", file, "org.matrix.msc3245.voice": {} });
report({ ok: ok && [ok.msgtype, ok.type, ok.w, ok.h], offsite, mxc, picture, text, voice: voice && voice.voice });
""")
    assert got["ok"] == ["m.image", "image/jpeg", 2, 1]
    assert got["offsite"] is None and got["mxc"] is None and got["picture"] is None and got["text"] is None
    assert got["voice"] is True


# --- the wiring -----------------------------------------------------------------------------------------------------

def test_a_chat_file_is_never_part_of_a_post_and_only_its_chats_members_fetch_it():
    attach = read("ruqqus", "helpers", "media", "attach.py")
    assert "asset.kind != rules.CHAT" in attach.split("def claimed_assets(", 1)[1].split("\ndef ", 1)[0]
    media = read("ruqqus", "routes", "media.py")
    serve = media.split("def media_file(", 1)[1].split("\ndef _post_file_access(", 1)[0]
    assert "chat_rooms.kind_for(g.db, asset.chat_room_id, v.id)" in serve and "mode = rules.PRIVATE" in serve
    assert 'age_declaration.feature_refusal(v, "chat")' in serve and "v.qr_limited" in serve
    assert '"attachment" if asset.kind == rules.CHAT else "inline"' in media
    begin = media.split("def media_upload_begin(", 1)[1].split("\n@app.", 1)[0]
    assert begin.index("if not chat_rooms.kind_for(g.db, room_id, v.id):") < begin.index("asset = MediaAsset(")
    assert "chat_room_id=room_id" in begin and "rules.CHAT_UPLOADS_PER_DAY" in begin
    complete = media.split("def media_upload_complete(", 1)[1].split("\n@app.", 1)[0]
    assert complete.index("if provider.served and asset.kind == rules.CHAT:") < complete.index("elif provider.served:")


def test_the_page_never_draws_a_file_as_html_and_redraws_pictures():
    files = (SRC / "files.js").read_text(encoding="utf-8")
    assert "innerHTML" not in files
    assert 'type = "application/octet-stream";               // never the original picture' in files
    assert "const SHOWN = /^(image\\/(jpeg|png|webp|gif)|video\\/(mp4|webm)|audio\\/" in files
    index = (SRC / "index.js").read_text(encoding="utf-8")
    # a guild channel takes no file; a public channel only its writer's picture (routes/broadcasts.py), never an encrypted file
    assert 'onAttach: isGuildChannel ? null : isChannel ? (channel && channel.public && channel.may_write ? pickChannelPicture : null) : pickFiles,' in index
    assert 'if (!navigator.mediaDevices || !window.MediaRecorder || frameRole() !== "top") return false;' in index
    assert "ensure: ensureAccount" in read("ruqqus", "assets", "js", "media_upload.js")
    # a second press while the microphone is still starting stops that recording once it has started, never a second one
    voice = index.split("async function toggleVoiceNote(button) {", 1)[1].split("\n}\n", 1)[0]
    assert voice.index("const ok = await started;") < voice.index("rec.stop()")
    assert voice.index("voiceRecording = mine;") < voice.index("if (!(await started)) {")
    assert "if (voiceRecording !== mine) return;           // pressed again while it started" in voice
