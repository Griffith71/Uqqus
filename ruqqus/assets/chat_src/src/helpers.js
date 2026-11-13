// A channel's helpers (routes/broadcasts.py broadcast_helpers, helpers/broadcasts.py MAX_HELPERS): its owner names up to three
// members who write in it too, and takes them off again. A box like chat's other boxes; everything is set as text, and the
// site checks every change again (only the owner, only a member of the channel, never more than three).
import { setChannelHelper } from "./api.js";

export function manageHelpers({ boot, channel, onChanged }) {
  let current = channel;
  const shade = document.createElement("div");
  shade.className = "chat-key-dialog";
  shade.setAttribute("role", "dialog");
  shade.setAttribute("aria-modal", "true");
  const box = document.createElement("div");
  box.className = "chat-key-box";
  const title = document.createElement("h2");
  title.className = "h5";
  title.id = `chat-helpers-title-${Date.now()}`;
  title.textContent = window.tr("Helpers");
  shade.setAttribute("aria-labelledby", title.id);
  const intro = document.createElement("p");
  intro.className = "text-small";
  intro.textContent = window.tr("Helpers write in this channel with you. Choose up to three people who are in it.");
  const list = document.createElement("div");
  list.className = "mb-2";
  const status = document.createElement("p");
  status.className = "text-small mb-1";
  status.setAttribute("role", "status");
  const form = document.createElement("form");
  form.className = "d-flex mb-2";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "form-control form-control-sm mr-2";
  input.maxLength = 30;
  input.placeholder = "@username";
  input.setAttribute("aria-label", window.tr("Add a helper by username"));
  const add = document.createElement("button");
  add.type = "submit";
  add.className = "btn btn-primary btn-sm";
  add.textContent = window.tr("Add");
  form.append(input, add);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn btn-link text-muted";
  close.textContent = window.tr("Close");
  box.append(title, intro, list, form, status, close);
  shade.append(box);

  const ownerMxid = channel.owner && channel.owner.mxid;
  function draw() {
    list.textContent = "";
    const helpers = Array.from(current.writers ? (current.writers.values ? current.writers.values() : current.writers) : [])
      .filter((w) => w.mxid !== ownerMxid);
    if (!helpers.length) {
      const none = document.createElement("p");
      none.className = "text-small text-muted mb-0";
      none.textContent = window.tr("No helpers yet.");
      list.append(none);
    }
    for (const helper of helpers) {
      const line = document.createElement("div");
      line.className = "d-flex align-items-center justify-content-between py-1";
      const name = document.createElement("span");
      name.className = "text-truncate";
      name.textContent = helper.shown_name || `@${helper.username}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-outline-danger btn-sm ml-2";
      remove.textContent = window.tr("Remove");
      remove.setAttribute("aria-label", window.tr("Remove @%(name)s as a helper", { name: helper.username }));
      remove.addEventListener("click", () => change(helper.username, false));
      line.append(name, remove);
      list.append(line);
    }
    form.hidden = helpers.length >= 3;
  }

  async function change(username, on) {
    status.textContent = "";
    status.classList.remove("text-danger");
    try {
      const updated = await setChannelHelper(boot, current.channel_id, username, on);
      current = { ...updated, writers: new Map((updated.writers || []).map((w) => [w.mxid, w])) };
      draw();
      onChanged(updated);
      input.value = "";
    } catch (e) {
      status.textContent = e.message;
      status.classList.add("text-danger");
    }
  }

  const finish = () => {
    shade.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => { if (e.key === "Escape") finish(); };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = input.value.trim().replace(/^@/, "");
    if (name) change(name, true);
  });
  close.addEventListener("click", finish);
  document.addEventListener("keydown", onKey);
  document.body.append(shade);
  draw();
  input.focus();
}
