// Measures the fixed top bar and names its height --navbar-height on <html>, so what sits under it (the page's top
// padding, the sticky tab and pill bars, the left sidebar) follows it. The bar is as tall as its text, so it grows with
// Settings > Content > Layout > Text size: setTextSize (all_js.js) raises a resize event, which measures it again.
// Every page shell loads this (default.html and header.html, which the settings, Create post and guild settings pages
// include), because the stylesheets read the one variable; 59px is what it measures at the default text size and what
// the stylesheets start from, so nothing moves while the page loads.

(function () {
  var nav = document.getElementById('navbar');
  if (!nav) return;

  function updateNavHeight() {
    var h = nav.offsetHeight || 49;
    document.documentElement.style.setProperty('--navbar-height', h + 'px');
  }

  // set at once and checked again once the fonts and the layout have settled
  document.addEventListener('DOMContentLoaded', function () {
    requestAnimationFrame(updateNavHeight);
    setTimeout(updateNavHeight, 250);
  });
  window.addEventListener('resize', function () { requestAnimationFrame(updateNavHeight); });
  // anything that changes inside the bar (a badge, a name, the text size) can change its height
  if (window.MutationObserver) {
    new MutationObserver(function () { requestAnimationFrame(updateNavHeight); })
      .observe(nav, { childList: true, subtree: true, characterData: true, attributes: true });
  }
  updateNavHeight();
})();
