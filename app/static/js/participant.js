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
  const mainEl = document.getElementById("app-main");
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

  // -- Droit à l'effacement RGPD (§7) --------------------------------------
  const privacyLink = document.getElementById("privacy-link");
  if (privacyLink) {
    privacyLink.addEventListener("click", openPrivacyDialog);
  }

  function openPrivacyDialog() {
    const overlay = document.createElement("div");
    overlay.className = "privacy-dialog-overlay";
    overlay.innerHTML = `
      <div class="privacy-dialog card" role="dialog" aria-modal="true" aria-labelledby="privacy-dialog-title">
        <h2 id="privacy-dialog-title" style="font-family:var(--font-display);font-weight:700;font-size:var(--fs-md);margin:0 0 var(--sp-2);">Gérer mes données</h2>
        <p class="text-sm">
          Votre participation est identifiée uniquement par un identifiant technique
          stocké dans votre navigateur, jamais par votre identité réelle. Vous pouvez
          à tout moment demander l'une des actions suivantes :
        </p>
        <ul class="text-sm privacy-dialog-options">
          <li>
            <strong>Anonymiser mes contributions</strong> — vos impacts, votes et
            cotations restent visibles pour le groupe (ils gardent leur valeur pour
            la restitution collective), mais ne sont plus rattachés à vous.
          </li>
          <li>
            <strong>Tout effacer</strong> — vos contributions, votes et cotations
            sont définitivement supprimés, y compris leur contenu.
          </li>
        </ul>
        <p class="text-xs text-faint">
          Dans les deux cas, un projet que vous auriez proposé reste visible pour le
          groupe (il a pu faire l'objet de toute une consultation), simplement sans
          mention de votre nom.
        </p>
        <div class="privacy-dialog-actions" style="display:flex;gap:var(--sp-2);flex-wrap:wrap;margin-top:var(--sp-3);">
          <button type="button" class="btn btn-outline btn-sm" data-mode="anonymize">Anonymiser mes contributions</button>
          <button type="button" class="btn btn-danger btn-sm" data-mode="erase">Tout effacer</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="cancel">Annuler</button>
        </div>
        <p class="text-xs" id="privacy-dialog-feedback" style="margin-top:var(--sp-2);"></p>
      </div>
    `;
    document.body.appendChild(overlay);

    const feedback = overlay.querySelector("#privacy-dialog-feedback");
    overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelectorAll("[data-mode]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const mode = btn.dataset.mode;
        const confirmMsg = mode === "erase"
          ? "Confirmer la suppression définitive de toutes vos contributions ? Cette action est irréversible."
          : "Confirmer l'anonymisation de vos contributions ?";
        if (!window.confirm(confirmMsg)) return;
        overlay.querySelectorAll("button").forEach((b) => (b.disabled = true));
        feedback.textContent = "Traitement en cours…";
        try {
          await Boussole.eraseMyData(code, mode);
          feedback.textContent = "Fait. Vous pouvez fermer cette fenêtre ; la page va se recharger.";
          setTimeout(() => window.location.reload(), 1500);
        } catch (err) {
          feedback.textContent = "Une erreur est survenue, veuillez réessayer.";
          overlay.querySelectorAll("button").forEach((b) => (b.disabled = false));
        }
      });
    });
  }

  let stopStepTimer = () => {};

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
    mainEl.classList.toggle("is-wide", phase === "consultation");
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
    root.dataset.shell = "";
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
    root.dataset.shell = "";
    if (root.dataset.mode !== "project_submission") {
      root.dataset.mode = "project_submission";
      const allowed = state.webinar.allow_project_proposals;
      root.innerHTML = `
        ${allowed ? `
        <form id="project-form" class="card" style="margin-bottom:var(--sp-6);">
          <h2 style="margin-top:0;">Votre projet</h2>
          <p class="text-sm text-soft" style="margin-top:calc(-1 * var(--sp-2));">Nous vous encourageons à proposer un projet réel dont vous avez la gestion — vous repartirez avec des retours concrets de l'intelligence collective.</p>
          <div class="field">
            <label for="p-title">Titre</label>
            <input class="input" id="p-title" maxlength="200" placeholder="Ex. Végétalisation de la cour de l'école" required>
          </div>
          <div class="field">
            <label for="p-desc">Description</label>
            <textarea class="input" id="p-desc" rows="3" maxlength="4000" placeholder="En quelques phrases, en quoi consiste ce projet ?"></textarea>
          </div>
          <div class="field">
            <label for="p-image-file">Image du projet (optionnel)</label>
            <input class="input" id="p-image-file" type="file" accept="image/png,image/jpeg,image/webp,image/gif">
            <div class="hint">Formats acceptés : JPG, PNG, WEBP, GIF — 8 Mo maximum. Vous pouvez aussi <button type="button" id="p-image-toggle-url" class="link-inline" style="background:none;border:none;padding:0;text-decoration:underline;cursor:pointer;color:inherit;font:inherit;">coller un lien d'image</button> à la place.</div>
            <input class="input" id="p-image" type="url" placeholder="https://…" style="display:none;margin-top:var(--sp-2);">
            <p class="text-xs" id="p-image-status" style="margin:var(--sp-1) 0 0;"></p>
            <img id="p-image-preview" alt="" style="display:none;max-width:100%;max-height:160px;border-radius:8px;margin-top:var(--sp-2);object-fit:cover;">
          </div>
          <div class="field">
            <label for="p-map">Lien carte (optionnel)</label>
            <input class="input" id="p-map" type="url" placeholder="https://cartes.gouv.fr/…">
            <div class="hint">Centrez la carte sur le projet sur <a href="https://cartes.gouv.fr/explorer-les-cartes/" target="_blank" rel="noopener">cartes.gouv.fr</a>, puis collez le lien de la page ici.</div>
          </div>
          <details class="field-group">
            <summary>Informations complémentaires (optionnel)</summary>
            <div class="field-group-body">
              <div class="field"><label for="p-porteur">Porteur du projet</label><input class="input" id="p-porteur" maxlength="255" placeholder="Ex. Mairie de…, association…"></div>
              <div class="field"><label for="p-budget">Budget</label><input class="input" id="p-budget" maxlength="120" placeholder="Ex. 250 000 €"></div>
              <div class="field"><label for="p-territoire">Territoire</label><input class="input" id="p-territoire" maxlength="255" placeholder="Ex. Commune, EPCI…"></div>
              <div class="field"><label for="p-stade">Stade d'avancement</label><input class="input" id="p-stade" maxlength="120" placeholder="Ex. Étude, conception, travaux…"></div>
            </div>
          </details>
          <button class="btn btn-primary" type="submit" style="margin-top:var(--sp-2);">Proposer ce projet</button>
          <p class="text-xs text-faint" id="p-quota" style="margin-top:var(--sp-2);"></p>
        </form>` : `<div class="card" style="margin-bottom:var(--sp-6);"><p style="margin:0;">L'animateur a fermé la proposition de nouveaux projets. Vous pouvez consulter ceux déjà proposés ci-dessous.</p></div>`}
        <div class="section-head"><h2>Projets proposés <span class="badge badge-neutral" id="count-badge"></span></h2></div>
        <div id="dynamic-projects" class="project-grid"></div>
      `;
      if (allowed) {
        const draftFields = [
          document.getElementById("p-title"),
          document.getElementById("p-desc"),
          document.getElementById("p-image"),
          document.getElementById("p-map"),
          document.getElementById("p-porteur"),
          document.getElementById("p-budget"),
          document.getElementById("p-territoire"),
          document.getElementById("p-stade"),
        ];
        const draft = Boussole.draftAutosave(draftFields, `project|${code}`);
        if (draft.restore()) {
          Boussole.toast("Brouillon restauré.", "info");
          const restoredUrl = document.getElementById("p-image").value.trim();
          if (restoredUrl) showImagePreview(restoredUrl);
        }

        // -- Upload d'image (§2/§7) : le fichier choisi est envoyé
        // immédiatement au serveur ; l'URL renvoyée alimente le champ cache
        // `p-image`, qui reste le seul champ réellement soumis avec le
        // formulaire (upload de fichier et lien collé aboutissent à la
        // même donnée : une URL d'image, l'une locale, l'autre externe).
        const fileInput = document.getElementById("p-image-file");
        const urlInput = document.getElementById("p-image");
        const statusEl = document.getElementById("p-image-status");
        const toggleBtn = document.getElementById("p-image-toggle-url");

        toggleBtn.addEventListener("click", () => {
          const showingUrl = urlInput.style.display !== "none";
          urlInput.style.display = showingUrl ? "none" : "block";
          fileInput.style.display = showingUrl ? "block" : "none";
          toggleBtn.textContent = showingUrl ? "coller un lien d'image" : "importer un fichier";
          statusEl.textContent = "";
        });

        fileInput.addEventListener("change", async () => {
          const file = fileInput.files[0];
          if (!file) return;
          statusEl.textContent = "Envoi en cours…";
          statusEl.style.color = "";
          try {
            const formData = new FormData();
            formData.append("file", file);
            const res = await fetch(`/api/webinars/${encodeURIComponent(code)}/upload-image`, {
              method: "POST",
              body: formData,
            });
            const data = await res.json();
            if (!res.ok) {
              statusEl.textContent = data.detail || "Échec de l'envoi.";
              statusEl.style.color = "var(--red)";
              fileInput.value = "";
              return;
            }
            urlInput.value = data.image_url;
            statusEl.textContent = "Image ajoutée ✓";
            statusEl.style.color = "var(--green-700)";
            showImagePreview(data.image_url);
            urlInput.dispatchEvent(new Event("input"));
          } catch (err) {
            statusEl.textContent = "Erreur de connexion, veuillez réessayer.";
            statusEl.style.color = "var(--red)";
            fileInput.value = "";
          }
        });

        function showImagePreview(url) {
          const preview = document.getElementById("p-image-preview");
          preview.src = url;
          preview.style.display = "block";
          preview.onerror = () => { preview.style.display = "none"; };
        }

        document.getElementById("project-form").addEventListener("submit", (e) => {
          e.preventDefault();
          const title = document.getElementById("p-title").value.trim();
          if (title.length < 3) { Boussole.toast("Le titre est trop court.", "error"); return; }
          ws.send("submit_project", {
            title,
            description: document.getElementById("p-desc").value.trim(),
            context: "",
            image_url: document.getElementById("p-image").value.trim() || null,
            map_url: document.getElementById("p-map").value.trim() || null,
            porteur: document.getElementById("p-porteur").value.trim() || null,
            budget: document.getElementById("p-budget").value.trim() || null,
            territoire: document.getElementById("p-territoire").value.trim() || null,
            stade: document.getElementById("p-stade").value.trim() || null,
          });
          e.target.reset();
          draft.clear();
          document.getElementById("p-image-preview").style.display = "none";
          statusEl.textContent = "";
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
    root.dataset.shell = "";
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
        ${p.is_mine ? `<span class="badge badge-red conflict-inline-badge" title="Vous avez proposé ce projet">⚠️ Votre projet</span>` : ""}
        ${p.image_url ? `<img src="${Boussole.escapeHtml(p.image_url)}" alt="" style="width:100%;border-radius:8px;height:120px;object-fit:cover;" onerror="this.remove()">` : ""}
        <h3 style="margin:0;">${Boussole.escapeHtml(p.title)}</h3>
        ${p.description ? `<p class="text-sm" style="margin:0;">${Boussole.escapeHtml(p.description)}</p>` : ""}
        ${p.map_url ? Boussole.mapEmbed(p.map_url, { height: 140 }) : ""}
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
  // Volet projet (§3) : panneau latéral permanent affiché pendant toute la
  // consultation, à côté du contenu de l'étape en cours (positifs / négatifs
  // / vote / améliorations). `ensureConsultationShell` crée cette disposition
  // une seule fois ; seul `#consultation-step-content` est ensuite reconstruit
  // par les fonctions d'étape existantes, qui ciblent ce conteneur au lieu de
  // `root` directement.
  function ensureConsultationShell() {
    if (root.dataset.shell === "consultation") return;
    root.dataset.shell = "consultation";
    root.dataset.mode = "";
    root.innerHTML = `
      <div class="consultation-layout">
        <div id="consultation-panel-slot"></div>
        <div id="consultation-step-content"></div>
      </div>`;
  }

  function renderConsultation(state) {
    const c = state.consultation;
    if (!c || !c.project) { root.dataset.shell = ""; root.innerHTML = `<div class="empty-state"><p>En préparation…</p></div>`; return; }
    titleEl.textContent = `${c.project.title}`;
    ensureConsultationShell();
    document.getElementById("consultation-panel-slot").innerHTML = Boussole.projectPanel(c.project, {
      axisIndex: c.axis_index, axisCount: c.axis_count,
    });
    stopStepTimer();
    const timerEl = document.getElementById("consultation-panel-slot").querySelector(".project-panel");
    if (timerEl) {
      const timerSlot = document.createElement("div");
      timerSlot.className = "step-timer-slot";
      timerEl.prepend(timerSlot);
      stopStepTimer = Boussole.mountStepTimer(timerSlot, c);
    }

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
    const stepRoot = document.getElementById("consultation-step-content");

    if (root.dataset.mode !== key) {
      root.dataset.mode = key;
      stepRoot.innerHTML = `
        <div class="card" style="margin-bottom:var(--sp-5);">
          <span class="badge badge-blue">${c.axis_count > 1 ? `Axe ${c.axis_index + 1}/${c.axis_count} · ` : ""}${info.label}</span>
          ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
          <h2 style="margin:var(--sp-3) 0 var(--sp-2);">${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</h2>
          ${c.axis ? Boussole.axisNationalTarget(c.axis.categorie) : ""}
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
        propDraft.clear();
      });
      // Délégation : les boutons de vote sont recréés à chaque rafraîchissement
      // de la liste, mais ce conteneur (le formulaire shell) reste stable.
      stepRoot.addEventListener("click", onVoteClick);

      // Brouillon (§7) : la clé inclut l'étape et l'axe, pour ne jamais
      // proposer par erreur le brouillon d'une autre étape/axe au retour
      // sur ce formulaire (le shell est recréé à chaque changement de `key`,
      // donc ce bloc s'exécute une fois par étape/axe).
      var propDraft = Boussole.draftAutosave(
        [document.getElementById("prop-text")], `prop|${code}|${key}`
      );
      if (propDraft.restore()) {
        Boussole.toast("Brouillon restauré.", "info");
      }
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
    const stepRoot = document.getElementById("consultation-step-content");
    stepRoot.innerHTML = `
      <div class="card" style="margin-bottom:var(--sp-5);">
        <span class="badge badge-blue">${c.axis_count > 1 ? `Axe ${c.axis_index + 1}/${c.axis_count} · ` : ""}Vote</span>
        ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
        <h2 style="margin:var(--sp-3) 0 var(--sp-2);">${Boussole.escapeHtml(c.axis ? c.axis.texte : "")}</h2>
        ${c.axis ? Boussole.axisNationalTarget(c.axis.categorie) : ""}
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
            <div class="bar-track"><div class="bar-fill" style="width:${cot.percentages[cc.key] || 0}%;background:${cc.key === "FAVORABLE" ? "var(--cotation-favorable)" : cc.key === "DEFAVORABLE" ? "var(--cotation-defavorable)" : "var(--cotation-neutre)"};"></div></div>
            <div class="pct">${Boussole.fmtPct(cot.percentages[cc.key] || 0)}</div>
          </div>`).join("")}
      </div>
    `;
    Boussole.qsa(".cotation-btn", stepRoot).forEach((btn) => {
      btn.addEventListener("click", () => ws.send("submit_cotation", { reponse: btn.dataset.key }));
    });
  }

  // -- ENDED ------------------------------------------------------------------
  function renderEnded(state) {
    titleEl.textContent = "Webinaire terminé";
    root.dataset.mode = "";
    root.dataset.shell = "";
    root.innerHTML = `
      <div class="empty-state card">
        <div class="ico">✅</div>
        <h2>Merci pour votre participation !</h2>
        <p>La consultation est terminée. ${state.consultation && state.consultation.project ? `Le projet étudié était : <strong>${Boussole.escapeHtml(state.consultation.project.title)}</strong>.` : ""}</p>
      </div>`;
  }
})();
