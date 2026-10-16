// The two group sheets on the chat page (templates/partials/chat_group_sheets.html): "New group" and a group's own
// sheet. The server decides everything (routes/chat_groups.py); these only offer what it would allow and show its
// answer. Every name goes in through textContent.

import {
  searchPeople, createGroup, addToGroup, removeFromGroup, setGroupRole, renameGroup, leaveGroup,
} from "./api.js";

const byId = (id) => document.getElementById(id);

function sheet(id, action) {
  if (window.jQuery) window.jQuery(`#${id}`).modal(action);
}

function say(el, text, bad) {
  el.textContent = text || "";
  el.classList.toggle("text-danger", !!bad);
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function avatar(url) {
  const img = document.createElement("img");
  img.className = "rounded-circle mr-2 flex-shrink-0";
  img.width = img.height = 32;
  img.alt = "";
  if (typeof url === "string" && /^(https?:\/\/|\/)/.test(url)) img.src = url;
  return img;
}

// a person as a row: avatar, display name with the @handle, a few words after it, and buttons
function personRow(person, { note, buttons } = {}) {
  const row = document.createElement("div");
  row.className = "chat-person d-flex align-items-center py-1";
  row.appendChild(avatar(person.profile_url));
  const name = document.createElement("div");
  name.className = "flex-grow-1 text-truncate";
  const strong = document.createElement("span");
  strong.className = "font-weight-bold";
  strong.textContent = person.shown_name || person.display_name || (person.username ? `@${person.username}` : "");
  name.appendChild(strong);
  if (person.username && (person.display_name || (person.shown_name && person.shown_name !== person.username))) {
    const handle = document.createElement("span");
    handle.className = "text-muted ml-1";
    handle.textContent = `@${person.username}`;
    name.appendChild(handle);
  }
  if (note) {
    const small = document.createElement("span");
    small.className = "badge badge-light ml-2";
    small.textContent = note;
    name.appendChild(small);
  }
  row.appendChild(name);
  for (const b of buttons || []) row.appendChild(b);
  return row;
}

function button(label, className, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `btn btn-sm ml-1 ${className}`;
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

const ROLE_NOTES = { owner: "Owner", admin: "Admin" };

export function initGroups({ boot, onCreated, onChanged, onLeft }) {
  const max = Number(boot.maxGroupMembers) || 32;

  // --- New group ---------------------------------------------------------------------------------------------
  const chosen = new Map();             // username -> person
  const nameInput = byId("chat-group-name");
  const searchInput = byId("chat-group-search");
  const results = byId("chat-group-results");
  const chips = byId("chat-group-chosen");
  const createButton = byId("chat-group-create");
  const newStatus = byId("chat-group-new-status");
  let token = 0;

  function drawChosen() {
    chips.textContent = "";
    for (const person of chosen.values()) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "btn btn-sm btn-outline-secondary chat-chip mr-1 mb-1";
      chip.setAttribute("aria-label", `Remove ${person.username}`);
      chip.textContent = `@${person.username} `;
      const x = document.createElement("i");
      x.className = "fas fa-xmark";
      x.setAttribute("aria-hidden", "true");
      chip.appendChild(x);
      chip.addEventListener("click", () => {
        chosen.delete(person.username);
        drawChosen();
        load(searchInput.value);
      });
      chips.appendChild(chip);
    }
    createButton.disabled = chosen.size === 0;
  }

  function drawResults(people) {
    results.textContent = "";
    if (!people.length) {
      const empty = document.createElement("p");
      empty.className = "text-small text-muted mb-0";
      empty.textContent = searchInput.value.trim() ? "Nobody matches." : "Follow someone to add them to a group.";
      results.appendChild(empty);
      return;
    }
    for (const person of people) {
      const picked = chosen.has(person.username);
      const toggle = button(picked ? "Added" : "Add", picked ? "btn-secondary" : "btn-primary", () => {
        if (chosen.has(person.username)) {
          chosen.delete(person.username);
        } else if (chosen.size + 1 >= max) {
          say(newStatus, `A group can have at most ${max} people.`, true);
          return;
        } else {
          chosen.set(person.username, person);
        }
        say(newStatus, "");
        drawChosen();
        drawResults(people);
      });
      toggle.setAttribute("aria-pressed", String(picked));
      results.appendChild(personRow(person, { buttons: [toggle] }));
    }
  }

  function load(q) {
    const mine = ++token;
    searchPeople(q.trim())
      .then((people) => { if (mine === token) drawResults(people); })
      .catch(() => { if (mine === token) say(newStatus, "Could not search. Try again.", true); });
  }

  searchInput.addEventListener("input", debounce(() => load(searchInput.value), 250));

  byId("chat-new-group").addEventListener("click", () => {
    chosen.clear();
    nameInput.value = "";
    searchInput.value = "";
    say(newStatus, "");
    drawChosen();
    load("");
    sheet("chatGroupNew", "show");
  });

  createButton.addEventListener("click", async () => {
    createButton.disabled = true;
    say(newStatus, "Creating…");
    try {
      const made = await createGroup(boot.formkey, nameInput.value.trim(), [...chosen.keys()]);
      sheet("chatGroupNew", "hide");
      onCreated(made);
    } catch (e) {
      say(newStatus, e.message, true);
      createButton.disabled = chosen.size === 0;
    }
  });

  // --- a group's own sheet -------------------------------------------------------------------------------------
  let current = null;
  const infoTitle = byId("chatGroupInfoTitle");
  const members = byId("chat-group-members");
  const count = byId("chat-group-count");
  const renameForm = byId("chat-group-rename");
  const renameInput = byId("chat-group-rename-input");
  const addBox = byId("chat-group-add");
  const addSearch = byId("chat-group-add-search");
  const addResults = byId("chat-group-add-results");
  const infoStatus = byId("chat-group-info-status");
  let addToken = 0;

  function changed(updated) {
    current = updated;
    draw();
    onChanged(updated);
  }

  async function act(promise, done) {
    say(infoStatus, "");
    try {
      const updated = await promise;
      if (updated) changed(updated);
      if (done) done();
    } catch (e) {
      say(infoStatus, e.message, true);
    }
  }

  function draw() {
    const group = current;
    const me = group.members.find((m) => m.id === boot.userId);
    const myRole = me ? me.role : null;
    infoTitle.textContent = group.label;
    count.textContent = `${group.size} ${group.size === 1 ? "person" : "people"}`;
    renameForm.classList.toggle("d-none", !group.can_manage);
    if (document.activeElement !== renameInput) renameInput.value = group.name || "";
    addBox.classList.toggle("d-none", !group.can_manage || group.size >= max);

    members.textContent = "";
    for (const person of group.members) {
      const isMe = person.id === boot.userId;
      const notes = [];
      if (ROLE_NOTES[person.role]) notes.push(ROLE_NOTES[person.role]);
      if (!person.joined) notes.push("Invited");
      if (isMe) notes.push("You");
      const buttons = [];
      if (!isMe && person.username) {
        // what the server would allow (helpers/chat_groups.py); it checks again
        if (myRole === "owner" && person.role !== "owner" && person.joined) {
          const admin = person.role === "admin";
          buttons.push(button(admin ? "Remove admin" : "Make admin", "btn-outline-primary", () =>
            act(setGroupRole(boot.formkey, group.group_id, person.username, admin ? "member" : "admin"))));
        }
        if (myRole === "owner" ? person.role !== "owner" : myRole === "admin" && person.role === "member") {
          buttons.push(button("Remove", "btn-outline-danger", () => {
            if (!window.confirm(`Remove @${person.username} from the group?`)) return;
            act(removeFromGroup(boot.formkey, group.group_id, person.username));
          }));
        }
      }
      members.appendChild(personRow(person, { note: notes.join(" · "), buttons }));
    }
  }

  function drawAddResults(people) {
    addResults.textContent = "";
    const inGroup = new Set(current.members.map((m) => m.username));
    const shown = people.filter((p) => !inGroup.has(p.username));
    if (!shown.length) {
      const empty = document.createElement("p");
      empty.className = "text-small text-muted mb-0";
      empty.textContent = addSearch.value.trim() ? "Nobody else matches." : "Search for someone to add.";
      addResults.appendChild(empty);
      return;
    }
    for (const person of shown) {
      addResults.appendChild(personRow(person, {
        buttons: [button("Add", "btn-primary", () =>
          act(addToGroup(boot.formkey, current.group_id, [person.username]), () => {
            addSearch.value = "";
            addResults.textContent = "";
          }))],
      }));
    }
  }

  addSearch.addEventListener("input", debounce(() => {
    const mine = ++addToken;
    const q = addSearch.value.trim();
    if (!q) {
      addResults.textContent = "";
      return;
    }
    searchPeople(q).then((people) => { if (mine === addToken) drawAddResults(people); }).catch(() => {});
  }, 250));

  renameForm.addEventListener("submit", () => act(renameGroup(boot.formkey, current.group_id, renameInput.value.trim())));

  byId("chat-group-leave").addEventListener("click", async () => {
    const group = current;
    const owner = group.my_role === "owner";
    const message = owner
      ? "Leave this group? Someone else in it becomes its owner."
      : "Leave this group? You won't see new messages unless someone adds you again.";
    if (!window.confirm(message)) return;
    try {
      await leaveGroup(boot.formkey, group.group_id);
      sheet("chatGroupInfo", "hide");
      onLeft(group);
    } catch (e) {
      say(infoStatus, e.message, true);
    }
  });

  return {
    openInfo(group) {
      current = group;
      say(infoStatus, "");
      addSearch.value = "";
      addResults.textContent = "";
      draw();
      sheet("chatGroupInfo", "show");
    },
    refresh(group) {
      if (current && group && current.group_id === group.group_id) {
        current = group;
        draw();
      }
    },
  };
}
