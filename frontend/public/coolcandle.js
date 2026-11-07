/* CoolCandle — logo + loader helpers (no dependencies)
 *
 * Markup:
 *   <span data-cc-logo data-play="loop"></span>   logo; data-play = loop | once | (omit)
 *   <span data-cc-logo data-wordmark="false"></span>   icon only
 *   <div data-cc-loader data-text="Loading…"></div>   inline loader
 *
 * Full-page loader:
 *   CoolCandleLoader.show();              // appears after 300ms (no flash)
 *   CoolCandleLoader.hide();
 *   CoolCandleLoader.track(fetch(url));   // shows while the promise is pending
 */
(function () {
  var ARC = "M153.6 55A70 70 0 1 0 153.6 145";
  var GAP = "M153.6 145A70 70 0 0 0 153.6 55";
  var THICK = ' fill="none" stroke-width="22" stroke-linecap="round"';

  function logoSVG() {
    return [
      '<svg viewBox="0 0 200 200" role="img" aria-label="CoolCandle logo">',
      '<path d="' + ARC + '" stroke="var(--cc-track)"' + THICK + "/>",
      '<path class="cc-progress" pathLength="1" d="' + ARC + '"',
      ' stroke="var(--cc-ice)"' + THICK + "/>",
      '<path class="cc-gap" pathLength="1" d="' + GAP + '"',
      ' stroke="var(--cc-mint)"' + THICK + "/>",
      '<g class="cc-candle">',
      '<line x1="100" y1="62" x2="100" y2="138"',
      ' stroke="var(--cc-mint)" stroke-width="6" stroke-linecap="round"/>',
      '<rect x="86" y="78" width="28" height="44" rx="6" fill="var(--cc-mint)"/>',
      "</g>",
      '<g class="cc-check cc-fb">',
      '<circle cx="100" cy="100" r="30" fill="var(--cc-mint)"/>',
      '<path d="M86 100l9 9 18-20" fill="none" stroke="var(--cc-navy)"',
      ' stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>',
      "</g>",
      "</svg>",
    ].join("");
  }

  // Trader DNA: 8 columns, each a green candle (top) and red candle (bottom)
  // joined by a rung. Every column flips top/bottom (CSS .cc-helix); the
  // staggered negative delays make the row twist like a DNA helix.
  function loaderSVG() {
    var parts = ['<svg viewBox="16 44 168 112" aria-hidden="true">'];
    for (var i = 0; i < 8; i++) {
      var x = 30 + i * 20;
      var delay = (-i * 0.3).toFixed(1) + "s";
      parts.push(
        '<g class="cc-helix" style="animation-delay:' + delay + '">',
        '<line x1="' + x + '" y1="62" x2="' + x + '" y2="138"',
        ' stroke="var(--cc-rung)" stroke-width="2"/>',
        '<rect x="' + (x - 6) + '" y="54" width="12" height="18" rx="3"',
        ' fill="var(--cc-mint)"/>',
        '<rect x="' + (x - 6) + '" y="128" width="12" height="18" rx="3"',
        ' fill="var(--cc-coral)"/>',
        "</g>"
      );
    }
    parts.push("</svg>");
    return parts.join("");
  }

  function escapeHTML(s) {
    var map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return String(s).replace(/[&<>"']/g, function (c) {
      return map[c];
    });
  }

  function loaderHTML(text) {
    var label = text
      ? '<p class="cc-loader__text">' + escapeHTML(text) + "</p>"
      : '<span class="cc-sr">Loading</span>';
    return (
      '<div class="cc-loader" role="status" aria-live="polite">' +
      loaderSVG() +
      label +
      "</div>"
    );
  }

  function render(root) {
    root = root || document;
    var logos = root.querySelectorAll("[data-cc-logo]:not([data-cc-ready])");
    logos.forEach(function (el) {
      el.classList.add("cc-logo");
      var showWord = el.getAttribute("data-wordmark") !== "false";
      var word = showWord
        ? '<span class="cc-logo__word"><span>Cool</span>Candle</span>'
        : "";
      el.innerHTML = logoSVG() + word;
      el.setAttribute("data-cc-ready", "");
    });
    var loaders = root.querySelectorAll("[data-cc-loader]:not([data-cc-ready])");
    loaders.forEach(function (el) {
      el.innerHTML = loaderHTML(el.getAttribute("data-text"));
      el.setAttribute("data-cc-ready", "");
    });
  }

  // ---------- Full-page overlay ----------
  var overlay = null;
  var pending = 0;
  var timer = null;

  function ensureOverlay(text) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "cc-overlay";
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = loaderHTML(text || "Loading…");
    return overlay;
  }

  var CoolCandleLoader = {
    show: function (opts) {
      opts = opts || {};
      var delay = opts.delay == null ? 300 : opts.delay;
      pending++;
      if (pending > 1) return;
      ensureOverlay(opts.text);
      clearTimeout(timer);
      timer = setTimeout(function () {
        overlay.classList.add("is-visible");
      }, delay);
    },
    hide: function (force) {
      pending = force ? 0 : Math.max(0, pending - 1);
      if (pending > 0) return;
      clearTimeout(timer);
      if (overlay) overlay.classList.remove("is-visible");
    },
    track: function (promise, opts) {
      CoolCandleLoader.show(opts);
      return Promise.resolve(promise).finally(function () {
        CoolCandleLoader.hide();
      });
    },
    render: render,
    logoSVG: logoSVG,
    loaderSVG: loaderSVG,
  };

  // Rails Turbo: show the loader automatically on slow visits / form submits
  document.addEventListener("turbo:before-fetch-request", function () {
    CoolCandleLoader.show();
  });
  document.addEventListener("turbo:before-fetch-response", function () {
    CoolCandleLoader.hide();
  });
  document.addEventListener("turbo:fetch-request-error", function () {
    CoolCandleLoader.hide(true);
  });
  document.addEventListener("turbo:load", function () {
    CoolCandleLoader.hide(true);
    render();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      render();
    });
  } else {
    render();
  }

  window.CoolCandleLoader = CoolCandleLoader;
})();
