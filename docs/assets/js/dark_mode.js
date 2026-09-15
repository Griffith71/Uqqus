function switch_css() {
  css = document.getElementById("css-link");
  dswitch = document.getElementById("dark-switch");

  if (css.href.endsWith("/assets/style/main.css")) {
    post("/settings/dark_mode/1", callback=function() {
      css.href="/assets/style/main_dark.css";
      dswitch.classList.remove("fa-toggle-off");
      dswitch.classList.add("fa-toggle-on");
      // Update text label from "Dark mode" to "Light mode"
      var links = document.querySelectorAll('a[onclick="switch_css()"]');
      links.forEach(function(link) {
        var textNode = Array.from(link.childNodes).find(node => node.nodeType === 3);
        if(textNode) textNode.textContent = 'Light mode';
      });
    });
  }
  else {
    post("/settings/dark_mode/0", callback=function() {
      css.href="/assets/style/main.css";
      dswitch.classList.remove("fa-toggle-on");
      dswitch.classList.add("fa-toggle-off");
      // Update text label from "Light mode" to "Dark mode"
      var links = document.querySelectorAll('a[onclick="switch_css()"]');
      links.forEach(function(link) {
        var textNode = Array.from(link.childNodes).find(node => node.nodeType === 3);
        if(textNode) textNode.textContent = 'Dark mode';
      });
    });
  }
}