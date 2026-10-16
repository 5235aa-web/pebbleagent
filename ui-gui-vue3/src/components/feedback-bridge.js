/**
 * PebbleAgent Feedback Bridge — injected into iframe HTML preview.
 *
 * Lets users click any element on the page → comment popup → save/cancel.
 * Comments are pushed via postMessage to the parent HtmlPreview.vue.
 *
 * Activated when parent sends 'pebble-feedback-activate' message.
 * Deactivated when parent sends 'pebble-feedback-deactivate'.
 */
(function () {
  if (window.__pebbleFeedbackInit) return;
  window.__pebbleFeedbackInit = true;

  // ── State ──
  let active = false;
  let comments = [];                // { cfId, selector, tag, textSnippet, body }
  let editorEl = null;             // comment editor DOM
  let editorTarget = null;         // currently targeted element (for save)
  let hoverEl = null;              // currently hovered element (for outline)
  let markerMap = new Map();       // cfId → marker DOM element
  let cfCounter = 0;

  const TEXT_SNIPPET_MAX = 200;

  // ── Unique ID ──
  function uid() { return 'cf-' + Date.now().toString(36) + '-' + (cfCounter++); }

  // ── CSS Selector builder ──
  function buildSelector(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const path = [];
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      let seg = cur.tagName.toLowerCase();
      if (cur.id) { path.unshift('#' + CSS.escape(cur.id)); break; }
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (same.length > 1) {
          const idx = same.indexOf(cur) + 1;
          seg += ':nth-child(' + idx + ')';
        }
      }
      path.unshift(seg);
      cur = cur.parentElement;
    }
    return path.join(' > ');
  }

  // ── Editor DOM ──
  function createEditor() {
    if (editorEl) return;
    const root = document.createElement('div');
    root.id = '__pebble_fb_root';

    const editor = document.createElement('div');
    editor.className = '__pf_editor';
    editor.innerHTML = `
      <div class="__pf_editor_quote"></div>
      <textarea placeholder="Enter your feedback..." rows="3"></textarea>
      <div class="__pf_editor_actions">
        <button class="__pf_btn __pf_btn_danger" data-action="cancel">Cancel</button>
        <button class="__pf_btn_primary" data-action="save">Save</button>
      </div>`;
    root.appendChild(editor);
    document.body.appendChild(root);
    editorEl = editor;

    // Click delegation for editor buttons
    editor.addEventListener('click', function (e) {
      const btn = e.target.closest('button');
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === 'save') saveComment();
      if (action === 'cancel') hideEditor();
    });
  }

  function showEditor(el, x, y) {
    if (!active) return;
    createEditor();
    editorTarget = el;

    // Position near click, keep in viewport
    let left = x + 10;
    let top = y + 10;
    const ew = 320;
    const eh = 200;
    if (left + ew > window.innerWidth) left = x - ew - 10;
    if (top + eh > window.innerHeight) top = y - eh - 10;
    if (left < 0) left = 8;
    if (top < 0) top = 8;

    editorEl.style.left = left + 'px';
    editorEl.style.top = top + 'px';
    editorEl.classList.add('__pf_visible');

    // Show element text snippet as quote
    const quote = editorEl.querySelector('.__pf_editor_quote');
    const text = (el.textContent || '').trim().slice(0, TEXT_SNIPPET_MAX);
    quote.textContent = text || '(empty element)';

    // Clear textarea
    const ta = editorEl.querySelector('textarea');
    ta.value = '';
    ta.focus();
  }

  function hideEditor() {
    if (editorEl) {
      editorEl.classList.remove('__pf_visible');
    }
    editorTarget = null;
  }

  function saveComment() {
    if (!editorTarget) return;
    const ta = editorEl.querySelector('textarea');
    const body = ta.value.trim();
    if (!body) { hideEditor(); return; }

    const el = editorTarget;
    const cfId = uid();

    // Ensure element has a stable data-cf-id
    if (!el.dataset.cfId) el.dataset.cfId = cfId;

    const comment = {
      cfId: cfId,
      selector: buildSelector(el),
      tag: el.tagName.toLowerCase(),
      textSnippet: (el.textContent || '').trim().slice(0, TEXT_SNIPPET_MAX),
      body: body
    };

    // Update or add
    const idx = comments.findIndex(c => c.cfId === cfId);
    if (idx >= 0) comments[idx] = comment; else comments.push(comment);

    // Show marker on the element
    addMarker(el, cfId);

    // Notify parent
    notifyParent();

    hideEditor();
    // Keep element selected (outline stays)
  }

  // ── Markers ──
  function addMarker(el, cfId) {
    removeMarker(cfId);
    const rect = el.getBoundingClientRect();
    const marker = document.createElement('div');
    marker.className = '__pf_marker';
    marker.title = 'Click to view/delete comment';
    marker.style.left = (rect.right + window.scrollX - 8) + 'px';
    marker.style.top = (rect.top + window.scrollY - 8) + 'px';
    marker.innerHTML = '💬<span class="__pf_marker_remove">×</span>';

    marker.addEventListener('click', function (e) {
      if (e.target.classList.contains('__pf_marker_remove')) {
        removeComment(cfId);
      } else {
        // Re-show editor to edit
        const existing = comments.find(c => c.cfId === cfId);
        if (existing) {
          showEditor(el, rect.left, rect.bottom);
          const ta = editorEl.querySelector('textarea');
          ta.value = existing.body;
        }
      }
    });

    document.body.appendChild(marker);
    markerMap.set(cfId, marker);
  }

  function removeMarker(cfId) {
    const m = markerMap.get(cfId);
    if (m) { m.remove(); markerMap.delete(cfId); }
  }

  function removeComment(cfId) {
    comments = comments.filter(c => c.cfId !== cfId);
    removeMarker(cfId);
    // Remove outline from element
    const el = document.querySelector('[data-cf-id="' + cfId + '"]');
    if (el) {
      el.classList.remove('__pf_selected');
      el.removeAttribute('data-cf-id');
    }
    notifyParent();
  }

  function refreshMarkers() {
    // Reposition markers after scroll/resize
    markerMap.forEach(function (marker, cfId) {
      const el = document.querySelector('[data-cf-id="' + cfId + '"]');
      if (el) {
        const rect = el.getBoundingClientRect();
        marker.style.left = (rect.right + window.scrollX - 8) + 'px';
        marker.style.top = (rect.top + window.scrollY - 8) + 'px';
      }
    });
  }

  // ── Parent communication ──
  function notifyParent() {
    window.parent.postMessage({
      type: 'pebble-feedback-update',
      comments: comments.slice()
    }, '*');
  }

  // ── Click handler ──
  function handleClick(e) {
    if (!active) return;
    // Ignore clicks on our own UI
    if (e.target.closest('#__pebble_fb_root')) return;

    e.preventDefault();
    e.stopPropagation();

    const el = e.target;
    // Mark element as selected
    if (hoverEl) hoverEl.classList.remove('__pf_hover');
    el.classList.add('__pf_selected');

    showEditor(el, e.clientX, e.clientY);
  }

  // ── Hover handler (for element highlighting) ──
  function handleMouseMove(e) {
    if (!active) return;
    // Skip our own UI
    if (e.target.closest('#__pebble_fb_root')) {
      if (hoverEl) { hoverEl.classList.remove('__pf_hover'); hoverEl = null; }
      return;
    }
    if (e.target === hoverEl) return;
    if (hoverEl) hoverEl.classList.remove('__pf_hover');
    hoverEl = e.target;
    hoverEl.classList.add('__pf_hover');
  }

  // ── Activate / Deactivate ──
  function activate() {
    if (active) return;
    active = true;
    document.addEventListener('click', handleClick, true);
    document.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('scroll', refreshMarkers);
    window.addEventListener('resize', refreshMarkers);
    document.body.style.cursor = 'crosshair';
    window.parent.postMessage({ type: 'pebble-feedback-ready' }, '*');
  }

  function deactivate() {
    active = false;
    document.removeEventListener('click', handleClick, true);
    document.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('scroll', refreshMarkers);
    window.removeEventListener('resize', refreshMarkers);
    document.body.style.cursor = '';
    if (hoverEl) { hoverEl.classList.remove('__pf_hover'); hoverEl = null; }
    hideEditor();
    // Remove outlines
    document.querySelectorAll('.__pf_selected').forEach(el => el.classList.remove('__pf_selected'));
  }

  // ── Collect all comments (for parent to read before submit) ──
  function collect() {
    return comments.slice();
  }

  // ── Listen for parent messages ──
  window.addEventListener('message', function (e) {
    if (!e.data || typeof e.data !== 'object') return;
    switch (e.data.type) {
      case 'pebble-feedback-activate':
        activate();
        break;
      case 'pebble-feedback-deactivate':
        deactivate();
        break;
      case 'pebble-feedback-collect':
        window.parent.postMessage({
          type: 'pebble-feedback-collect-response',
          comments: collect()
        }, '*');
        break;
    }
  });
})();
