// The one-time backup codes box on Settings > Security (templates/settings_security.html): Copy, Download and Print.
// The codes are text in #backup-codes-list; nothing is sent anywhere.
(function () {
  var list = document.getElementById("backup-codes-list");
  if (!list) return;

  function text() {
    var codes = Array.prototype.map.call(list.querySelectorAll("li"), function (li) { return li.textContent.trim(); });
    return codes.join("\n") + "\n";
  }

  var box = document.getElementById("backup-codes-new");
  var say = box && box.querySelector("[data-backup-said]");

  function said(message) {
    if (say) say.textContent = message;
  }

  var copy = document.querySelector("[data-backup-copy]");
  if (copy) copy.addEventListener("click", function () {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text()).then(function () { said("Copied."); }, function () { said("Couldn't copy: select the codes and copy them."); });
    } else {
      said("Couldn't copy: select the codes and copy them.");
    }
  });

  var download = document.querySelector("[data-backup-download]");
  if (download) download.addEventListener("click", function () {
    var link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text()], { type: "text/plain" }));
    link.download = "backup-codes.txt";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    said("Downloaded.");
  });

  var print = document.querySelector("[data-backup-print]");
  if (print) print.addEventListener("click", function () { window.print(); });
})();
