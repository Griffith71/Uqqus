// Right to left (Arabic, Persian, Hebrew, Urdu: helpers/i18n.py RTL): loaded only when the page is <html dir="rtl">,
// right after Bootstrap's own script. The stylesheets are mirrored (helpers/css_flip.py), but Bootstrap 4's dropdowns
// and tooltips are placed by Popper with physical sides: a menu aligned to its button's "start" (left) must align to
// its right, a tooltip asked for "left" must open on the right. This swaps them, so every data-placement and
// .dropdown-menu-right on the site mirrors without a change to the markup.
(function () {
  "use strict";
  if (document.documentElement.getAttribute("dir") !== "rtl" || !window.jQuery || !jQuery.fn) return;

  function mirror(placement) {
    if (typeof placement !== "string") return placement;
    return placement
      .replace(/-start$/, "-\u0000").replace(/-end$/, "-start").replace(/-\u0000$/, "-end")
      .replace(/^left/, "\u0000").replace(/^right/, "left").replace(/^\u0000/, "right");
  }

  var Dropdown = jQuery.fn.dropdown && jQuery.fn.dropdown.Constructor;
  if (Dropdown && Dropdown.prototype._getPlacement) {
    var placement = Dropdown.prototype._getPlacement;
    Dropdown.prototype._getPlacement = function () { return mirror(placement.call(this)); };
  }

  var Tooltip = jQuery.fn.tooltip && jQuery.fn.tooltip.Constructor;
  if (Tooltip && Tooltip.prototype._getAttachment) {
    var attachment = Tooltip.prototype._getAttachment;
    // popovers inherit it
    Tooltip.prototype._getAttachment = function (where) { return mirror(attachment.call(this, where)); };
  }
})();
