/* Theme engine: palettes, accent derivation and the ripple switch.
   Split out of the interaction runtime so the surface can retheme
   without the rest of the app being involved. */
export function createTheme(root) {
  var THEMES = {
    light: {
      bg: "#F2EEE6", surface: "#FFFDF9", surface2: "#FAF6EF", surface3: "#F1EBE0",
      ink: "#17130F", ink2: "#3E362D", muted: "#7A6F62", faint: "#A79A89",
      line: "rgba(23,19,15,.10)", line2: "rgba(23,19,15,.055)", lineStrong: "rgba(23,19,15,.18)",
      glass: "rgba(255,253,249,.74)", glass2: "rgba(250,246,239,.62)",
      scroll: "rgba(23,19,15,.18)", accentInk: "#FFF8F4"
    },
    dark: {
      bg: "#0A0908", surface: "#161310", surface2: "#1D1915", surface3: "#251F19",
      ink: "#F4EDE2", ink2: "#D6CCBE", muted: "#9C9184", faint: "#6E6459",
      line: "rgba(244,237,226,.11)", line2: "rgba(244,237,226,.055)", lineStrong: "rgba(244,237,226,.2)",
      glass: "rgba(22,19,16,.74)", glass2: "rgba(29,25,21,.62)",
      scroll: "rgba(244,237,226,.16)", accentInk: "#1A0F09"
    }
  };

  function hexToRgb(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16)
    };
  }

  function applyAccent(hex, persist) {
    if (!hex) return;
    root.style.setProperty("--accent", hex);
    var c = hexToRgb(hex);
    root.style.setProperty("--accent-soft", "rgba(" + c.r + "," + c.g + "," + c.b + ",.14)");
    root.style.setProperty("--accent-soft-2", "rgba(" + c.r + "," + c.g + "," + c.b + ",.07)");
    document.querySelectorAll("[data-accent]").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-accent") === hex);
    });
    // The swatch is a preference like the palette, so it outlives the tab.
    if (persist) { try { window.localStorage.setItem("pebble-orbit-accent", hex); } catch (err) {} }
  }

  function setTheme(mode, origin, silent) {
    if (!THEMES[mode]) return;
    // Everything below flips variables and classes in one synchronous pass.
    // The veil is what the user sees; the DOM swap happens underneath it.
    var apply = function () {
    root.classList.add("theme-transition");
    root.setAttribute("data-theme", mode);
    var t = THEMES[mode];
    root.style.setProperty("--bg", t.bg);
    root.style.setProperty("--surface", t.surface);
    root.style.setProperty("--surface-2", t.surface2);
    root.style.setProperty("--surface-3", t.surface3);
    root.style.setProperty("--ink", t.ink);
    root.style.setProperty("--ink-2", t.ink2);
    root.style.setProperty("--muted", t.muted);
    root.style.setProperty("--faint", t.faint);
    root.style.setProperty("--line", t.line);
    root.style.setProperty("--line-2", t.line2);
    root.style.setProperty("--line-strong", t.lineStrong);
    root.style.setProperty("--glass", t.glass);
    root.style.setProperty("--glass-2", t.glass2);
    root.style.setProperty("--scroll", t.scroll);
    root.style.setProperty("--accent-ink", t.accentInk);

    document.querySelectorAll("#themePill button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-theme-set") === mode);
    });
    document.querySelectorAll(".theme-card").forEach(function (c) {
      c.classList.toggle("on", c.getAttribute("data-mode") === mode);
    });
    window.setTimeout(function () { root.classList.remove("theme-transition"); }, 40);
    if (!silent) {
      try { window.localStorage.setItem("pebble-orbit-theme", mode); } catch (err) {}
      try { if (window.__pebbleThemeSave) window.__pebbleThemeSave(mode); } catch (err) {}
    }
    };

    // A restore must not paint the ripple: it runs before the first frame.
    if (silent) { apply(); return; }
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { apply(); return; }
    var w = root.clientWidth || window.innerWidth;
    var h = root.clientHeight || window.innerHeight;
    var hasPoint = origin && typeof origin.x === "number" && typeof origin.y === "number";
    var x = hasPoint ? origin.x : w - 138;
    var y = hasPoint ? origin.y : 34;
    var radius = Math.ceil(Math.hypot(Math.max(x, w - x), Math.max(y, h - y)));
    var veil = document.createElement("div");
    veil.className = "theme-ripple";
    veil.style.background = THEMES[mode].bg;
    veil.style.clipPath = "circle(0px at " + x + "px " + y + "px)";
    document.body.appendChild(veil);
    window.requestAnimationFrame(function () {
      veil.style.transition = "clip-path 460ms cubic-bezier(.22,.61,.36,1), opacity 180ms ease 430ms";
      veil.style.clipPath = "circle(" + radius + "px at " + x + "px " + y + "px)";
      veil.style.opacity = "0";
    });
    window.setTimeout(apply, 70);
    window.setTimeout(function () { veil.remove(); }, 720);
  }

  document.querySelectorAll("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function (e) { setTheme(b.getAttribute("data-theme-set"), { x: e.clientX, y: e.clientY }); });
  });
  document.querySelectorAll("[data-set-theme]").forEach(function (b) {
    b.addEventListener("click", function (e) { setTheme(b.getAttribute("data-set-theme"), { x: e.clientX, y: e.clientY }); });
  });
  document.querySelectorAll("[data-accent]").forEach(function (b) {
    b.addEventListener("click", function () { applyAccent(b.getAttribute("data-accent"), true); });
  });

  /* Restore both preferences before returning to the caller: the palette and the
     swatch are user choices, and a reload should not bounce back to the default
     daylight orange. */
  var savedMode = "";
  var savedAccent = "";
  try {
    savedMode = window.localStorage.getItem("pebble-orbit-theme") || "";
    savedAccent = window.localStorage.getItem("pebble-orbit-accent") || "";
  } catch (err) {}
  if (THEMES[savedMode]) setTheme(savedMode, null, true);
  if (savedAccent) applyAccent(savedAccent, false);

  return { THEMES: THEMES, setTheme: setTheme, applyAccent: applyAccent };
}
