/* ==========================================================================
   Console animateur — authentification puis pilotage temps réel complet.
   ========================================================================== */

(() => {
  const code = document.body.dataset.code;
  const loginScreen = document.getElementById("login-screen");
  const shell = document.getElementById("host-shell");
  const phaseLabel = document.getElementById("host-phase-label");
  const presenceBadge = document.getElementById("presence-badge");

  document.getElementById("open-projector").href = `/w/${code}/projector`;
  document.getElementById("open-participant").href = `/w/${code}`;

  let ws = null;
  let state = null;
  let stopOverviewTimer = () => {};
  let stopConsultationTimer = () => {};

  // -- Authentification -----------------------------------------------------

  function tryConnectWithToken(token) {
    ws = Boussole.connect(code, { role: "host", token });
    ws.on("_auth_error", () => {
      Boussole.clearHostToken(code);
      showLogin("Session expirée, merci de vous reconnecter.");
    });
    ws.on("_not_found", () => { document.body.innerHTML = "<p style='padding:2rem;'>Webinaire introuvable.</p>"; });
    ws.on("state", (s) => { showShell(); onState(s); });
    ws.on("error", (p) => Boussole.toast(p.message, "error"));
    ws.on("ack", (p) => Boussole.toast(p.message, "success"));
    ws.on("_disconnected", () => presenceBadge.innerHTML = `<span class="badge-dot"></span> Reconnexion…`);
  }

  function showLogin(message) {
    shell.style.display = "none";
    loginScreen.style.display = "flex";
    if (message) {
      const err = document.getElementById("login-error");
      err.textContent = message;
      err.style.display = "block";
    }
  }
  function showShell() {
    loginScreen.style.display = "none";
    shell.style.display = "grid";
  }

  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = document.getElementById("login-password").value;
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await fetch(`/api/webinars/${code}/host/login`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }),
      });
      if (!res.ok) throw new Error("Mot de passe incorrect.");
      const data = await res.json();
      Boussole.setHostToken(code, data.token);
      tryConnectWithToken(data.token);
    } catch (err) {
      document.getElementById("login-error").textContent = err.message;
      document.getElementById("login-error").style.display = "block";
      btn.disabled = false;
    }
  });

  document.getElementById("logout-btn").addEventListener("click", () => {
    Boussole.clearHostToken(code);
    location.reload();
  });

  const existingToken = Boussole.getHostToken(code);
  if (existingToken) tryConnectWithToken(existingToken);

  // -- Navigation entre panneaux ---------------------------------------------
  // §5.3 : la nav reflète où en est le webinaire — badge "en cours" sur
  // l'onglet correspondant à la phase active, et onglets non pertinents
  // grisés/désactivés (ex: "Consultation" tant qu'aucun projet n'est
  // sélectionné, "Modération" hors phase de consultation). "Vue d'ensemble"
  // et "Paramètres" restent toujours accessibles : ce sont des panneaux de
  // pilotage/config, jamais liés à une phase précise.

  function selectPanel(panelName) {
    Boussole.qsa("#host-nav button").forEach((b) => b.classList.toggle("active", b.dataset.panel === panelName));
    Boussole.qsa(".host-panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${panelName}`));
  }

  Boussole.qsa("#host-nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      selectPanel(btn.dataset.panel);
    });
  });

  function currentActivePanel() {
    const active = Boussole.qs("#host-nav button.active");
    return active ? active.dataset.panel : "overview";
  }

  // Détermine, pour l'état courant, quel onglet correspond à la phase en
  // cours (badge "en cours") et quels onglets n'ont rien à montrer pour le
  // moment (désactivés). Centralisé ici plutôt que dispersé dans chaque
  // fonction de rendu, pour garder une seule source de vérité sur "où en
  // est le webinaire".
  function navRelevance(s) {
    const phase = s.webinar.phase;
    const hasConsultation = !!(s.consultation && s.consultation.project);
    const hasPropositionsStep = !!(s.consultation && s.consultation.propositions);
    const pendingCount = hasPropositionsStep
      ? s.consultation.propositions.filter((p) => p.status === "pending").length
      : 0;

    return {
      overview: { current: false, disabled: false },
      projects: {
        current: phase === "project_submission" || phase === "project_vote",
        disabled: phase === "lobby",
      },
      consultation: {
        current: phase === "consultation",
        disabled: !hasConsultation,
      },
      moderation: {
        current: pendingCount > 0,
        disabled: !hasPropositionsStep,
      },
      settings: { current: false, disabled: false },
    };
  }

  function updateNav(s) {
    const relevance = navRelevance(s);
    let activePanel = currentActivePanel();
    let switched = false;

    Boussole.qsa("#host-nav button").forEach((btn) => {
      const panel = btn.dataset.panel;
      const r = relevance[panel] || { current: false, disabled: false };
      btn.disabled = r.disabled;
      btn.classList.toggle("nav-current", r.current);
      const badgeEl = btn.querySelector("[data-badge]");
      if (badgeEl) badgeEl.textContent = r.current ? "En cours" : "";
      // Si l'onglet actuellement affiché vient de devenir non pertinent
      // (ex: l'animateur a réinitialisé le webinaire pendant qu'on était
      // sur "Consultation"), on retombe sur "Vue d'ensemble" plutôt que de
      // laisser un panneau grisé et vide à l'écran.
      if (panel === activePanel && r.disabled) switched = true;
    });

    if (switched) selectPanel("overview");
  }

  // -- QR / export -----------------------------------------------------------

  const qrModal = document.getElementById("qr-modal");
  document.getElementById("show-qr-btn").addEventListener("click", () => {
    const url = `${location.origin}/w/${code}`;
    document.getElementById("qr-img").src = `/api/webinars/${code}/qrcode.png`;
    document.getElementById("qr-url").textContent = url;
    qrModal.showModal();
  });
  document.getElementById("close-qr").addEventListener("click", () => qrModal.close());

  document.getElementById("export-btn").addEventListener("click", (e) => {
    e.preventDefault();
    const token = Boussole.getHostToken(code);
    window.open(`/api/webinars/${code}/export.zip?token=${encodeURIComponent(token)}`, "_blank");
  });

  document.getElementById("export-pdf-btn").addEventListener("click", (e) => {
    e.preventDefault();
    const token = Boussole.getHostToken(code);
    window.open(`/api/webinars/${code}/export.pdf?token=${encodeURIComponent(token)}`, "_blank");
  });

  function hostAction(action, extra = {}) {
    ws.send("host_action", { action, ...extra });
  }
  window.__hostAction = hostAction; // utilisé par les attributs onclick générés dynamiquement

  // -- Réception d'état -------------------------------------------------------

  function onState(s) {
    state = s;
    phaseLabel.textContent = s.webinar.phase_label;
    presenceBadge.innerHTML = `<span class="badge-dot"></span> ${s.participant_count} en ligne`;
    updateNav(s);
    renderOverview(s);
    renderProjects(s);
    renderConsultation(s);
    renderModeration(s);
    renderSettings(s);
    renderAside(s);
  }


  // -- VUE D'ENSEMBLE -----------------------------------------------------

  function renderOverview(s) {
    const el = document.getElementById("panel-overview");
    const phase = s.webinar.phase;
    let actionBlock = "";

    if (phase === "lobby") {
      actionBlock = `
        <div class="card">
          <h2 style="margin-top:0;">Démarrer le webinaire</h2>
          <p>Les participants peuvent déjà rejoindre avec le code <strong class="mono">${code}</strong>, mais ils attendent que vous ouvriez la première étape.</p>
          <button class="btn btn-primary btn-lg" onclick="__hostAction('start_project_submission')">Ouvrir la proposition de projets →</button>
        </div>`;
    } else if (phase === "project_submission") {
      const pf = s.project_phase;
      actionBlock = `
        <div class="card">
          <h2 style="margin-top:0;">Proposition de projets en cours</h2>
          <p><strong>${pf.projects.length}</strong> projet(s) proposé(s) jusqu'ici.</p>
          <button class="btn btn-primary btn-lg" ${pf.projects.length ? "" : "disabled"} onclick="__hostAction('close_submission_open_vote')">Clore les propositions et ouvrir le vote →</button>
          ${!pf.projects.length ? '<p class="text-xs text-faint">En attente d\'au moins un projet proposé.</p>' : ""}
        </div>`;
    } else if (phase === "project_vote") {
      const pf = s.project_phase;
      const sorted = [...pf.projects].sort((a, b) => (b.votes || 0) - (a.votes || 0));
      const leader = sorted[0];
      actionBlock = `
        <div class="card">
          <h2 style="margin-top:0;">Vote du projet en cours</h2>
          <p><strong>${pf.total_votes}</strong> vote(s) exprimé(s) sur ${pf.projects.length} projet(s).</p>
          ${leader ? `<p>Projet en tête : <strong>${Boussole.escapeHtml(leader.title)}</strong> (${leader.votes || 0} vote(s))</p>` : ""}
          <div class="host-actions-row">
            <button class="btn btn-primary btn-lg" ${leader ? "" : "disabled"} onclick="__hostAction('select_project', {project_id:${leader ? leader.id : "null"}})">Retenir ce projet et démarrer →</button>
            <button class="btn btn-outline" onclick="__hostAction('reopen_submission')">← Revenir aux propositions</button>
          </div>
        </div>`;
    } else if (phase === "consultation") {
      const c = s.consultation;
      actionBlock = `
        <div class="card">
          <h2 style="margin-top:0;">${Boussole.escapeHtml(c.project.title)}</h2>
          <p class="text-sm text-soft">${c.axis_count > 1 ? `Axe ${c.axis_index + 1} / ${c.axis_count} — ` : ""}${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</p>
          ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
          ${Boussole.consultationStepper(c)}
          <div class="host-actions-row">
            <button class="btn btn-danger btn-sm" style="margin-left:auto;" onclick="if(confirm('Terminer le webinaire ? Les résultats finaux seront affichés à tous.')) __hostAction('end_consultation')">Terminer le webinaire</button>
          </div>
        </div>`;
    } else if (phase === "ended") {
      actionBlock = `
        <div class="card">
          <h2 style="margin-top:0;">Webinaire terminé</h2>
          <p>Les participants voient désormais l'écran de fin. Vous pouvez exporter les données ou réinitialiser pour une nouvelle session.</p>
          <button class="btn btn-outline" onclick="if(confirm('Réinitialiser le webinaire ? Les données collectées sont conservées, mais la session repart de l\\'accueil.')) __hostAction('restart_webinar')">Réinitialiser le webinaire</button>
        </div>`;
    }

    el.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);background:var(--ink);color:#fff;display:flex;align-items:center;gap:var(--sp-5);">
        <div style="width:64px;height:64px;flex-shrink:0;" id="overview-dial"></div>
        <div>
          <p class="text-xs" style="margin:0;opacity:.7;text-transform:uppercase;letter-spacing:.06em;">Phase actuelle</p>
          <h2 style="margin:.2rem 0 0;color:#fff;">${s.webinar.phase_label}</h2>
        </div>
      </div>
      ${actionBlock}
    `;
    Boussole.renderDial(document.getElementById("overview-dial"), s.webinar.phase, s.consultation ? s.consultation.step : 0, { compact: true, showLabel: false });
    stopOverviewTimer();
    const overviewTimerEl = Boussole.qs("[data-step-timer-host]", el);
    stopOverviewTimer = overviewTimerEl ? Boussole.mountStepTimer(overviewTimerEl, s.consultation) : () => {};
  }

  // -- PROJETS ------------------------------------------------------------

  function renderProjects(s) {
    const el = document.getElementById("panel-projects");
    const pf = s.project_phase;
    if (!pf) {
      el.innerHTML = `<div class="empty-state card"><p>La phase de projets n'est pas (encore) active.</p>${s.consultation && s.consultation.project ? `<p>Projet actuellement étudié : <strong>${Boussole.escapeHtml(s.consultation.project.title)}</strong></p>` : ""}</div>`;
      return;
    }
    const showVotes = s.webinar.phase === "project_vote";
    const sorted = showVotes ? [...pf.projects].sort((a, b) => (b.votes || 0) - (a.votes || 0)) : pf.projects;
    el.innerHTML = `
      <div class="section-head"><h2>Projets proposés (${pf.projects.length})</h2></div>
      ${sorted.map((p) => `
        <div class="mod-row" style="align-items:flex-start;">
          <div class="txt">
            <strong>${Boussole.escapeHtml(p.title)}</strong>${showVotes ? ` <span class="nums">${p.votes || 0} vote(s)</span>` : ""}<br>
            <span class="text-sm text-soft">${Boussole.escapeHtml(p.description || "")}</span><br>
            <span class="text-xs text-faint">${p.proposed_by_name ? "Par " + Boussole.escapeHtml(p.proposed_by_name) : "Anonyme"}</span>
          </div>
          <div style="display:flex;gap:var(--sp-2);">
            <button class="btn btn-outline btn-sm" onclick="__hostAction('select_project',{project_id:${p.id}})">Sélectionner</button>
            <button class="btn btn-ghost btn-sm" style="color:var(--red);" onclick="if(confirm('Supprimer ce projet proposé ?')) __hostAction('delete_project',{project_id:${p.id}})">Supprimer</button>
          </div>
        </div>`).join("") || '<p class="text-soft">Aucun projet proposé pour le moment.</p>'}

      <div class="section-head" style="margin-top:var(--sp-5);"><h2>Projet type — réutiliser un projet existant</h2></div>
      <div class="card">
        <p class="text-sm text-soft" style="margin-top:0;">
          Vous animez régulièrement des ateliers ? Récupérez un projet déjà utilisé dans un autre webinaire (dont vous connaissez le code et le mot de passe) sans ressaisir ses informations.
        </p>
        <div style="display:flex;gap:var(--sp-2);flex-wrap:wrap;align-items:flex-end;">
          <div class="field" style="flex:1;min-width:120px;">
            <label for="dup-src-code">Code du webinaire source</label>
            <input class="input" id="dup-src-code" maxlength="12" placeholder="Ex. AB12CD" style="text-transform:uppercase;">
          </div>
          <div class="field" style="flex:1;min-width:120px;">
            <label for="dup-src-pass">Mot de passe animateur de ce webinaire</label>
            <input class="input" id="dup-src-pass" type="password" placeholder="••••••••">
          </div>
          <button class="btn btn-outline" id="dup-load-btn" type="button">Charger les projets</button>
        </div>
        <p class="text-xs" id="dup-feedback" style="margin:var(--sp-2) 0 0;"></p>
        <div id="dup-results" style="margin-top:var(--sp-3);"></div>
      </div>
    `;
    if (!el.__dupWired) {
      el.__dupWired = true;
      el.addEventListener("click", async (e) => {
        if (e.target.id === "dup-load-btn") {
          await loadDuplicableProjects();
        } else if (e.target.closest(".dup-btn")) {
          const btn = e.target.closest(".dup-btn");
          await duplicateProjectHere(parseInt(btn.dataset.id, 10), btn.dataset.title);
        }
      });
    }
  }

  async function loadDuplicableProjects() {
    const feedback = document.getElementById("dup-feedback");
    const resultsEl = document.getElementById("dup-results");
    const srcCode = document.getElementById("dup-src-code").value.trim().toUpperCase();
    const srcPass = document.getElementById("dup-src-pass").value;
    if (!srcCode || !srcPass) {
      feedback.textContent = "Renseignez le code et le mot de passe du webinaire source.";
      return;
    }
    feedback.textContent = "Connexion…";
    resultsEl.innerHTML = "";
    try {
      const loginRes = await fetch(`/api/webinars/${encodeURIComponent(srcCode)}/host/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: srcPass }),
      });
      if (!loginRes.ok) {
        feedback.textContent = "Code ou mot de passe incorrect pour ce webinaire.";
        return;
      }
      const { token: srcToken } = await loginRes.json();
      const listRes = await fetch(`/api/webinars/${encodeURIComponent(srcCode)}/projects/duplicable?token=${encodeURIComponent(srcToken)}`);
      if (!listRes.ok) {
        feedback.textContent = "Impossible de récupérer les projets de ce webinaire.";
        return;
      }
      const data = await listRes.json();
      if (!data.projects.length) {
        feedback.textContent = `Aucun projet dans « ${data.webinar.title} ».`;
        return;
      }
      feedback.textContent = `${data.projects.length} projet(s) trouvé(s) dans « ${data.webinar.title} ».`;
      resultsEl.innerHTML = data.projects.map((p) => `
        <div class="mod-row">
          <div class="txt">
            <strong>${Boussole.escapeHtml(p.title)}</strong><br>
            <span class="text-sm text-soft">${Boussole.escapeHtml(p.description || "")}</span>
          </div>
          <button class="btn btn-primary btn-sm dup-btn" data-id="${p.id}" data-title="${Boussole.escapeHtml(p.title)}">Dupliquer ici</button>
        </div>`).join("");
    } catch (err) {
      feedback.textContent = "Erreur de connexion, veuillez réessayer.";
    }
  }

  async function duplicateProjectHere(sourceProjectId, title) {
    const feedback = document.getElementById("dup-feedback");
    const token = Boussole.getHostToken(code);
    feedback.textContent = "Duplication en cours…";
    try {
      const res = await fetch(`/api/webinars/${encodeURIComponent(code)}/projects/duplicate?token=${encodeURIComponent(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_project_id: sourceProjectId }),
      });
      if (!res.ok) {
        feedback.textContent = "Échec de la duplication.";
        return;
      }
      Boussole.toast(`« ${title} » ajouté à ce webinaire.`, "success");
      feedback.textContent = "";
      // Le projet dupliqué apparaîtra dans la liste au prochain état diffusé
      // (broadcast déclenché côté serveur par la prochaine action, ou état
      // déjà à jour si une diffusion a eu lieu entre-temps) ; pas besoin de
      // rafraîchir manuellement ici.
    } catch (err) {
      feedback.textContent = "Erreur de connexion, veuillez réessayer.";
    }
  }

  // -- CONSULTATION ---------------------------------------------------------

  function renderConsultation(s) {
    const el = document.getElementById("panel-consultation");
    const c = s.consultation;
    if (!c || !c.project) {
      el.innerHTML = `<div class="empty-state card"><p>Aucun projet n'est encore sélectionné pour la consultation.</p></div>`;
      return;
    }
    let body = "";
    if (c.step === 3) {
      const cot = c.cotation || { counts: {}, total: 0, percentages: {} };
      body = `
        <div class="card">
          <div class="section-head"><h3 style="margin:0;">Cotation en direct</h3><span class="badge badge-live"><span class="badge-dot"></span>${cot.total} réponse(s)</span></div>
          ${["FAVORABLE", "NEUTRE", "DEFAVORABLE"].map((k) => `
            <div class="result-row">
              <div class="label">${k}</div>
              <div class="bar-track"><div class="bar-fill" style="width:${cot.percentages[k] || 0}%;background:${k === "FAVORABLE" ? "var(--cotation-favorable)" : k === "DEFAVORABLE" ? "var(--cotation-defavorable)" : "var(--cotation-neutre)"};"></div></div>
              <div class="pct">${Boussole.fmtPct(cot.percentages[k] || 0)} · ${cot.counts[k] || 0}</div>
            </div>`).join("")}
          <button class="btn btn-outline btn-sm" onclick="if(confirm('Réinitialiser tous les votes de cotation pour cet axe ?')) __hostAction('reset_cotation')">Réinitialiser les votes</button>
        </div>`;
    } else {
      const props = c.propositions || [];
      body = `
        <div class="card">
          <div class="section-head">
            <h3 style="margin:0;">Contributions (${props.length})</h3>
            <button class="btn btn-outline btn-sm" onclick="if(confirm('Supprimer toutes les contributions de cette étape ?')) __hostAction('reset_all_propositions',{prop_type:'${c.proposition_type}'})">Tout réinitialiser</button>
          </div>
          ${props.map((p) => `
            <div class="mod-row">
              <div class="txt">${Boussole.escapeHtml(p.texte)}</div>
              <div class="nums">👍${p.nb_accord} 👎${p.nb_desaccord} ⏭${p.nb_passer} · ${p.consensus_pct}% accord</div>
              <button class="btn btn-outline btn-sm" onclick="if(confirm('Réinitialiser les votes de cette contribution ? Le texte sera conservé.')) __hostAction('moderate_proposition',{proposition_id:${p.id}})" title="Remet les compteurs à zéro, conserve le texte">🔄 Modérer</button>
              <button class="btn btn-ghost btn-sm" style="color:var(--red);" onclick="if(confirm('Supprimer cette contribution ?')) __hostAction('delete_proposition',{proposition_id:${p.id}})">Suppr.</button>
            </div>`).join("") || '<p class="text-soft">Aucune contribution pour le moment.</p>'}
        </div>`;
    }
    el.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);">
        <h2 style="margin-top:0;">${Boussole.escapeHtml(c.project.title)}</h2>
        ${c.axis_count > 1 ? `<p class="text-sm text-soft">Axe ${c.axis_index + 1} / ${c.axis_count}</p>` : ""}
        ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
        <p style="margin:var(--sp-2) 0 0;">${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</p>
        ${Boussole.consultationStepper(c)}
      </div>
      ${body}
      <div class="card" style="margin-top:var(--sp-5);">
        <h3 style="margin-top:0;">Ajouter un axe de discussion</h3>
        <form id="add-axis-form" style="display:flex;gap:var(--sp-2);">
          <input class="input" id="axis-text" placeholder="Ex. Quel est l'impact sur la mobilité du quartier ?" required>
          <button class="btn btn-outline" type="submit">Ajouter</button>
        </form>
      </div>
    `;
    const form = document.getElementById("add-axis-form");
    if (form) form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = document.getElementById("axis-text");
      hostAction("add_axis", { texte: input.value.trim() });
      input.value = "";
    });
    stopConsultationTimer();
    const consultationTimerEl = Boussole.qs("[data-step-timer-host]", el);
    stopConsultationTimer = consultationTimerEl ? Boussole.mountStepTimer(consultationTimerEl, c) : () => {};
  }

  // -- MODÉRATION -----------------------------------------------------------

  function renderModeration(s) {
    const el = document.getElementById("panel-moderation");
    const c = s.consultation;
    if (!c || !c.propositions) {
      el.innerHTML = `<div class="empty-state card"><p>Rien à modérer pour le moment.</p></div>`;
      return;
    }
    const pending = c.propositions.filter((p) => p.status === "pending");
    el.innerHTML = `
      ${!s.webinar.moderation_enabled ? `<div class="card" style="margin-bottom:var(--sp-5);"><p style="margin:0;">La modération préalable est désactivée : les contributions sont visibles immédiatement. Activez-la dans <strong>Paramètres</strong> si vous souhaitez les valider avant publication.</p></div>` : ""}
      <div class="section-head"><h2>En attente de validation (${pending.length})</h2></div>
      ${pending.map((p) => `
        <div class="mod-row">
          <div class="txt">${Boussole.escapeHtml(p.texte)}</div>
          <button class="btn btn-success btn-sm" onclick="__hostAction('approve_proposition',{proposition_id:${p.id}})">Valider</button>
          <button class="btn btn-danger btn-sm" onclick="__hostAction('reject_proposition',{proposition_id:${p.id}})">Rejeter</button>
        </div>`).join("") || '<p class="text-soft">Aucune contribution en attente.</p>'}
    `;
  }

  // -- PARAMÈTRES -------------------------------------------------------------

  const STEP_DURATION_FIELDS = [
    { key: "positifs", label: "Impacts positifs" },
    { key: "negatifs", label: "Impacts négatifs" },
    { key: "vote", label: "Vote" },
    { key: "ameliorations", label: "Améliorations" },
  ];

  function renderSettings(s) {
    const el = document.getElementById("panel-settings");
    const durations = (s.consultation && s.consultation.step_durations) || {};
    el.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);">
        <div class="switch-row">
          <div><strong>Modération des contributions</strong><div class="hint">Les nouvelles propositions attendent votre validation avant d'être visibles de tous.</div></div>
          <label class="switch"><input type="checkbox" id="set-moderation" ${s.webinar.moderation_enabled ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <div><strong>Proposition de projets ouverte</strong><div class="hint">Autoriser les participants à proposer de nouveaux projets.</div></div>
          <label class="switch"><input type="checkbox" id="set-allow-projects" ${s.webinar.allow_project_proposals ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
      <div class="card" style="margin-bottom:var(--sp-5);">
        <h3 style="margin-top:0;">Minuteur par étape</h3>
        <p class="text-sm text-soft">Réglez une durée (en minutes) pour chaque étape de la consultation. Le compte à rebours est visible de tous (animateur, participants, écran de projection) et s'affiche en rouge dans les 30 dernières secondes. Laissez vide pour désactiver le minuteur sur une étape.</p>
        <form id="durations-form" class="duration-grid">
          ${STEP_DURATION_FIELDS.map((f) => `
            <div class="field" style="margin-bottom:0;">
              <label for="dur-${f.key}">${f.label}</label>
              <input class="input" id="dur-${f.key}" type="number" min="1" max="120" step="0.5" placeholder="Illimité" value="${durations[f.key] ? (durations[f.key] / 60) : ""}">
            </div>`).join("")}
          <button class="btn btn-outline" type="submit" style="grid-column:1/-1;">Appliquer les durées</button>
        </form>
      </div>
      <div class="card" style="border-color:var(--red);">
        <h3 style="margin-top:0;color:var(--red-700);">Zone sensible</h3>
        <p>Réinitialiser ramène le webinaire à l'écran d'accueil. Les données déjà collectées (projets, contributions, votes) sont conservées et restent exportables.</p>
        <button class="btn btn-danger" onclick="if(confirm('Réinitialiser le webinaire ?')) __hostAction('restart_webinar')">Réinitialiser le webinaire</button>
      </div>
    `;
    document.getElementById("set-moderation").addEventListener("change", (e) => hostAction("set_moderation", { enabled: e.target.checked }));
    document.getElementById("set-allow-projects").addEventListener("change", (e) => hostAction("set_allow_project_proposals", { enabled: e.target.checked }));
    document.getElementById("durations-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const payload = {};
      STEP_DURATION_FIELDS.forEach((f) => {
        const raw = document.getElementById(`dur-${f.key}`).value.trim();
        payload[f.key] = raw === "" ? null : Math.round(parseFloat(raw) * 60);
      });
      hostAction("set_step_durations", payload);
    });
  }

  // -- Colonne latérale (stats) ----------------------------------------------

  function renderAside(s) {
    const el = document.getElementById("aside-stats");
    const tiles = [["Participants en ligne", s.participant_count]];
    if (s.project_phase) tiles.push(["Projets proposés", s.project_phase.projects.length], ["Votes de projet", s.project_phase.total_votes]);
    if (s.consultation && s.consultation.propositions) tiles.push(["Contributions (étape)", s.consultation.propositions.length]);
    if (s.consultation && s.consultation.cotation) tiles.push(["Réponses de cotation", s.consultation.cotation.total]);
    el.innerHTML = tiles.map(([k, v]) => `<div class="stat-tile"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
  }
})();
