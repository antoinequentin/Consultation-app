/* ==========================================================================
   Écran de projection — connexion en lecture seule (role=viewer), pensé
   pour être affiché sur un grand écran pendant le webinaire : grande
   typographie, mise à jour automatique, aucune interaction requise.
   ========================================================================== */

(() => {
  const code = document.body.dataset.code;
  const main = document.getElementById("proj-main");
  const dial = document.getElementById("proj-dial");
  const titleEl = document.getElementById("proj-title");
  const presenceLine = document.getElementById("presence-line");

  const ws = Boussole.connect(code, { role: "viewer" });
  ws.on("state", onState);
  ws.on("_not_found", () => { titleEl.textContent = "Webinaire introuvable."; });
  ws.on("_disconnected", () => { presenceLine.textContent = "Reconnexion…"; });

  function onState(state) {
    presenceLine.textContent = `${state.participant_count} participant(s) connecté(s)${state.host_online ? "" : " · animateur hors ligne"}`;
    Boussole.renderDial(dial, state.webinar.phase, state.consultation ? state.consultation.step : 0, { showLabel: false, size: 140 });

    const phase = state.webinar.phase;
    if (phase === "lobby") return renderWaiting(state, "La session va bientôt commencer");
    if (phase === "project_submission") return renderProjectSubmission(state);
    if (phase === "project_vote") return renderProjectVote(state);
    if (phase === "consultation") return renderConsultation(state);
    if (phase === "ended") return renderEnded(state);
  }

  function joinBlock() {
    return `
      <div style="display:flex;flex-direction:column;align-items:center;gap:var(--sp-3);margin-top:var(--sp-5);">
        <img src="/api/webinars/${code}/qrcode.png" alt="QR code pour rejoindre" style="width:160px;height:160px;border-radius:12px;background:#fff;padding:10px;">
        <div class="proj-sub">Rejoignez avec le code <strong class="mono" style="color:#fff;">${code}</strong></div>
      </div>`;
  }

  function renderWaiting(state, label) {
    titleEl.textContent = label;
    Boussole.qsa(".proj-extra", main).forEach((n) => n.remove());
    const extra = document.createElement("div");
    extra.className = "proj-extra";
    extra.innerHTML = joinBlock();
    main.appendChild(extra);
  }

  function renderProjectSubmission(state) {
    titleEl.textContent = "Proposez votre projet !";
    Boussole.qsa(".proj-extra", main).forEach((n) => n.remove());
    const extra = document.createElement("div");
    extra.className = "proj-extra";
    const pf = state.project_phase;
    extra.innerHTML = `
      <p class="proj-sub">${pf.projects.length} projet(s) déjà proposé(s)</p>
      ${joinBlock()}
    `;
    main.appendChild(extra);
  }

  function renderProjectVote(state) {
    titleEl.textContent = "Votez pour le projet à étudier";
    Boussole.qsa(".proj-extra", main).forEach((n) => n.remove());
    const pf = state.project_phase;
    const sorted = [...pf.projects].sort((a, b) => (b.votes || 0) - (a.votes || 0));
    const total = pf.total_votes;
    const extra = document.createElement("div");
    extra.className = "proj-extra";
    extra.innerHTML = `<div class="proj-list">${sorted.map((p) => {
      const pct = total ? Math.round(100 * (p.votes || 0) / total) : 0;
      return `<div class="row">
        <div class="top"><strong>${Boussole.escapeHtml(p.title)}</strong><span class="mono">${p.votes || 0} vote(s)</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:var(--brass);height:100%;"></div></div>
      </div>`;
    }).join("")}</div>`;
    main.appendChild(extra);
  }

  function renderConsultation(state) {
    const c = state.consultation;
    if (!c || !c.project) { titleEl.textContent = "Préparation de la consultation…"; return; }
    Boussole.qsa(".proj-extra", main).forEach((n) => n.remove());
    const extra = document.createElement("div");
    extra.className = "proj-extra";

    if (c.step === 3) {
      titleEl.textContent = c.axis ? c.axis.texte : c.project.title;
      const cot = c.cotation || { counts: {}, total: 0, percentages: {} };
      const tiles = [
        { k: "FAVORABLE", label: "Favorable", color: "var(--green)" },
        { k: "NEUTRE", label: "Neutre", color: "var(--neutral-pass)" },
        { k: "DEFAVORABLE", label: "Défavorable", color: "var(--red)" },
      ];
      extra.innerHTML = `
        ${c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : ""}
        <p class="proj-sub">${cot.total} réponse(s)</p>
        <div class="proj-cotation">
          ${tiles.map((t) => `<div class="tile"><div class="pct" style="color:${t.color};">${Boussole.fmtPct(cot.percentages[t.k] || 0)}</div><div class="lbl">${t.label}</div></div>`).join("")}
        </div>`;
    } else {
      const stepLabels = { 1: "Impacts positifs", 2: "Impacts négatifs", 4: "Pistes d'amélioration" };
      titleEl.textContent = `${stepLabels[c.step] || ""} — ${c.axis ? c.axis.texte : ""}`;
      const props = [...(c.propositions || [])].filter((p) => p.status === "approved");
      props.sort((a, b) => b.consensus_pct - a.consensus_pct || b.total_votes - a.total_votes);
      const top = props.slice(0, 6);
      const badge = c.axis ? Boussole.categoryBadge(c.axis.categorie, c.axis.color) : "";
      extra.innerHTML = (badge ? badge : "") + (top.length ? `<div class="proj-list">${top.map((p) => `
        <div class="row">
          <div class="top"><span>${Boussole.escapeHtml(p.texte)}</span><span class="mono">${p.consensus_pct}% d'accord</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:${p.consensus_pct}%;background:var(--green);height:100%;"></div></div>
        </div>`).join("")}</div>` : `<p class="proj-sub">En attente des premières contributions…</p>`);
    }
    main.appendChild(extra);
  }

  function renderEnded(state) {
    titleEl.textContent = "Merci pour votre participation !";
    Boussole.qsa(".proj-extra", main).forEach((n) => n.remove());
    const extra = document.createElement("div");
    extra.className = "proj-extra";
    extra.innerHTML = state.consultation && state.consultation.project
      ? `<p class="proj-sub">Projet étudié : <strong style="color:#fff;">${Boussole.escapeHtml(state.consultation.project.title)}</strong></p>`
      : "";
    main.appendChild(extra);
  }
})();
