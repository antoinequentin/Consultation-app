/* ==========================================================================
   Page participant — tout le rendu est piloté par les messages "state" reçus
   du WebSocket. Pour ne pas faire perdre leur saisie en cours aux
   participants (ex: en train d'écrire une contribution) pendant qu'une
   diffusion arrive suite à l'action de quelqu'un d'autre, les écrans avec
   un champ de texte ("proposer un projet", "proposer une contribution")
   ne reconstruisent leur formulaire que lorsque la phase/étape change
   réellement ; seule la liste des résultats est rafraîchie à chaque mise
   à jour.
   ========================================================================== */

(() => {
  const code = document.body.dataset.code;
  const root = document.getElementById("phase-content");
  const titleEl = document.getElementById("phase-title");
  const headerDial = document.getElementById("header-dial");
  const presenceEl = document.getElementById("presence-count");
  const nameInput = document.getElementById("display-name-input");

  nameInput.value = Boussole.getDisplayName();
  let nameDebounce;
  nameInput.addEventListener("input", () => {
    clearTimeout(nameDebounce);
    nameDebounce = setTimeout(() => {
      const val = nameInput.value.trim();
      Boussole.setDisplayName(val);
      ws.send("join", { display_name: val || null });
    }, 600);
  });

  const ws = Boussole.connect(code, { role: "participant", name: Boussole.getDisplayName() });
  ws.on("state", onState);
  ws.on("error", (p) => Boussole.toast(p.message, "error"));
  ws.on("ack", (p) => Boussole.toast(p.message, "success"));
  ws.on("_connected", () => Boussole.toast("Connecté", "success"));
  ws.on("_disconnected", () => { presenceEl.textContent = "Reconnexion…"; });
  ws.on("_not_found", () => { root.innerHTML = `<div class="empty-state"><div class="ico">🧭</div><p>Ce webinaire n'est plus disponible.</p></div>`; });

  function onState(state) {
    presenceEl.textContent = `${state.participant_count} en ligne${state.host_online ? "" : " · animateur hors ligne"}`;
    Boussole.renderDial(headerDial, state.webinar.phase, state.consultation ? state.consultation.step : 0, { compact: true, showLabel: false });

    const phase = state.webinar.phase;
    if (phase === "lobby") return renderLobby(state);
    if (phase === "project_submission") return renderProjectSubmission(state);
    if (phase === "project_vote") return renderProjectVote(state);
    if (phase === "consultation") return renderConsultation(state);
    if (phase === "ended") return renderEnded(state);
  }

  // -- LOBBY ----------------------------------------------------------------
  function renderLobby(state) {
    titleEl.textContent = "En attente du début du webinaire";
    root.dataset.mode = "";
    root.innerHTML = `
      <div class="empty-state card">
        <div class="ico">🧭</div>
        <h2>Bienvenue !</h2>
        <p>L'animateur n'a pas encore démarré la session. Cette page se mettra à jour automatiquement dès que la consultation commencera — inutile de recharger.</p>
      </div>`;
  }

  // -- PROJECT_SUBMISSION -----------------------------------------------------
  function renderProjectSubmission(state) {
    titleEl.textContent = "Proposez un projet";
    const pf = state.project_phase;
    if (root.dataset.mode !== "project_submission") {
      root.dataset.mode = "project_submission";
      const allowed = state.webinar.allow_project_proposals;
      root.innerHTML = `
        ${allowed ? `
        <form id="project-form" class="card" style="margin-bottom:var(--sp-6);">
          <h2 style="margin-top:0;">Votre projet</h2>
          <div class="field">
            <label for="p-title">Titre</label>
            <input class="input" id="p-title" maxlength="200" placeholder="Ex. Végétalisation de la cour de l'école" required>
          </div>
          <div class="field">
            <label for="p-desc">Description</label>
            <textarea class="input" id="p-desc" rows="3" maxlength="4000" placeholder="En quelques phrases, en quoi consiste ce projet ?"></textarea>
          </div>
          <details>
            <summary class="text-sm" style="cursor:pointer;font-weight:600;">Ajouter une image (lien URL, optionnel)</summary>
            <div class="field" style="margin-top:var(--sp-3);">
              <input class="input" id="p-image" type="url" placeholder="https://…">
            </div>
          </details>
          <button class="btn btn-primary" type="submit" style="margin-top:var(--sp-2);">Proposer ce projet</button>
          <p class="text-xs text-faint" id="p-quota" style="margin-top:var(--sp-2);"></p>
        </form>` : `<div class="card" style="margin-bottom:var(--sp-6);"><p style="margin:0;">L'animateur a fermé la proposition de nouveaux projets. Vous pouvez consulter ceux déjà proposés ci-dessous.</p></div>`}
        <div class="section-head"><h2>Projets proposés <span class="badge badge-neutral" id="count-badge"></span></h2></div>
        <div id="dynamic-projects" class="project-grid"></div>
      `;
      if (allowed) {
        document.getElementById("project-form").addEventListener("submit", (e) => {
          e.preventDefault();
          const title = document.getElementById("p-title").value.trim();
          if (title.length < 3) { Boussole.toast("Le titre est trop court.", "error"); return; }
          ws.send("submit_project", {
            title,
            description: document.getElementById("p-desc").value.trim(),
            context: "",
            image_url: document.getElementById("p-image").value.trim() || null,
          });
          e.target.reset();
        });
      }
    }
    const quotaEl = document.getElementById("p-quota");
    if (quotaEl) {
      const used = state.you.my_projects_count || 0;
      quotaEl.textContent = `${used} / ${pf.max_projects_per_participant} projets proposés par vous.`;
    }
    document.getElementById("count-badge").textContent = pf.projects.length;
    renderProjectGrid(document.getElementById("dynamic-projects"), pf.projects, { votable: false });
  }

  // -- PROJECT_VOTE -------------------------------------------------------
  function renderProjectVote(state) {
    titleEl.textContent = "Votez pour le projet à étudier";
    root.dataset.mode = "";
    const pf = state.project_phase;
    root.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);">
        <p style="margin:0;">Choisissez le projet qui sera étudié en priorité pendant ce webinaire. <strong>${pf.total_votes}</strong> vote(s) exprimé(s) jusqu'ici — les résultats sont en direct.</p>
      </div>
      <div id="dynamic-projects" class="project-grid"></div>
    `;
    const sorted = [...pf.projects].sort((a, b) => (b.votes || 0) - (a.votes || 0));
    renderProjectGrid(document.getElementById("dynamic-projects"), sorted, { votable: true, myVote: state.you.my_project_vote, total: pf.total_votes });
  }

  function renderProjectGrid(container, projects, { votable, myVote, total }) {
    if (!projects.length) {
      container.innerHTML = `<div class="empty-state"><p>Aucun projet proposé pour le moment.</p></div>`;
      return;
    }
    container.innerHTML = projects.map((p, i) => {
      const pct = votable && total ? Math.round(100 * (p.votes || 0) / total) : null;
      const isLeading = votable && i === 0 && (p.votes || 0) > 0;
      const mine = myVote === p.id;
      return `
      <div class="project-card card-hover ${isLeading ? "is-leading" : ""}" data-project-id="${p.id}">
        ${isLeading ? `<span class="project-rank">En tête</span>` : ""}
        ${p.image_url ? `<img src="${Boussole.escapeHtml(p.image_url)}" alt="" style="width:100%;border-radius:8px;height:120px;object-fit:cover;" onerror="this.remove()">` : ""}
        <h3 style="margin:0;">${Boussole.escapeHtml(p.title)}</h3>
        ${p.description ? `<p class="text-sm" style="margin:0;">${Boussole.escapeHtml(p.description)}</p>` : ""}
        <p class="text-xs text-faint" style="margin:0;">${p.proposed_by_name ? "Proposé par " + Boussole.escapeHtml(p.proposed_by_name) : "Proposé anonymement"}${p.is_mine ? " · vous" : ""}</p>
        ${votable ? `
          <div>
            <div class="result-row" style="grid-template-columns:1fr auto;">
              <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:var(--brass);"></div></div>
              <div class="pct">${pct}%</div>
            </div>
            <span class="vote-count text-sm">${p.votes || 0} vote(s)</span>
          </div>
          <button class="btn ${mine ? "btn-brass" : "btn-outline"} btn-block vote-project-btn" data-id="${p.id}">${mine ? "✓ Votre choix" : "Voter pour ce projet"}</button>
        ` : ""}
      </div>`;
    }).join("");

    if (votable) {
      Boussole.qsa(".vote-project-btn", container).forEach((btn) => {
        btn.addEventListener("click", () => ws.send("vote_project", { project_id: parseInt(btn.dataset.id, 10) }));
      });
    }
  }

  // -- CONSULTATION ---------------------------------------------------------
  function renderConsultation(state) {
    const c = state.consultation;
    if (!c || !c.project) { root.innerHTML = `<div class="empty-state"><p>En préparation…</p></div>`; return; }
    titleEl.textContent = `${c.project.title}`;

    if (c.step === 3) return renderCotation(state);
    return renderPropositionStep(state);
  }

  const STEP_INTROS = {
    1: { label: "Impacts positifs", help: "Quels sont, selon vous, les impacts positifs de ce projet ?", placeholder: "Ex. Réduction de la facture énergétique pour les habitants…" },
    2: { label: "Impacts négatifs", help: "Quels points de vigilance ou impacts négatifs identifiez-vous ?", placeholder: "Ex. Nuisances pendant la phase de travaux…" },
    4: { label: "Pistes d'amélioration", help: "Quelles améliorations proposeriez-vous pour ce projet ?", placeholder: "Ex. Prévoir un espace de stationnement vélo…" },
  };

  function renderPropositionStep(state) {
    const c = state.consultation;
    const info = STEP_INTROS[c.step];
    const key = `prop|${c.step}|${c.axis ? c.axis.id : ""}`;

    if (root.dataset.mode !== key) {
      root.dataset.mode = key;
      root.innerHTML = `
        <div class="card" style="margin-bottom:var(--sp-5);">
          <span class="badge badge-blue">${c.axis_count > 1 ? `Axe ${c.axis_index + 1}/${c.axis_count} · ` : ""}${info.label}</span>
          ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
          <h2 style="margin:var(--sp-3) 0 var(--sp-2);">${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</h2>
          <p style="margin:0;">${info.help}</p>
        </div>
        <form id="prop-form" class="composer">
          <textarea class="input" id="prop-text" rows="2" maxlength="500" placeholder="${info.placeholder}" required></textarea>
          <button class="btn btn-primary" type="submit">Envoyer</button>
        </form>
        <p class="text-xs text-faint" id="prop-quota" style="margin:calc(-1 * var(--sp-3)) 0 var(--sp-4);"></p>
        <div class="section-head"><h2>Contributions <span class="badge badge-neutral" id="prop-count-badge"></span></h2></div>
        <div id="dynamic-props" class="prop-list"></div>
      `;
      document.getElementById("prop-form").addEventListener("submit", (e) => {
        e.preventDefault();
        const ta = document.getElementById("prop-text");
        const texte = ta.value.trim();
        if (texte.length < 2) return;
        ws.send("submit_proposition", { prop_type: c.proposition_type, texte });
        ta.value = "";
      });
      // Délégation : les boutons de vote sont recréés à chaque rafraîchissement
      // de la liste, mais ce conteneur (le formulaire shell) reste stable.
      root.addEventListener("click", onVoteClick);
    }

    const quotaEl = document.getElementById("prop-quota");
    if (quotaEl) quotaEl.textContent = `${state.you.my_propositions_count || 0} / ${state.webinar.max_propositions_per_participant ?? 5} contribution(s) envoyée(s) par vous pour cette étape.`;

    document.getElementById("prop-count-badge").textContent = c.propositions.length;
    renderPropList(document.getElementById("dynamic-props"), c.propositions, state.you.my_vote_map || {});
  }

  function onVoteClick(e) {
    const btn = e.target.closest(".vote-btn");
    if (!btn) return;
    const row = btn.closest("[data-prop-id]");
    if (!row) return;
    ws.send("vote_proposition", { proposition_id: parseInt(row.dataset.propId, 10), vote: btn.dataset.vote });
  }

  function renderPropList(container, propositions, myVotes) {
    if (!propositions.length) {
      container.innerHTML = `<div class="empty-state"><p>Aucune contribution pour l'instant — soyez le premier·e à en proposer une.</p></div>`;
      return;
    }
    container.innerHTML = propositions.map((p) => {
      const myVote = myVotes[p.id];
      const total = p.total_votes;
      const pctA = total ? (100 * p.nb_accord / total) : 0;
      const pctD = total ? (100 * p.nb_desaccord / total) : 0;
      const pctP = Math.max(0, 100 - pctA - pctD);
      return `
      <div class="prop-card ${p.is_mine ? "is-mine" : ""} ${p.status !== "approved" ? "is-pending" : ""}" data-prop-id="${p.id}">
        <div class="prop-text">${Boussole.escapeHtml(p.texte)}${p.is_mine ? ' <span class="badge badge-brass">Vous</span>' : ""}</div>
        ${p.status !== "approved" ? `<span class="badge badge-neutral">En attente de modération</span>` : `
        <div class="consensus-bar"><div class="seg-accord" style="width:${pctA}%"></div><div class="seg-desaccord" style="width:${pctD}%"></div><div class="seg-passer" style="width:${pctP}%"></div></div>
        <div class="vote-row">
          <button class="vote-btn ${myVote === "accord" ? "active accord" : ""}" data-vote="accord">👍 Accord <span class="count">${p.nb_accord}</span></button>
          <button class="vote-btn ${myVote === "desaccord" ? "active desaccord" : ""}" data-vote="desaccord">👎 Désaccord <span class="count">${p.nb_desaccord}</span></button>
          <button class="vote-btn ${myVote === "passer" ? "active passer" : ""}" data-vote="passer">⏭ Passer <span class="count">${p.nb_passer}</span></button>
        </div>`}
      </div>`;
    }).join("");
  }

  // -- Cotation (étape 3) ---------------------------------------------------
  const COTATIONS = [
    { key: "FAVORABLE", ico: "👍", label: "Favorable", cls: "favorable" },
    { key: "NEUTRE", ico: "✋", label: "Neutre", cls: "neutre" },
    { key: "DEFAVORABLE", ico: "👎", label: "Défavorable", cls: "defavorable" },
  ];

  function renderCotation(state) {
    const c = state.consultation;
    root.dataset.mode = "";
    const mine = state.you.my_cotation;
    const cot = c.cotation || { counts: {}, total: 0, percentages: {} };
    root.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);">
        <span class="badge badge-blue">${c.axis_count > 1 ? `Axe ${c.axis_index + 1}/${c.axis_count} · ` : ""}Vote</span>
        ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
        <h2 style="margin:var(--sp-3) 0 var(--sp-2);">${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</h2>
        <p style="margin:0;">Quel est votre avis global sur ce point ?</p>
      </div>
      <div class="cotation-grid" id="cotation-grid">
        ${COTATIONS.map((cc) => `
          <button class="cotation-btn ${cc.cls} ${mine === cc.key ? "active" : ""}" data-key="${cc.key}">
            <span class="ico">${cc.ico}</span>${cc.label}
          </button>`).join("")}
      </div>
      <div class="card" style="margin-top:var(--sp-5);">
        <div class="section-head"><h3 style="margin:0;">Résultats en direct</h3><span class="badge badge-live"><span class="badge-dot"></span>${cot.total} réponse(s)</span></div>
        ${COTATIONS.map((cc) => `
          <div class="result-row">
            <div class="label">${cc.ico} ${cc.label}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${cot.percentages[cc.key] || 0}%;background:${cc.key === "FAVORABLE" ? "var(--green)" : cc.key === "DEFAVORABLE" ? "var(--red)" : "var(--neutral-pass)"};"></div></div>
            <div class="pct">${Boussole.fmtPct(cot.percentages[cc.key] || 0)}</div>
          </div>`).join("")}
      </div>
    `;
    Boussole.qsa(".cotation-btn", root).forEach((btn) => {
      btn.addEventListener("click", () => ws.send("submit_cotation", { reponse: btn.dataset.key }));
    });
  }

  // -- ENDED ------------------------------------------------------------------
  function renderEnded(state) {
    titleEl.textContent = "Webinaire terminé";
    root.dataset.mode = "";
    root.innerHTML = `
      <div class="empty-state card">
        <div class="ico">✅</div>
        <h2>Merci pour votre participation !</h2>
        <p>La consultation est terminée. ${state.consultation && state.consultation.project ? `Le projet étudié était : <strong>${Boussole.escapeHtml(state.consultation.project.title)}</strong>.` : ""}</p>
      </div>`;
  }
})();
