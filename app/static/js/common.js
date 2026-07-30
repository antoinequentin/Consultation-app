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
  // Luminance relative (WCAG) d'une couleur hex, pour choisir un texte
  // noir ou blanc garantissant un contraste suffisant (≥ 4.5:1) quelle
  // que soit la couleur d'axe transmise par le serveur — y compris pour
  // un axe personnalisé ajouté par l'animateur avec une teinte imprévue.
  function relativeLuminance(hex) {
    const h = hex.replace("#", "");
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.substring(i, i + 2), 16) / 255);
    const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }

  function categoryBadge(categorie, color) {
    if (!categorie) return "";
    const bg = color || "#000091";
    let textColor = "#fff";
    try {
      const contrastWhite = (1.05) / (relativeLuminance(bg) + 0.05);
      if (contrastWhite < 4.5) textColor = "#161616";
    } catch (e) { /* couleur non parseable : on garde le texte blanc par défaut */ }
    return `<span class="badge-axe" style="--axe-color:${bg};color:${textColor};"><span class="badge-axe-dot" style="background:${textColor === "#fff" ? "#fff" : "rgba(22,22,22,.55)"};"></span>${escapeHtml(categorie)}</span>`;
  }

  // -- Repères réglementaires nationaux par axe --------------------------
  // Rappel factuel des objectifs français et de la stratégie de référence
  // associés à chaque axe de consultation, pour donner aux participants un
  // repère chiffré et sourcé sans se substituer à la question posée par
  // l'axe. Contenu vérifié par recherche web (sources officielles
  // .gouv.fr) plutôt qu'estimé — à mettre à jour si ces stratégies sont
  // réviséés (la plupart le sont tous les 5 ans).
  const AXIS_NATIONAL_TARGETS = {
    "ADAPTATION": {
      strategy: "PNACC 3 (Plan national d'adaptation au changement climatique)",
      text: "Anticiper un réchauffement de +2°C en France d'ici 2050 (trajectoire de référence pour l'adaptation, TRACC) et déployer des solutions d'adaptation fondées sur la nature.",
    },
    "ATTÉNUATION": {
      strategy: "SNBC (Stratégie nationale bas-carbone)",
      text: "Atteindre la neutralité carbone en France d'ici 2050, avec une réduction d'au moins 55 % des émissions de gaz à effet de serre d'ici 2030 (par rapport à 1990).",
    },
    "RESSOURCE EN EAU": {
      strategy: "Plan Eau (2023)",
      text: "Réduire de 10 % les prélèvements d'eau d'ici 2030 et développer la réutilisation des eaux usées traitées (objectif : 10 % des eaux usées recyclées, contre moins de 1 % aujourd'hui).",
    },
    "BIODIVERSITÉ": {
      strategy: "SNB 2030 (Stratégie nationale biodiversité)",
      text: "Placer 10 % du territoire national sous protection forte d'ici 2030 et diviser par deux le rythme d'artificialisation des sols (objectif ZAN, zéro artificialisation nette).",
    },
    "POLLUTION": {
      strategy: "PREPA (Plan national de réduction des émissions de polluants atmosphériques)",
      text: "Réduire d'ici 2030 (par rapport à 2005) les émissions de particules fines PM2,5 de 57 %, d'oxydes d'azote de 69 %, et de dioxyde de soufre de 77 %.",
    },
    "ÉCONOMIE CIRCULAIRE": {
      strategy: "FREC (Feuille de route pour l'économie circulaire)",
      text: "Diviser par deux les quantités de déchets non dangereux mis en décharge d'ici 2025 (par rapport à 2010) et tendre vers 100 % de plastiques recyclés.",
    },
  };

  function axisNationalTarget(categorie) {
    if (!categorie) return "";
    const ref = AXIS_NATIONAL_TARGETS[categorie.trim().toUpperCase()];
    if (!ref) return "";
    return `
      <p class="axis-national-target text-sm text-soft">
        <strong>${escapeHtml(ref.strategy)}</strong> — ${escapeHtml(ref.text)}
      </p>`;
  }

  // Encart carte intégré (cartes.gouv.fr) avec lien plein écran en secours
  // (§2.2c / §3 du cahier des charges) — réutilisé sur les cartes projet et
  // le futur volet "participants et projections".
  function mapEmbed(mapUrl, { height = 180 } = {}) {
    if (!mapUrl) return "";
    const safeUrl = escapeHtml(mapUrl);
    return `
      <div class="map-embed">
        <iframe src="${safeUrl}" style="width:100%;height:${height}px;border:0;border-radius:8px;" loading="lazy" referrerpolicy="no-referrer-when-downgrade" title="Localisation du projet" onerror="this.closest('.map-embed').classList.add('map-embed-error')"></iframe>
        <a class="map-embed-fallback text-xs" href="${safeUrl}" target="_blank" rel="noopener">Ouvrir la carte en plein écran ↗</a>
      </div>`;
  }

  // Bandeau d'aide contextuelle par étape de consultation (§5.2). Stocké en
  // dur ici : pas de table dédiée nécessaire, ce ne sont que des textes
  // d'accompagnement pour l'animateur.
  const STEP_HELP = {
    1: "Laissez les participants lister les impacts positifs, sans juger, avant de passer à la suite.",
    2: "Invitez les participants à identifier les points de vigilance ou impacts négatifs.",
    3: "Invitez les participants à voter avant de passer à l'étape suivante.",
    4: "Recueillez les pistes d'amélioration concrètes proposées par les participants.",
  };
  const STEP_LABELS = { 1: "Impacts positifs", 2: "Impacts négatifs", 3: "Vote", 4: "Améliorations" };

  // Stepper de consultation : ligne unique "◀ Axe précédent | 1 → 2 → 3 → 4 |
  // Axe suivant ▶" avec l'étape courante mise en évidence, + bandeau d'aide
  // sous la ligne. Les boutons appellent directement `window.__hostAction`
  // (déjà exposée globalement par host.js pour tous les onclick générés
  // dynamiquement), donc ce composant ne fonctionne que côté écran animateur.
  function consultationStepper(c) {
    const steps = [1, 2, 3, 4].map((st) => `
      <button class="stepper-step ${c.step === st ? "active" : ""} ${c.step > st ? "done" : ""}" ${c.step === st ? 'aria-current="step"' : ""} onclick="__hostAction('set_step',{step:${st}})">
        <span class="stepper-num">${st}</span><span class="stepper-label">${STEP_LABELS[st]}</span>
      </button>
      ${st < 4 ? `<span class="stepper-arrow" aria-hidden="true">→</span>` : ""}
    `).join("");
    return `
      <div class="stepper-row">
        <button class="btn btn-ghost btn-sm stepper-axis-btn" ${c.axis_index <= 0 ? "disabled" : ""} onclick="__hostAction('prev_axis')">◀ Axe précédent</button>
        <div class="stepper-steps">${steps}</div>
        <button class="btn btn-ghost btn-sm stepper-axis-btn" ${c.axis_index >= c.axis_count - 1 ? "disabled" : ""} onclick="__hostAction('next_axis')">Axe suivant ▶</button>
      </div>
      <div class="stepper-help-row">
        <p class="stepper-help text-sm text-soft">${STEP_HELP[c.step] || ""}</p>
        <div class="step-timer-slot" data-step-timer-host></div>
      </div>
    `;
  }

  // Mini-stepper de progression par axe (§3 du cahier des charges) — vue
  // LECTURE SEULE de la progression du projet (à ne pas confondre avec
  // `consultationStepper`, réservé à l'écran animateur car il déclenche des
  // actions). Affiche les axes déjà traités / l'axe courant / les axes
  // restants, à partir de `axis_index` et `axis_count` déjà exposés par
  // `build_state` — aucune donnée backend supplémentaire nécessaire.
  function axisProgress(axisIndex, axisCount) {
    if (!axisCount || axisCount <= 1) return "";
    let dots = "";
    for (let i = 0; i < axisCount; i++) {
      const cls = i === axisIndex ? "current" : i < axisIndex ? "done" : "todo";
      dots += `<span class="axis-dot ${cls}" title="Axe ${i + 1}/${axisCount}"></span>`;
    }
    return `
      <div class="axis-progress">
        <div class="axis-progress-dots">${dots}</div>
        <div class="text-xs text-faint">Axe ${axisIndex + 1} / ${axisCount}</div>
      </div>`;
  }

  // Volet projet permanent (§3 du cahier des charges) : titre, image,
  // résumé, encart carte et métadonnées clés, affiché en colonne fixe
  // pendant toute la phase de consultation (participant ET écran de
  // projection). `variant` ajuste seulement quelques détails d'affichage
  // (le texte sur fond sombre pour le projecteur).
  function projectPanel(project, { axisIndex = 0, axisCount = 0, variant = "light" } = {}) {
    if (!project) return "";
    const metaRows = [
      ["Porteur", project.porteur],
      ["Budget", project.budget],
      ["Territoire", project.territoire],
      ["Stade", project.stade],
    ].filter(([, v]) => v);
    const dark = variant === "dark";
    return `
      <aside class="project-panel ${dark ? "project-panel-dark" : ""}">
        ${project.is_mine ? `
          <p class="conflict-badge" role="note">
            <span aria-hidden="true">⚠️</span> Vous consultez votre propre projet — pensez à rester objectif dans vos contributions.
          </p>` : ""}
        ${project.image_url ? `<img class="project-panel-img" src="${escapeHtml(project.image_url)}" alt="" onerror="this.remove()">` : ""}
        <h2 class="project-panel-title">${escapeHtml(project.title)}</h2>
        ${project.description ? `<p class="project-panel-desc text-sm">${escapeHtml(project.description)}</p>` : ""}
        ${metaRows.length ? `
          <dl class="project-panel-meta">
            ${metaRows.map(([label, v]) => `<dt>${label}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
          </dl>` : ""}
        ${project.map_url ? mapEmbed(project.map_url, { height: 160 }) : ""}
        ${axisProgress(axisIndex, axisCount)}
      </aside>`;
  }

  // -- Minuteur par étape (§5.1 du cahier des charges) ---------------------
  // Calculé côté client à partir de `step_started_at` (ISO, horodatage
  // serveur) + `step_duration_seconds`, diffusés dans `consultation` par
  // build_state. Pas de valeur "temps restant" envoyée telle quelle par le
  // serveur : elle se périmerait entre deux diffusions WebSocket. Chaque
  // écran (host/participant/projecteur) appelle `mountStepTimer` avec son
  // propre conteneur ; un seul intervalle par écran, nettoyé/relancé à
  // chaque nouvel état reçu.
  function stepTimerRemainingSeconds(consultation) {
    if (!consultation || !consultation.step_started_at || !consultation.step_duration_seconds) return null;
    const started = new Date(consultation.step_started_at).getTime();
    if (Number.isNaN(started)) return null;
    const elapsed = (Date.now() - started) / 1000;
    return Math.max(0, Math.round(consultation.step_duration_seconds - elapsed));
  }

  function fmtDuration(totalSeconds) {
    const s = Math.max(0, Math.round(totalSeconds));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  /**
   * Monte (ou met à jour) un minuteur vivant dans `el`. Retourne une
   * fonction `stop()` à appeler quand l'écran change de vue (évite les
   * intervalles qui s'accumulent en arrière-plan). Si `consultation` n'a
   * pas de minuteur actif pour l'étape en cours, l'élément est vidé.
   */
  function mountStepTimer(el, consultation, { compact = false } = {}) {
    if (el.__stepTimerInterval) { clearInterval(el.__stepTimerInterval); el.__stepTimerInterval = null; }
    const remaining0 = stepTimerRemainingSeconds(consultation);
    if (remaining0 === null) {
      el.innerHTML = "";
      el.classList.remove("step-timer-warning", "step-timer-elapsed");
      return () => {};
    }
    el.classList.add("step-timer");
    if (compact) el.classList.add("step-timer-compact");

    function paint() {
      const remaining = stepTimerRemainingSeconds(consultation);
      if (remaining === null) return;
      el.innerHTML = `<span class="step-timer-ico" aria-hidden="true">⏱</span><span class="step-timer-value">${fmtDuration(remaining)}</span>`;
      el.classList.toggle("step-timer-warning", remaining > 0 && remaining <= 30);
      el.classList.toggle("step-timer-elapsed", remaining === 0);
      el.setAttribute("aria-label", remaining > 0 ? `Temps restant : ${fmtDuration(remaining)}` : "Temps écoulé");
    }
    paint();
    el.__stepTimerInterval = setInterval(paint, 1000);
    return () => { clearInterval(el.__stepTimerInterval); el.__stepTimerInterval = null; };
  }

  // -- Droit à l'effacement RGPD (§7) --------------------------------------
  // Appelle l'endpoint serveur avec le participant_id local, puis efface
  // aussi l'identité locale (localStorage) : le prochain accès génère un
  // nouveau participant_id, cohérent avec l'idée d'un "nouveau départ".
  async function eraseMyData(code, mode = "anonymize") {
    const participantId = getParticipantId();
    const res = await fetch(`/api/webinars/${encodeURIComponent(code)}/participants/erase`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant_id: participantId, mode }),
    });
    if (!res.ok) {
      throw new Error("Échec de la demande d'effacement.");
    }
    localStorage.removeItem("boussole_pid");
    localStorage.removeItem("boussole_name");
    return res.json();
  }

  // -- Brouillon participant (§7) ------------------------------------------
  // Sauvegarde légère de la saisie en cours dans un formulaire, pour ne pas
  // perdre une contribution en cas de rechargement accidentel (perte de
  // connexion, fermeture d'onglet par erreur, etc). Purement local
  // (localStorage) : aucune donnée envoyée au serveur tant que le
  // participant n'a pas cliqué sur "Envoyer"/"Proposer".
  const DRAFT_PREFIX = "boussole_draft_";

  function draftAutosave(fields, storageKey) {
    const key = DRAFT_PREFIX + storageKey;
    let debounceTimer;

    function save() {
      const data = {};
      let hasContent = false;
      for (const el of fields) {
        if (!el) continue;
        data[el.id] = el.value;
        if (el.value && el.value.trim()) hasContent = true;
      }
      if (hasContent) {
        localStorage.setItem(key, JSON.stringify(data));
      } else {
        localStorage.removeItem(key);
      }
    }

    function scheduleSave() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(save, 400);
    }

    function restore() {
      let raw;
      try {
        raw = localStorage.getItem(key);
      } catch (e) {
        return false;
      }
      if (!raw) return false;
      let data;
      try {
        data = JSON.parse(raw);
      } catch (e) {
        localStorage.removeItem(key);
        return false;
      }
      let restored = false;
      for (const el of fields) {
        if (!el || !(el.id in data)) continue;
        if (data[el.id]) {
          el.value = data[el.id];
          restored = true;
        }
      }
      return restored;
    }

    function clear() {
      clearTimeout(debounceTimer);
      localStorage.removeItem(key);
    }

    for (const el of fields) {
      if (!el) continue;
      el.addEventListener("input", scheduleSave);
    }

    return { restore, clear };
  }

  return {
    getParticipantId, getDisplayName, setDisplayName,
    getHostToken, setHostToken, clearHostToken,
    connect, renderDial, DIAL_LABELS, phaseToDialIndex,
    toast, escapeHtml, fmtPct, qs, qsa, categoryBadge, axisNationalTarget, mapEmbed, consultationStepper,
    axisProgress, projectPanel,
    stepTimerRemainingSeconds, fmtDuration, mountStepTimer,
    eraseMyData, draftAutosave,
  };
})();
