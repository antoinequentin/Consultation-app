/* ==========================================================================
   Boussole — utilitaires JS communs aux 3 écrans (participant / animateur /
   projection). Pas de framework : DOM natif + WebSocket natif, pour rester
   dans l'esprit "un seul service Python" demandé (FastAPI + WebSocket).
   ========================================================================== */

const Boussole = (() => {
  // -- Identité participant (persistée en localStorage, pas de cookie) ----
  function getParticipantId() {
    let id = localStorage.getItem("boussole_pid");
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : "p-" + Math.random().toString(36).slice(2) + Date.now());
      localStorage.setItem("boussole_pid", id);
    }
    return id;
  }

  function getDisplayName() {
    return localStorage.getItem("boussole_name") || "";
  }
  function setDisplayName(name) {
    if (name) localStorage.setItem("boussole_name", name);
  }

  function getHostToken(code) {
    return localStorage.getItem("boussole_host_token_" + code) || "";
  }
  function setHostToken(code, token) {
    localStorage.setItem("boussole_host_token_" + code, token);
  }
  function clearHostToken(code) {
    localStorage.removeItem("boussole_host_token_" + code);
  }

  // -- Connexion WebSocket robuste (reconnexion avec backoff) -------------
  function connect(code, { role = "participant", token = "", name = "" } = {}) {
    const listeners = {};
    let socket = null;
    let attempt = 0;
    let closedByUser = false;
    let connected = false;

    function on(type, cb) {
      (listeners[type] = listeners[type] || []).push(cb);
      return api;
    }
    function emit(type, payload) {
      (listeners[type] || []).forEach((cb) => cb(payload));
      (listeners["*"] || []).forEach((cb) => cb(type, payload));
    }

    function buildUrl() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const params = new URLSearchParams({ role });
      if (role === "participant") {
        params.set("pid", getParticipantId());
        if (name) params.set("name", name);
      } else if (role === "host") {
        params.set("token", token);
      }
      return `${proto}//${location.host}/ws/${code}?${params.toString()}`;
    }

    function open() {
      socket = new WebSocket(buildUrl());
      socket.onopen = () => {
        connected = true;
        attempt = 0;
        emit("_connected", {});
      };
      socket.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          emit(msg.type, msg.payload);
        } catch (e) {
          console.error("Message WS invalide", e);
        }
      };
      socket.onclose = (evt) => {
        connected = false;
        emit("_disconnected", { code: evt.code });
        if (evt.code === 4401) { emit("_auth_error", {}); return; }
        if (evt.code === 4404) { emit("_not_found", {}); return; }
        if (closedByUser) return;
        attempt += 1;
        const delay = Math.min(1000 * Math.pow(1.6, attempt), 10000);
        setTimeout(open, delay);
      };
      socket.onerror = () => { try { socket.close(); } catch (e) {} };
    }

    function send(type, payload = {}) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type, payload }));
        return true;
      }
      return false;
    }

    function close() {
      closedByUser = true;
      if (socket) socket.close();
    }

    open();
    // ping applicatif léger pour garder la connexion vivante derrière les proxys
    setInterval(() => { if (connected) send("ping", {}); }, 25000);

    const api = { on, send, close, isConnected: () => connected };
    return api;
  }

  // -- Cadran boussole (signature visuelle / indicateur de phase) ---------
  const DIAL_LABELS = [
    "Accueil", "Proposition de projets", "Vote du projet",
    "Impacts positifs", "Impacts négatifs", "Vote (cotation)", "Améliorations", "Terminé",
  ];

  function phaseToDialIndex(phase, step) {
    if (phase === "consultation") return 3 + Math.max(0, Math.min(3, (step || 1) - 1));
    if (phase === "ended") return 7;
    if (phase === "project_submission") return 1;
    if (phase === "project_vote") return 2;
    return 0;
  }

  function dialSVG(index, { compact = false } = {}) {
    const total = DIAL_LABELS.length;
    const step = 360 / total;
    let ticks = "";
    for (let i = 0; i < total; i++) {
      const angle = i * step;
      const active = i === index;
      const passed = i < index;
      const color = active ? "var(--brass)" : passed ? "var(--brass-100)" : "var(--border-strong)";
      ticks += `<line x1="100" y1="9" x2="100" y2="${compact ? 20 : 24}" stroke="${color}" stroke-width="${active ? 5 : 2.5}" stroke-linecap="round" transform="rotate(${angle} 100 100)"/>`;
    }
    const needleAngle = index * step;
    return `<svg viewBox="0 0 200 200" width="100%" height="100%" role="img" aria-label="Étape : ${DIAL_LABELS[index]}">
      <circle cx="100" cy="100" r="92" fill="none" stroke="var(--border)" stroke-width="1.5"/>
      ${ticks}
      <g class="dial-needle" transform="rotate(${needleAngle} 100 100)">
        <polygon points="100,30 93,100 107,100" fill="var(--brass)"/>
        <line x1="100" y1="100" x2="100" y2="138" stroke="var(--ink-faint)" stroke-width="3" stroke-linecap="round"/>
      </g>
      <circle cx="100" cy="100" r="8" fill="var(--brass)" stroke="var(--surface)" stroke-width="2.5"/>
    </svg>`;
  }

  function renderDial(el, phase, step, { compact = false, showLabel = true, size = null } = {}) {
    const index = phaseToDialIndex(phase, step);
    el.classList.add("dial");
    if (compact) el.classList.add("dial-compact");
    const sizeStyle = size ? `width:${size}px;height:${size}px;` : "";
    el.innerHTML = `
      <div style="${sizeStyle || (compact ? 'width:52px;height:52px;' : 'width:160px;height:160px;')}">${dialSVG(index, { compact })}</div>
      ${showLabel ? `<div class="dial-label">${DIAL_LABELS[index]}</div>` : ""}
    `;
  }

  // -- Notifications (toasts) ----------------------------------------------
  function ensureToastStack() {
    let stack = document.querySelector(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, level = "info") {
    const stack = ensureToastStack();
    const el = document.createElement("div");
    el.className = `toast toast-${level}`;
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s ease";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 320);
    }, 3600);
  }

  // -- Divers ---------------------------------------------------------------
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function fmtPct(n) {
    return `${Math.round(n)}%`;
  }

  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  // Badge de catégorie coloré (référentiel "Boussole de la Transition
  // Écologique" : ADAPTATION, ATTÉNUATION, RESSOURCE EN EAU, BIODIVERSITÉ,
  // POLLUTION, ÉCONOMIE CIRCULAIRE — couleur transmise par le serveur,
  // fidèle à get_category_color() de l'application d'origine).
  function categoryBadge(categorie, color) {
    if (!categorie) return "";
    const bg = color || "#000091";
    return `<span class="badge" style="background:${bg}1a;color:${bg};border:1.5px solid ${bg}55;">${escapeHtml(categorie)}</span>`;
  }

  return {
    getParticipantId, getDisplayName, setDisplayName,
    getHostToken, setHostToken, clearHostToken,
    connect, renderDial, DIAL_LABELS, phaseToDialIndex,
    toast, escapeHtml, fmtPct, qs, qsa, categoryBadge,
  };
})();
