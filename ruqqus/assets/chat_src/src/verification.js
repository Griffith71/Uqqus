// Verifying a new chat app from this page (Settings > Chat apps): when an app the member signed in to asks "verify
// with another device", this page shows the seven emoji to compare with the app's. Once they match, this device signs
// the app's device with the member's own key, and the app can read their messages; until then nobody's messages are
// sent to it (OnlySignedDevicesIsolationMode). Only the member's own devices are answered, never another person's.
// Text only: nothing here writes HTML.
import { CryptoEvent, VerificationPhase, VerificationRequestEvent, VerifierEvent } from "matrix-js-sdk/lib/crypto-api/index.js";

let open = null;      // one verification at a time

function sheet() {
  const shade = document.createElement("div");
  shade.className = "chat-key-dialog";
  shade.setAttribute("role", "dialog");
  shade.setAttribute("aria-modal", "true");
  const box = document.createElement("div");
  box.className = "chat-key-box";
  shade.append(box);
  document.body.append(shade);
  return {
    show(title, lines, buttons, emoji) {
      const h = document.createElement("h2");
      h.className = "h5";
      h.id = `chat-verify-title-${Date.now()}`;
      h.textContent = title;
      shade.setAttribute("aria-labelledby", h.id);
      const parts = [h];
      for (const line of lines) {
        const p = document.createElement("p");
        p.className = "text-small";
        p.textContent = line;
        parts.push(p);
      }
      if (emoji) {
        const list = document.createElement("ol");
        list.className = "chat-sas list-unstyled";
        list.setAttribute("aria-label", "The emoji to compare");
        for (const [symbol, name] of emoji) {
          const item = document.createElement("li");
          const glyph = document.createElement("span");
          glyph.className = "chat-sas-emoji";
          glyph.setAttribute("aria-hidden", "true");
          glyph.textContent = symbol;
          const label = document.createElement("span");
          label.className = "chat-sas-name";
          label.textContent = name;
          item.append(glyph, label);
          list.append(item);
        }
        parts.push(list);
      }
      const row = document.createElement("div");
      row.className = "d-flex flex-wrap mt-2";
      row.style.gap = "0.5rem";
      for (const b of buttons) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = b.primary ? "btn btn-primary" : "btn btn-link text-muted";
        button.textContent = b.label;
        button.addEventListener("click", () => { button.disabled = true; b.run(); });
        row.append(button);
      }
      parts.push(row);
      box.replaceChildren(...parts);
      (row.querySelector(".btn-primary") || row.querySelector("button"))?.focus();
    },
    close() { shade.remove(); },
  };
}

function follow(request) {
  const view = sheet();
  open = view;
  let wired = false;
  const finish = () => {
    request.off(VerificationRequestEvent.Change, onChange);
    open = null;
  };
  const close = () => { view.close(); finish(); };
  const stop = () => { request.cancel().catch(() => {}); close(); };

  function onChange() {
    if (request.phase === VerificationPhase.Cancelled) {
      view.show("Verification stopped", ["The app wasn't verified. You can start again from the app, or use your security key instead."],
                [{ label: "Close", primary: true, run: close }]);
      finish();
      return;
    }
    if (request.phase === VerificationPhase.Done) {
      view.show("Your app is verified", ["It can now read your messages. You can sign it out in Settings > Chat apps."],
                [{ label: "Done", primary: true, run: close }]);
      finish();
      return;
    }
    const verifier = request.verifier;
    if (verifier && !wired) {
      wired = true;
      verifier.on(VerifierEvent.ShowSas, (sas) => {
        if (!sas.sas.emoji) { sas.cancel(); return; }
        view.show("Do these emoji match?", ["Compare them with the ones your app shows, in the same order."],
                  [{ label: "They match", primary: true, run: () => { sas.confirm().catch(() => {}); view.show("Waiting for the app", ["Confirm in the app as well."], [{ label: "Cancel", run: stop }]); } },
                   { label: "They don't match", run: () => { sas.mismatch(); close(); } }],
                  sas.sas.emoji);
      });
      verifier.verify().catch(() => {});
    }
  }
  request.on(VerificationRequestEvent.Change, onChange);

  const device = request.otherDeviceId ? ` (${request.otherDeviceId})` : "";
  view.show("Verify your new chat app?", [
    `A chat app signed in to your account${device} asks to be verified from this browser.`,
    "Only go on if you are signing in to an app right now. If not, choose Not now and sign it out in Settings > Chat apps.",
  ], [
    { label: "Verify", primary: true, run: () => {
      request.accept().catch(() => {});
      view.show("Waiting for the app", ["In the app, choose to compare emoji."], [{ label: "Cancel", run: stop }]);
    } },
    { label: "Not now", run: stop },
  ]);
}

export function answerOwnVerifications(client) {
  client.on(CryptoEvent.VerificationRequestReceived, (request) => {
    if (!request.isSelfVerification || request.phase !== VerificationPhase.Requested || open) return;
    follow(request);
  });
}
