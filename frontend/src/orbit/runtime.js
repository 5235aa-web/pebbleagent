/* Interaction runtime, extracted verbatim from design-preview/pebble-orbit.html.
   The prototype body is mounted as static DOM before this runs, so the
   original imperative code operates on the same nodes it always did. */
import { createTheme } from "./theme.js";
import { selectSession, plan, permissions, settlePermission, dropPermission, pendingPermissions, sessionState } from "../data/store";
import { decidePermission, loadSession, sendPrompt, interruptSession, renameSession, deleteSession, toggleSessionPin, syncPermissionMode, setPermissionMode, createSession, exportSession, exportSessionMarkdown } from "../data/wire";
import { askConfirm } from "../data/confirm";
import { previewState, reloadPreview, capturePreview } from "../data/preview";
import { gateway } from "../data/gateway";
import { wireAttachments, clearFiles } from "../data/fileDrop";

export function initOrbit() {

(function () {
  "use strict";

  var root = document.documentElement;

  var themeEngine = createTheme(root);
  var THEMES = themeEngine.THEMES;
  var setTheme = themeEngine.setTheme;
  var applyAccent = themeEngine.applyAccent;

  // Cross-device theme sync: the Orbit pick travels through the gateway theme
  // record, so another machine opens on the same surface.
  window.__pebbleThemeApply = function (mode, accent) {
    if (mode === "light" || mode === "dark") themeEngine.setTheme(mode);
    if (accent) themeEngine.applyAccent(accent);
  };

  document.querySelectorAll("#swatches .swatch").forEach(function (s) {
    s.addEventListener("click", function () {
      document.querySelectorAll("#swatches .swatch").forEach(function (x) { x.classList.remove("on"); });
      s.classList.add("on");
      applyAccent(s.getAttribute("data-accent"));
      toast("Accent applied", "Charts, rings and badges follow the new token.", "ok");
    });
  });

  /* ---------------- toasts ---------------- */
  var toastHost = document.getElementById("toasts");

  function toast(title, msg, kind) {
    if (!toastHost) return;    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    var icon = kind === "err"
      ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      : kind === "warn"
        ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
        : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
    el.innerHTML =
      '<span class="toast-ico">' + icon + '</span>' +
      '<span class="toast-main"><span class="toast-title"></span>' +
      '<span class="toast-msg"></span></span>' +
      '<button class="toast-x"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>' +
      '<span class="toast-bar"></span>';
    /* Text, not markup: a reason from the gateway is data, and this is the one
       place that renders it without escaping. */
    el.querySelector(".toast-title").textContent = title;
    el.querySelector(".toast-msg").textContent = msg || "";
    toastHost.appendChild(el);
    var timer = window.setTimeout(function () { dismiss(); }, 4600);
    function dismiss() {
      window.clearTimeout(timer);
      el.classList.add("out");
      window.setTimeout(function () { el.remove(); }, 320);
    }
    el.querySelector(".toast-x").addEventListener("click", dismiss);
  }

  // Vue-rendered rows share this host instead of growing a second toast stack.
  window.__pebbleToast = toast;

  /* The command palette is a Vue component; the panels and toggles the shell
     owns are reached through this one object, so the component never touches
     this closure and its rows can come from real state instead of markup. */
  window.__pebbleNav = {
    openSheet: openOverlay,
    sessions: function () { setSessOpen(true); },
    stage: function () { setStageOpen(true); },
    tab: function (name) { ensureRightOpen(); selectTab(name); },
    plan: function () {
      var stream = document.querySelector(".stream");
      setPlanOpen(!(stream && stream.classList.contains("plan-open")));
    },
    newSession: newSession
  };

  document.querySelectorAll("[data-toast]").forEach(function (b) {
    b.addEventListener("click", function () { toast("Done", b.getAttribute("data-toast"), "ok"); });
  });

  /* ---------------- overlays ---------------- */
  function openOverlay(id) {
    var el = document.getElementById(id);
    if (!el) return;
    // The message search popover owns its own open path (focus + render),
    // so route it through the trigger button instead of just adding .open.
    if (id === "msPop") {
      var trigger = document.getElementById("msgSearchBtn");
      // Defer past the current click so the popover's outside-click guard
      // does not close it again in the same tick.
      if (trigger) { window.setTimeout(function () { trigger.click(); }, 0); return; }
    }
    el.classList.add("open");
  }

  function closeOverlay(el) {
    if (!el) return;
    // The permission gate is bound to the store, so closing it is a state write.
    if (el.id === "permDialog") { permissions.open = false; return; }
    el.classList.remove("open");
  }

  document.querySelectorAll("[data-sheet]").forEach(function (b) {
    b.addEventListener("click", function () {
      openOverlay(b.getAttribute("data-sheet"));
      el_rail_reset();
    });
  });

  function el_rail_reset() { /* placeholder to keep click handlers chainable */ }

  document.querySelectorAll("[data-close]").forEach(function (b) {
    b.addEventListener("click", function () {
      closeOverlay(b.closest(".overlay"));
    });
  });

  document.querySelectorAll(".sheet-x").forEach(function (b) {
    b.addEventListener("click", function () { closeOverlay(b.closest(".overlay")); });
  });

  document.querySelectorAll(".overlay").forEach(function (ov) {
    ov.addEventListener("click", function (e) {
      if (e.target !== ov) return;
      if (ov.id === "permDialog") return;
      closeOverlay(ov);
    });
  });

  /* ---------------- command palette ---------------- */
  /* ---------------- command palette ---------------- */
  var cmdOverlay = document.getElementById("cmdOverlay");
  var cmdInput = document.getElementById("cmdInput");

  /* The list is a Vue component (CommandPalette.vue); this file only owns the
     overlay element and the open path every trigger binds to. Query text and
     every row live in Vue state, so nothing here filters or dispatches — the
     component used to be static markup driven by these handlers, which is why
     half its rows were placeholders. */
  function openCmd() {
    if (!cmdOverlay) return;
    cmdOverlay.classList.add("open");
    /* The component mounts before this file runs (main.ts mounts Vue first), so
       it cannot have registered a callback here yet. The open signal is a DOM
       event, which is live whenever the palette is actually shown. */
    window.dispatchEvent(new CustomEvent("pebble:cmd-open"));
    window.setTimeout(function () { if (cmdInput) cmdInput.focus(); }, 60);
  }
  function closeCmd() { closeOverlay(cmdOverlay); }

  document.querySelectorAll("[data-cmd]").forEach(function (b) {
    b.addEventListener("click", openCmd);
  });

  /* ---------------- tabs ---------------- */
  function selectTab(name) {
    document.querySelectorAll(".dr-tab").forEach(function (t) {
      t.classList.toggle("on", t.getAttribute("data-tab") === name);
    });
    document.querySelectorAll(".dr-pane").forEach(function (p) {
      p.classList.toggle("on", p.getAttribute("data-pane") === name);
    });
  }

  document.querySelectorAll(".dr-tab").forEach(function (t) {
    t.addEventListener("click", function () { selectTab(t.getAttribute("data-tab")); });
  });

  /* ---------------- session switcher ---------------- */
  var sessSwitch = document.getElementById("sessSwitch");
  var ssBtn = document.getElementById("ssBtn");
  var ssPop = document.getElementById("ssPop");
  var ssTitle = document.getElementById("ssTitle");
  var ssCount = document.querySelector(".ss-count");
  var convSearch = document.getElementById("convSearch");
  var convList = document.getElementById("convList");

  function setSessOpen(open) {
    if (!ssBtn || !ssPop) return;
    ssPop.classList.toggle("open", open);
    ssBtn.classList.toggle("open", open);
    ssBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open && convSearch) window.setTimeout(function () { convSearch.focus(); }, 90);
  }
  if (ssBtn) ssBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    setSessOpen(!ssPop.classList.contains("open"));
  });
  document.addEventListener("click", function (e) {
    if (sessSwitch && ssPop && ssPop.classList.contains("open") && !sessSwitch.contains(e.target)) {
      setSessOpen(false);
    }
  });
  if (ssPop) ssPop.addEventListener("click", function (e) { e.stopPropagation(); });

  if (convList) {
    convList.addEventListener("click", function (e) {
      /* The menu button has its own listener below. This handler only has to
         leave the row alone — it used to toast a fake menu on the way. */
      if (e.target.closest(".conv-more")) return;
      var row = e.target.closest(".conv");
      if (!row) return;
      // Vue owns the row classes and the header label; this only moves state.
      var picked = selectSession(row.getAttribute("data-id"));
      setSessOpen(false);
      if (picked) {
        loadSession(picked.id);
        toast("Session switched", picked.title, "ok");
      }
    });
    convList.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (e.target.classList.contains("conv")) e.target.click();
      }
    });
  }

  /* The filter lives in Vue state, not in inline `display` on the rows. Every
     list update re-renders those rows, and a DOM-level filter was silently
     dropped while the search box still showed the term. */
  if (convSearch) {
    convSearch.addEventListener("input", function () {
      sessionState.filter = convSearch.value;
    });
    convSearch.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        convSearch.value = "";
        sessionState.filter = "";
      }
    });
  }

  /* ---------------- collapse ---------------- */
  var workspace = document.getElementById("workspace");
  function ensureLeftOpen() { setSessOpen(true); }
  function ensureRightOpen() {
    if (workspace) workspace.classList.remove("right-collapsed");
  }

  var cr = document.getElementById("collapseRight");
  if (cr) cr.addEventListener("click", function () {
    workspace.classList.toggle("right-collapsed");
  });
  var tr = document.getElementById("toggleRight");
  if (tr) tr.addEventListener("click", function () {
    workspace.classList.toggle("right-collapsed");
  });

  /* ---------------- live plan slip ---------------- */
  var streamEl = document.querySelector(".stream");
  var planCard = document.getElementById("planCard");

  /* The card's own close button is wired in PlanCard.vue now. This function
     only serves the command palette's "Toggle task list", and it drives the
     class the CSS reads instead of a store plain JS cannot reach. */
  function setPlanOpen(open) {
    if (!streamEl || !planCard) return;
    streamEl.classList.toggle("plan-open", open);
    if (!open) {
      planCard.style.setProperty("--rx", "0deg");
      planCard.style.setProperty("--ry", "0deg");
    }
  }

  if (planCard) {
    planCard.addEventListener("mousemove", function (e) {
      var r = planCard.getBoundingClientRect();
      var nx = (e.clientX - r.left) / r.width - .5;
      var ny = (e.clientY - r.top) / r.height - .5;
      planCard.style.setProperty("--ry", (nx * 9).toFixed(2) + "deg");
      planCard.style.setProperty("--rx", (-ny * 7).toFixed(2) + "deg");
    });
    planCard.addEventListener("mouseleave", function () {
      planCard.style.setProperty("--rx", "0deg");
      planCard.style.setProperty("--ry", "0deg");
    });
  }

  /* ---------------- preview stage ---------------- */
  var stage = document.getElementById("previewStage");
  var stagePaper = document.getElementById("stagePaper");
  var stageZoom = document.getElementById("stageZoom");
  var stageSize = document.getElementById("stageSize");
  var openStageBtn = document.getElementById("openStage");

  function setStageOpen(open) {
    if (!stage) return;
    stage.classList.toggle("open", open);
    stage.setAttribute("aria-hidden", open ? "false" : "true");
  }
  document.querySelectorAll("[data-stage-close]").forEach(function (b) {
    b.addEventListener("click", function () { setStageOpen(false); });
  });
  if (openStageBtn) openStageBtn.addEventListener("click", function () { setStageOpen(true); });

  /* drag the floating window by its bar */
  var stageWindow = document.getElementById("stageWindow");
  var stageDrag = document.getElementById("stageDrag");
  var stageFrame = document.getElementById("stageFrame");
  var stageCode = document.getElementById("stageCode");
  var stageFull = document.getElementById("stageFull");
  var stageReload = document.getElementById("stageReload");

  if (stageWindow && stageDrag) {
    var dragState = null;
    stageDrag.addEventListener("pointerdown", function (e) {
      if (e.target.closest("button") || stageWindow.classList.contains("full")) return;
      var r = stageWindow.getBoundingClientRect();
      dragState = { px: e.clientX, py: e.clientY, x: r.left, y: r.top };
      stageWindow.classList.add("dragging");
      try { stageDrag.setPointerCapture(e.pointerId); } catch (err) {}
    });
    stageDrag.addEventListener("pointermove", function (e) {
      if (!dragState) return;
      var w = stageWindow.offsetWidth, h = stageWindow.offsetHeight;
      var nx = Math.min(Math.max(6, dragState.x + e.clientX - dragState.px), Math.max(6, innerWidth - w - 6));
      var ny = Math.min(Math.max(6, dragState.y + e.clientY - dragState.py), Math.max(6, innerHeight - h - 6));
      stageWindow.style.setProperty("--stage-x", nx + "px");
      stageWindow.style.setProperty("--stage-y", ny + "px");
    });
    var endDrag = function () { dragState = null; stageWindow.classList.remove("dragging"); };
    stageDrag.addEventListener("pointerup", endDrag);
    stageDrag.addEventListener("pointercancel", endDrag);
  }

  document.querySelectorAll("#stageView button").forEach(function (b) {
    b.addEventListener("click", function () {
      var view = b.getAttribute("data-view");
      document.querySelectorAll("#stageView button").forEach(function (x) { x.classList.toggle("on", x === b); });
      if (stage) stage.classList.toggle("show-code", view === "code");
    });
  });

  var stagePrev = null;
  if (stageFull && stageWindow) {
    stageFull.addEventListener("click", function () {
      var on = stageWindow.classList.toggle("full");
      if (on) {
        stagePrev = {
          x: stageWindow.style.getPropertyValue("--stage-x"),
          y: stageWindow.style.getPropertyValue("--stage-y")
        };
        stageWindow.style.setProperty("--stage-x", "14px");
        stageWindow.style.setProperty("--stage-y", "14px");
        stageWindow.style.setProperty("--stage-w", "calc(100vw - 28px)");
        stageWindow.style.setProperty("--stage-h", "calc(100vh - 28px)");
      } else if (stagePrev) {
        stageWindow.style.setProperty("--stage-x", stagePrev.x || "");
        stageWindow.style.setProperty("--stage-y", stagePrev.y || "");
        stageWindow.style.setProperty("--stage-w", "");
        stageWindow.style.setProperty("--stage-h", "");
      }
    });
  }
  if (stageReload) {
    stageReload.addEventListener("click", function () {
      reloadPreview();
      toast("Preview reloaded", "Re-read from the file on disk.", "ok");
    });
  }

  var stageCopy = document.getElementById("stageCopy");
  if (stageCopy && stageFrame) {
    stageCopy.addEventListener("click", function () {
      var html = stageFrame.getAttribute("srcdoc") || "";
      var done = function () { toast("HTML copied", html.length.toLocaleString() + " characters on the clipboard.", "ok"); };
      var fail = function () { toast("Copy blocked", "Clipboard needs a secure context.", "warn"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(html).then(done, fail);
      } else if (window.clipboardData) {
        window.clipboardData.setData("Text", html);
        done();
      } else {
        fail();
      }
    });
  }

  var stagePng = document.getElementById("stagePng");
  if (stagePng) {
    stagePng.addEventListener("click", function () {
      capturePreview().then(function (url) {
        var name = (previewState.activePath.split("/").pop() || "preview").replace(/\.[^.]+$/, "");
        var internals = window.__TAURI_INTERNALS__;
        if (internals && internals.invoke) {
          // Desktop: the snapshot belongs in the project next to the page, not
          // in the downloads folder, so it goes back through the gateway.
          gateway.request("file.write_base64", { path: name + ".png", data_base64: url.split(",")[1] || "" })
            .then(function (r) {
              toast(r && r.ok ? "PNG saved" : "Cannot save", (r && (r.path || r.error)) || name + ".png", r && r.ok ? "ok" : "warn");
            })
            .catch(function (err) { toast("Cannot save", String((err && err.message) || err), "warn"); });
          return;
        }
        var a = document.createElement("a");
        a.download = name + ".png";
        a.href = url;
        a.click();
        toast("PNG saved", name + ".png", "ok");
      }).catch(function (err) {
        toast("Cannot capture", String((err && err.message) || err), "warn");
      });
    });
  }


  var DEV_SIZES = { desktop: [1440, 900, 100], tablet: [768, 1024, 82], phone: [390, 844, 64] };
  // Device presets live in PreviewStage.vue: it owns the class the CSS keys on.



  // The stage opens on demand: auto-revealing a demo artifact would cover the
  // real preview the transcript drives.


  /* ---------------- compact summary (delegated above) ---------------- */

  /* ---------------- island ---------------- */
  var island = document.getElementById("island");
  if (island) {
    island.addEventListener("click", function (e) {
      if (e.target.closest(".island-expand")) return;
      island.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (!island.contains(e.target)) island.classList.remove("open");
    });
  }

  /* ---------------- stream interactions (delegated) ----------------
     The message list is rendered from data, so any row can be replaced on the
     next patch. Handlers therefore live on document, not on the nodes. */
  (function () {
    function toggleSummary(hdr) {
      var card = hdr.closest(".summary");
      if (!card) return;
      var closed = card.classList.toggle("closed");
      hdr.setAttribute("aria-expanded", closed ? "false" : "true");
    }

    function copyCode(btn) {
      var box = btn.closest(".code");
      var pre = box && box.querySelector(".code-pre");
      var text = pre ? pre.innerText : "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(function () {});
      }
      var old = btn.innerHTML;
      btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> copied';
      window.setTimeout(function () { btn.innerHTML = old; }, 1400);
    }

    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;

      var hdr = t.closest(".summary-hdr");
      if (hdr) { toggleSummary(hdr); return; }

      var copy = t.closest("[data-copy]");
      if (copy) { copyCode(copy); return; }

      var opt = t.closest(".q-opt");
      if (opt) {
        var card = opt.closest(".qcard");
        if (!card || card.classList.contains("done")) return;
        // Cards rendered from the store own their selection state.
        if (card.hasAttribute("data-live")) return;
        card.querySelectorAll(".q-opt").forEach(function (x) { x.classList.remove("sel"); });
        opt.classList.add("sel");
        return;
      }

      var go = t.closest(".q-go");
      if (go) {
        var sent = go.closest(".qcard");
        if (!sent || sent.classList.contains("done")) return;
        if (sent.hasAttribute("data-live")) return;
        sent.classList.add("done");
        toast("Answer sent", "Pebble resumed with your decision.", "ok");
        return;
      }

      var skip = t.closest(".q-skip");
      if (skip) {
        var skipped = skip.closest(".qcard");
        if (!skipped || skipped.classList.contains("done")) return;
        if (skipped.hasAttribute("data-live")) return;
        var stamp = skipped.querySelector(".q-stamp");
        if (stamp) stamp.textContent = "Skipped";
        skipped.classList.add("done");
        toast("Question skipped", "Pebble will choose on its own.", "warn");
        return;
      }

      var th = t.closest(".tool-hdr");
      if (th) {
        // Cards rendered from the store own their expanded state; only the
        // seeded prototype markup still toggles a class here.
        if (!th.closest("[data-live]") && th.parentElement) th.parentElement.classList.toggle("open");
        return;
      }

      var cd = t.closest(".cdiv");
      if (cd) {
        // Rows rendered from the store own their folded state; only the seeded
        // prototype markup still flips the class here.
        if (!cd.hasAttribute("data-live")) cd.classList.toggle("collapsed");
        return;
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var t = e.target;
      if (!t || !t.closest) return;
      var hdr = t.closest(".summary-hdr");
      if (!hdr) return;
      e.preventDefault();
      toggleSummary(hdr);
    });
  })();

  /* ---------------- segmented controls ---------------- */
  document.querySelectorAll(".seg").forEach(function (seg) {
    seg.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
      });
    });
  });

  document.querySelectorAll(".ledger-seg").forEach(function (seg) {
    seg.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
      });
    });
  });

  document.querySelectorAll(".prov-tab").forEach(function (b) {
    b.addEventListener("click", function () {
      b.parentElement.querySelectorAll(".prov-tab").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
    });
  });

  document.querySelectorAll(".model-row").forEach(function (r) {
    r.addEventListener("click", function () {
      r.parentElement.querySelectorAll(".model-row").forEach(function (x) { x.classList.remove("on"); });
      r.classList.add("on");
    });
  });

  document.querySelectorAll(".agent-card").forEach(function (c) {
    c.addEventListener("click", function () {
      c.parentElement.querySelectorAll(".agent-card").forEach(function (x) { x.classList.remove("on"); });
      c.classList.add("on");
    });
  });

  document.querySelectorAll(".proj-item").forEach(function (p) {
    p.addEventListener("click", function () {
      p.parentElement.querySelectorAll(".proj-item").forEach(function (x) { x.classList.remove("on"); });
      p.classList.add("on");
    });
  });

  document.querySelectorAll(".theme-card").forEach(function (c) {
    c.addEventListener("click", function () {
      c.parentElement.querySelectorAll(".theme-card").forEach(function (x) { x.classList.remove("on"); });
      c.classList.add("on");
    });
  });

  /* ---------------- todo / divider (delegated above) ---------------- */

  /* ---------------- composer submit ---------------- */
  function submitPrompt(field) {
    if (!field) return false;
    var text = field.value.trim();
    // Attachments alone are a valid turn, the same as in the old shell.
    var atts = wireAttachments();
    if (!text && !atts.length) return false;
    if (sendPrompt(text, atts)) {
      field.value = "";
      field.dispatchEvent(new Event("input"));
      clearFiles();
      return true;
    }
    toast("Not connected", "Start the gateway to send this message.", "warn");
    return false;
  }

  /* ---------------- composer autoresize ---------------- */
  var ta = document.getElementById("composerTa");
  function autoSize() {
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 168) + "px";
  }
  if (ta) {
    ta.addEventListener("input", autoSize);
    window.setTimeout(autoSize, 40);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitPrompt(ta);
      }
    });
  }

  /* ---------------- stream scroll button ---------------- */
  /* Owned by `JumpToLatest.vue`; the rail has a ResizeObserver so streaming
     growth is noticed without polling. */

  /* ---------------- copy buttons (delegated above) ---------------- */

  /* ---------------- permission mode switcher ---------------- */
  (function () {
    var wrap = document.getElementById("permWrap");
    if (!wrap) return;
    var btn = document.getElementById("permModeBtn");
    var shell = document.getElementById("pmShell");
    var label = document.getElementById("pmLabel");
    var cur = document.getElementById("pmCur");
    var hint = document.getElementById("pmHint");
    var MODES = {
      manual: { label: "Ask first", name: "Manual", hint: "Risky tool calls pause for your approval.", warn: false },
      auto: { label: "Auto", name: "Auto", hint: "Every tool call is approved without prompting.", warn: true },
      readonly: { label: "Read-only", name: "Read-only", hint: "Writes, commands, downloads and secret files are rejected.", warn: true }
    };

    function close() {
      wrap.classList.remove("open");
      shell.classList.remove("open");
      shell.setAttribute("aria-hidden", "true");
      btn.setAttribute("aria-expanded", "false");
    }
    function toggle() {
      var open = !wrap.classList.contains("open");
      wrap.classList.toggle("open", open);
      shell.classList.toggle("open", open);
      shell.setAttribute("aria-hidden", open ? "false" : "true");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    function apply(mode, silent) {
      var m = MODES[mode];
      if (!m) return;
      wrap.setAttribute("data-mode", mode);
      label.textContent = m.label;
      cur.textContent = m.name;
      hint.textContent = m.hint;
      hint.classList.toggle("warn", m.warn);
      [].forEach.call(shell.querySelectorAll(".pm-opt"), function (o) {
        o.classList.toggle("active", o.getAttribute("data-mode") === mode);
      });
      if (!silent) { setPermissionMode(mode); toast("Permission mode", m.name + " · " + m.hint, "ok"); }
    }

    btn.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    shell.addEventListener("click", function (e) {
      e.stopPropagation();
      var opt = e.target.closest(".pm-opt");
      if (!opt) return;
      apply(opt.getAttribute("data-mode"));
      close();
    });
    document.addEventListener("click", function () { if (wrap.classList.contains("open")) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    function pullMode() {
      syncPermissionMode().then(function (mode) { if (mode) apply(mode, true); });
    }
    apply("manual", true);
    pullMode();
    // The socket opens after this script runs, so the real mode is read again
    // once the gateway is reachable.
    window.addEventListener("pebble:gateway-ready", pullMode);
  })();

  /* ---------------- permission gate ---------------- */
  (function () {
    var gate = document.getElementById("permDialog");
    if (!gate) return;
    var body = document.getElementById("pgBody");

    var durMenu = document.createElement("div");
    durMenu.className = "dur-menu";
    durMenu.hidden = true;
    durMenu.innerHTML = [["5", "5 minutes"], ["15", "15 minutes"], ["30", "30 minutes"], ["60", "1 hour"], ["1440", "1 day"]]
      .map(function (p) { return '<button type="button" data-min="' + p[0] + '">' + p[1] + "</button>"; })
      .join("");
    document.body.appendChild(durMenu);

    var activeDur = null;
    function closeDur() {
      durMenu.hidden = true;
      if (activeDur) activeDur.classList.remove("open");
      activeDur = null;
    }
    function openDur(btn) {
      var rect = btn.getBoundingClientRect();
      durMenu.hidden = false;
      durMenu.style.left = Math.max(8, Math.min(rect.left, innerWidth - 140)) + "px";
      durMenu.style.top = Math.round(rect.bottom + 5) + "px";
      var cur = btn.getAttribute("data-min") || "15";
      Array.prototype.forEach.call(durMenu.children, function (b) {
        b.classList.toggle("on", b.getAttribute("data-min") === cur);
      });
      btn.classList.add("open");
      activeDur = btn;
    }

    durMenu.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-min]");
      if (!b || !activeDur) return;
      activeDur.setAttribute("data-min", b.getAttribute("data-min"));
      activeDur.querySelector("[data-dur-label]").textContent = b.textContent;
      closeDur();
    });
    document.addEventListener("click", function (e) {
      if (durMenu.hidden) return;
      if (e.target.closest(".dur-menu") || e.target.closest("[data-dur-btn]")) return;
      closeDur();
    });
    gate.addEventListener("click", function (e) {
      var durBtn = e.target.closest("[data-dur-btn]");
      if (durBtn) {
        if (activeDur === durBtn) { closeDur(); return; }
        closeDur();
        openDur(durBtn);
        return;
      }
      var rem = e.target.closest("[data-remember]");
      if (rem) rem.classList.toggle("on");
    });

    function checkEmpty() {
      if (permissions.queue.length) return;
      setTimeout(function () {
        permissions.open = false;
        toast("All requests handled", "The agent resumed with your decisions.", "ok");
      }, 380);
    }

    function settle(id, allow) {
      // The stamp, the deny tint and the slide-out are all store flags now.
      var req = settlePermission(id, allow);
      if (!req) return;
      var card = document.querySelector('.pass[data-id="' + id + '"]');
      var remember = card && card.querySelector("[data-remember]");
      var durBtn = card && card.querySelector("[data-dur-btn]");
      var minutes = remember && remember.classList.contains("on") && durBtn
        ? parseInt(durBtn.getAttribute("data-min") || "0", 10)
        : 0;
      decidePermission(id, allow, minutes || 0);
      setTimeout(function () {
        req.leaving = true;
        setTimeout(function () {
          dropPermission(id);
          checkEmpty();
        }, 300);
      }, 600);
    }

    if (body) {
      body.addEventListener("click", function (e) {
        var act = e.target.closest("[data-act]");
        if (!act) return;
        var card = act.closest(".pass");
        if (!card) return;
        settle(card.getAttribute("data-id"), act.getAttribute("data-act") === "allow");
      });
    }

    function settleAll(allow) {
      pendingPermissions().forEach(function (r, i) {
        setTimeout(function () { settle(r.id, allow); }, i * 90);
      });
    }

    var allowAll = document.getElementById("pgAllowAll");
    var denyAll = document.getElementById("pgDenyAll");
    var denyCur = document.getElementById("pgDenyCur");
    if (allowAll) allowAll.addEventListener("click", function () { settleAll(true); });
    if (denyAll) denyAll.addEventListener("click", function () { settleAll(false); });
    if (denyCur) denyCur.addEventListener("click", function () {
      var next = pendingPermissions()[0];
      if (next) settle(next.id, false);
    });
  })();

  /* ---------------- image viewer trigger ---------------- */
  document.querySelectorAll(".attach-chip").forEach(function (chip) {
    if (chip.textContent.indexOf("png") === -1) return;
    chip.style.cursor = "zoom-in";
    chip.addEventListener("click", function () { openOverlay("sheet-image"); });
  });

  /* ---------------- drop veil (fake) ---------------- */
  var veil = document.getElementById("dropVeil");
  var dragDepth = 0;
  window.addEventListener("dragenter", function (e) {
    e.preventDefault();
    dragDepth++;
    if (veil) veil.classList.add("show");
  });
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("dragleave", function () {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0 && veil) veil.classList.remove("show");
  });
  window.addEventListener("drop", function (e) {
    e.preventDefault();
    dragDepth = 0;
    if (veil) veil.classList.remove("show");
    toast("Attached", "2 files added to the composer.", "ok");
  });

  /* ---------------- clock & rate ---------------- */
  var clockEl = document.getElementById("clock");
  function tickClock() {
    /* The clock and the token rate are owned by the status bar component now:
       its Vue binding and this loop would fight over the same text node, and the
       rate was a random number dressed up as telemetry. */
  }

  /* The island timer is bound in `IslandWrap.vue`; it used to be advanced here
     from a fixed 134-second seed, which is why the readout never matched the
     run it was supposedly timing. */

  /* ---------------- keyboard ---------------- */
  document.addEventListener("keydown", function (e) {
    var meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openCmd();
    } else if (meta && e.key.toLowerCase() === "f") {
      e.preventDefault();
      setSessOpen(true);
    } else if (meta && e.key.toLowerCase() === "n") {
      e.preventDefault();
      newSession();
    } else if (meta && e.shiftKey && e.key.toLowerCase() === "l") {
      e.preventDefault();
      setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
    } else if (e.key === "Escape") {
      closeCmd();
      setSessOpen(false);
      setStageOpen(false);
      /* A confirmation sheet owns the key while it is open. Closing the panel
         that asked would take the question away and strand the answer. */
      var confirmEl = document.getElementById("confirmSheet");
      if (confirmEl && confirmEl.classList.contains("open")) return;
      document.querySelectorAll(".overlay.open").forEach(function (ov) {
        /* permDialog owns its own dismissal; confirmSheet is Vue state, and
           closing it here would leave the promise that opened it unresolved. */
        if (ov.id === "permDialog" || ov.id === "confirmSheet") return;
        closeOverlay(ov);
      });
      if (island) island.classList.remove("open");
    }
  });

  /* ---------------- project popover ---------------- */
  (function () {
    var chip = document.getElementById("projectChip");
    var pop = document.getElementById("projPop");
    if (!chip || !pop) return;
    function place() {
      var r = chip.getBoundingClientRect();
      var w = pop.offsetWidth || 392;
      pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 12)) + "px";
      pop.style.top = Math.round(r.bottom + 8) + "px";
    }
    function toggle(open) {
      var next = open === undefined ? !pop.classList.contains("open") : open;
      if (next) place();
      pop.classList.toggle("open", next);
      chip.classList.toggle("active", next);
      /* Vue owns what sits inside the popover, including the file preview it
         only mounts on demand. The runtime is the one place that knows the
         panel has closed, so it reports the transition instead of reaching
         into component state. */
      window.dispatchEvent(new CustomEvent("pebble:project-panel", { detail: { open: next } }));
    }
    chip.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    pop.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () { toggle(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") toggle(false); });
    /* The popover is anchored to the chip, so a window resize has to re-measure
       it. Its height is a calc() against the viewport, which reflows on its own;
       only the horizontal placement needs the second pass. */
    window.addEventListener("resize", function () {
      if (pop.classList.contains("open")) place();
    });
    /* Opening a file widens the panel, which moves its right edge. Vue owns that
       width; this is the runtime's cue to re-measure rather than guess. */
    window.addEventListener("pebble:project-relayout", function () {
      if (pop.classList.contains("open")) place();
    });

    /* Vue owns the rows and answers the gateway. The popover still closes
       itself, which is the one behaviour the prototype had that no component
       replaced. A remove click keeps it open: the list is being edited, not
       dismissed. */
    var list = document.getElementById("ppList");
    if (list) list.addEventListener("click", function (e) {
      if (e.target.closest(".pp-x")) return;
      if (e.target.closest(".pp-item")) toggle(false);
    });

    var spManage = document.querySelector(".sp-manage");
    if (spManage) spManage.addEventListener("click", function (e) {
      e.stopPropagation();
      toggle(true);
    });
  })();

  /* ---------------- bell ---------------- */
  var bell = document.getElementById("bellBtn");
  if (bell) bell.addEventListener("click", function () { openOverlay("sheet-notify"); });

  /* ---------------- conversation menu ---------------- */
  (function () {
    var menu = document.getElementById("convMenu");
    var list = document.getElementById("convList");
    if (!menu || !list) return;
    var current = null;
    function titleOf(conv) {
      var t = conv.querySelector(".conv-title");
      return t ? t.textContent.trim() : "Conversation";
    }
    function place(btn) {
      var r = btn.getBoundingClientRect();
      var w = 168, h = menu.offsetHeight || 152;
      var left = Math.min(r.right - w, innerWidth - w - 12);
      var top = r.bottom + 6;
      if (top + h > innerHeight - 10) top = r.top - h - 6;
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";
    }
    function open(btn, conv) {
      current = conv;
      menu.classList.add("open");
      menu.setAttribute("aria-hidden", "false");
      menu.querySelector('[data-act="pin"] span').textContent = conv.classList.contains("pinned") ? "Unpin" : "Pin";
      place(btn);
    }
    function close() {
      menu.classList.remove("open");
      menu.setAttribute("aria-hidden", "true");
    }
    list.addEventListener("click", function (e) {
      var btn = e.target.closest(".conv-more");
      if (!btn) return;
      e.stopPropagation();
      var conv = btn.closest(".conv");
      if (current === conv && menu.classList.contains("open")) { close(); return; }
      open(btn, conv);
    });
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () { if (menu.classList.contains("open")) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    menu.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b || !current) return;
      var act = b.getAttribute("data-act");
      var conv = current;
      close();
      if (act === "pin") {
        // Rows rendered from the store own their pin flag; only the seeded
        // prototype markup still flips a class here.
        var pinId = conv.getAttribute("data-id");
        if (pinId) {
          toast(toggleSessionPin(pinId) ? "Pinned" : "Unpinned", titleOf(conv), "ok");
        } else {
          toast(conv.classList.toggle("pinned") ? "Pinned" : "Unpinned", titleOf(conv), "ok");
        }
      } else if (act === "rename") {
        startRename(conv);
      } else if (act === "export") {
        exportMd(conv);
      } else if (act === "delete") {
        removeConv(conv);
      }
    });

    function startRename(conv) {
      var t = conv.querySelector(".conv-title");
      if (!t || t.querySelector("input")) return;
      var old = t.textContent;
      t.textContent = "";
      var inp = document.createElement("input");
      inp.className = "conv-rename";
      inp.value = old;
      t.appendChild(inp);
      inp.focus();
      inp.select();
      function done(save) {
        if (!inp.parentNode) return;
        var v = inp.value.trim();
        var next = save && v ? v : old;
        t.textContent = next;
        if (save && v && v !== old) {
          renameSession(conv.getAttribute("data-id") || "", v);
          toast("Renamed", v, "ok");
        }
      }
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); done(true); }
        else if (e.key === "Escape") { e.preventDefault(); done(false); }
      });
      inp.addEventListener("blur", function () { done(true); });
    }

    async function exportMd(conv) {
      var title = titleOf(conv);
      var saved = await exportSession(conv.getAttribute("data-id") || undefined);
      if (saved) {
        toast("Exported", saved.split(/[\\/]/).pop() || saved, "ok");
        return;
      }
      // The gateway writes the file next to its own data. When it refuses, ask
      // it for the markdown and download that: the old fallback built a header
      // from the row label alone, so the file carried no transcript.
      var md = await exportSessionMarkdown(conv.getAttribute("data-id") || undefined);
      if (!md) {
        toast("Export failed", "The gateway could not build the markdown.", "warn");
        return;
      }
      var blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (title.replace(/[^\w\u4e00-\u9fa5-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "conversation") + ".md";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
      toast("Exported", a.download, "ok");
    }
    async function removeConv(conv) {
      var title = titleOf(conv);
      var liveId = conv.getAttribute("data-id");
      var wasActive = conv.classList.contains("active");
      var ok = await askConfirm({
        title: "Delete session?",
        body: '"' + title + '" and its transcript are removed from this project.',
        confirmLabel: "Delete",
        danger: true,
        hint: "the stored transcript goes with it"
      });
      if (!ok) return;
      if (liveId) {
        // The store owns the row, so dropping it from the list is what removes
        // the element. `deleteSession` puts it back if the gateway refuses.
        if (await deleteSession(liveId)) toast("Conversation deleted", title, "warn");
        return;
      }
      conv.style.transition = "opacity .2s var(--ease), transform .2s var(--ease)";
      conv.style.opacity = "0";
      conv.style.transform = "translateX(-10px)";
      setTimeout(function () {
        conv.remove();
        if (wasActive) {
          var next = list.querySelector(".conv");
          if (next) next.classList.add("active");
        }
      }, 200);
      toast("Conversation deleted", title, "warn");
    }
  })();

  /* ---------------- composer pulse ---------------- */
  (function () {
    var ta = document.getElementById("composerTa");
    if (!ta) return;
    var composer = ta.closest(".composer");
    var pulse = document.getElementById("taPulse");
    var fill = document.getElementById("crFill");
    var val = document.getElementById("crVal");

    function autosize() {
      ta.style.height = "auto";
      ta.style.height = Math.min(200, ta.scrollHeight) + "px";
    }
    function sync() {
      var v = ta.value;
      var chars = v.length;
      var tok = Math.round(chars / 3.6);
      if (fill) fill.style.width = Math.min(100, 6 + tok / 18) + "%";
      if (val) val.textContent = "~" + tok + " tok";
      var lit = chars === 0 ? 0 : chars < 60 ? 1 : chars < 220 ? 2 : 3;
      if (pulse) {
        [].forEach.call(pulse.children, function (d, i) { d.classList.toggle("on", i < lit); });
      }
    }
    ta.addEventListener("input", function () {
      autosize();
      sync();
    });
    var hints = ["Tell Pebble what to build…", "Ask for a refactor, a review, or a bug hunt…", "Describe the surface you want to see…"];
    var hi = 0;
    setInterval(function () {
      if (document.activeElement === ta || ta.value.trim()) return;
      hi = (hi + 1) % hints.length;
      ta.placeholder = hints[hi] + "  (⏎ send · ⇧⏎ newline)";
    }, 4200);
    autosize();
    sync();
  })();

  /* ---------------- misc buttons ---------------- */
  var newBtn = document.getElementById("newSession");
  async function newSession() {
    setSessOpen(false);
    if (await createSession()) {
      toast("New session", "A clean transcript is ready.", "ok");
      return;
    }
    toast("Cannot create", "The gateway refused to start a session.", "warn");
  }
  if (newBtn) newBtn.addEventListener("click", newSession);


  var compact = document.getElementById("compactBtn");
  // Compaction rewrites the transcript, so the button asks first: the dialog
  // owns the `session.compress` call, this handler only opens it.
  if (compact) compact.addEventListener("click", function () { openOverlay("compactConfirm"); });

  var notes = document.getElementById("notesBtn");
  if (notes) notes.addEventListener("click", function () { ensureRightOpen(); selectTab("notes"); });

  /* The window is drawn without decorations, so these three buttons are the
     only way to minimise, maximise or close it. They reach the shell through
     __TAURI_INTERNALS__, the same bridge gateway.ts uses for the token, which
     keeps the page free of an @tauri-apps/api dependency. Outside the shell the
     buttons fall back to the prototype toast. */
  function windowCommand(cmd) {
    var internals = window.__TAURI_INTERNALS__;
    if (!internals || !internals.invoke) {
      toast("Window", "Only available in the desktop shell.", "warn");
      return;
    }
    var meta = internals.metadata || {};
    var label = (meta.currentWindow && meta.currentWindow.label) || "main";
    internals.invoke(cmd, { label: label }).catch(function (err) {
      console.warn("[orbit] window command failed", cmd, err);
    });
  }

  var wm = document.getElementById("winMin");
  var wx = document.getElementById("winMax");
  var wc = document.getElementById("winClose");
  if (wm) wm.addEventListener("click", function () { windowCommand("plugin:window|minimize"); });
  if (wx) wx.addEventListener("click", function () { windowCommand("plugin:window|toggle_maximize"); });
  if (wc) wc.addEventListener("click", function () { windowCommand("plugin:window|close"); });

  /* ---------------- conversation selection ---------------- */
  document.querySelectorAll(".conv").forEach(function (c) {
    c.addEventListener("click", function () {
      document.querySelectorAll(".conv").forEach(function (x) { x.classList.remove("active"); });
      c.classList.add("active");
    });
  });

  /* ---------------- decision card (delegated above) ---------------- */

  /* ---------------- llm control deck ---------------- */
  (function () {
    var pane = document.querySelector('[data-pane="llm"]');
    if (!pane) return;
    // The pane is rendered from the store now, so its own bindings own the
    // selection, the sliders and the save buttons.
    if (pane.hasAttribute("data-live")) return;
    pane.querySelectorAll(".lm-gauge").forEach(function (g) {
      var input = g.querySelector("input");
      var fill = g.querySelector("i");
      var knob = g.querySelector("b");
      var out = g.querySelector(".lm-v");
      var max = parseFloat(input.max), min = parseFloat(input.min);
      function sync() {
        var v = parseFloat(input.value);
        var pct = ((v - min) / (max - min)) * 100;
        fill.style.setProperty("--v", pct + "%");
        knob.style.setProperty("--v", pct + "%");
        out.textContent = g.getAttribute("data-k") === "maxtok" ? Math.round(v).toLocaleString() : v.toFixed(2).replace(/0$/, "");
      }
      input.addEventListener("input", sync);
      sync();
    });

    var agents = pane.querySelector("#lmAgents");
    if (agents) agents.addEventListener("click", function (e) {
      var a = e.target.closest(".lm-agent");
      if (!a) return;
      agents.querySelectorAll(".lm-agent").forEach(function (x) { x.classList.remove("on"); });
      a.classList.add("on");
    });
    var steps = pane.querySelector("#lmSteps");
    if (steps) steps.addEventListener("click", function (e) {
      var b = e.target.closest(".lm-step");
      if (!b) return;
      steps.querySelectorAll(".lm-step").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
    });
    var modes = pane.querySelector("#lmModes");
    if (modes) modes.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      modes.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
    });
    var think = pane.querySelector("#lmThink");
    if (think) think.addEventListener("click", function () {
      var on = think.classList.toggle("on");
      think.querySelector(".lm-tstate").textContent = on ? "enabled" : "disabled";
    });
  })();

  /* ---------------- message timeline ---------------- */
  (function () {
    var tl = document.getElementById("timeline");
    var body = document.querySelector(".stream-body");
    if (!tl || !body) return;
    var nodes = [].slice.call(tl.querySelectorAll(".tl-node"));
    var msgs = [].slice.call(document.querySelectorAll(".stream-inner .msg, .stream-inner .summary, .stream-inner .cdiv"));
    function go(n) {
      var i = parseInt(n.getAttribute("data-idx"), 10);
      var target = msgs[i] || msgs[msgs.length - 1];
      if (!target) return;
      var top = body.scrollTop + target.getBoundingClientRect().top - body.getBoundingClientRect().top - 24;
      body.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
    nodes.forEach(function (n) { n.addEventListener("click", function () { go(n); }); });
    body.addEventListener("scroll", function () {
      var p = body.scrollTop / Math.max(1, body.scrollHeight - body.clientHeight);
      var idx = Math.min(nodes.length - 1, Math.round(p * (nodes.length - 1)));
      nodes.forEach(function (n, i) { n.classList.toggle("on", i === idx); });
    }, { passive: true });
    var bottom = document.getElementById("tlBottom");
    if (bottom) bottom.addEventListener("click", function () {
      body.scrollTo({ top: body.scrollHeight, behavior: "smooth" });
    });
  })();

  /* Pricing is rendered by UsagePane from the gateway's model_pricing.json, so
     the prototype's fake table and its DOM handlers are gone. */


  /* ---------------- llm config pane ---------------- */
  (function () {
    var pane = document.querySelector('[data-pane="llm"]');
    if (!pane) return;
    if (pane.hasAttribute("data-live")) return;
    pane.querySelectorAll('.llm-sl input[type="range"]').forEach(function (r) {
      var out = r.parentElement.querySelector("b");
      var fmt = function (v) {
        var n = parseFloat(v);
        if (r.max === "32000") return Math.round(n).toLocaleString();
        return n.toFixed(2).replace(/0$/, "");
      };
      out.textContent = fmt(r.value);
      r.addEventListener("input", function () { out.textContent = fmt(r.value); });
    });

    pane.addEventListener("click", function (e) {
      var pill = e.target.closest(".llm-pills button");
      if (pill) {
        pill.parentElement.querySelectorAll("button").forEach(function (b) { b.classList.remove("on"); });
        pill.classList.add("on");
        return;
      }
      var prov = e.target.closest(".llm-prov button");
      if (prov && !prov.classList.contains("add")) {
        pane.querySelectorAll(".llm-prov button").forEach(function (b) { b.classList.remove("on"); });
        prov.classList.add("on");
        return;
      }
      var ag = e.target.closest(".llm-agent");
      if (ag) {
        pane.querySelectorAll(".llm-agent").forEach(function (b) { b.classList.remove("on"); });
        ag.classList.add("on");
        return;
      }
      var tg = e.target.closest(".llm-tg");
      if (tg) { tg.classList.toggle("on"); }
    });

    var save = document.getElementById("llmSave");
    if (save) save.addEventListener("click", function () {
      toast("Provider saved", "anthropic-main written to config.json.", "ok");
    });
    var del = pane.querySelector(".llm-del");
    if (del) del.addEventListener("click", function () {
      toast("Provider removed", "Fell back to the default provider.", "warn");
    });
  })();

  /* ---------------- card entrance choreography ---------------- */
  (function () {
    var cards = document.querySelectorAll(".stream-inner .thinking, .stream-inner .md, .stream-inner .tool, .stream-inner .code, .stream-inner .summary, .stream-inner .qcard");
    Array.prototype.forEach.call(cards, function (el, i) {
      el.style.setProperty("--i", i);
    });
    var soft = document.querySelectorAll(".stream-inner .md, .stream-inner .code, .stream-inner .summary, .stream-inner .qcard");
    Array.prototype.forEach.call(soft, function (el, i) {
      el.classList.add("enter");
      el.style.animationDelay = (i * 45 + 140) + "ms";
    });
  })();

  /* ---------------- pointer parallax ---------------- */
  var beams = document.querySelectorAll(".beam");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (beams.length && !reduceMotion) {
    var rafId = 0, px = 0, py = 0;
    document.addEventListener("mousemove", function (e) {
      px = e.clientX / window.innerWidth - .5;
      py = e.clientY / window.innerHeight - .5;
      if (rafId) return;
      rafId = window.requestAnimationFrame(function () {
        rafId = 0;
        beams.forEach(function (b, i) {
          var k = (i + 1) * 9;
          b.style.translate = (px * k).toFixed(1) + "px " + (py * k * .7).toFixed(1) + "px";
        });
      });
    });
  }

  /* The prototype plays a few scripted reveals on load (fake connection toast,
     plan slip, preview stage). A live gateway makes them wrong, so wire.js
     clears this flag as soon as a session is attached. */
  /* ---------------- boot ---------------- */
  try {
    var saved = window.localStorage.getItem("pebble-orbit-theme");
    if (saved === "dark" || saved === "light") setTheme(saved);
  } catch (err) {}

})();

}
