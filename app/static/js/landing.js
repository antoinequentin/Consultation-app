document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("create-modal");
  document.getElementById("btn-open-create").addEventListener("click", () => modal.showModal());
  document.getElementById("btn-open-create-2").addEventListener("click", () => modal.showModal());
  document.getElementById("close-create").addEventListener("click", () => modal.close());

  document.getElementById("create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitBtn = e.target.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner"></span> Création…';
    try {
      const res = await fetch("/api/webinars", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: document.getElementById("f-title").value,
          password: document.getElementById("f-password").value,
          moderation_enabled: document.getElementById("f-moderation").checked,
          allow_project_proposals: document.getElementById("f-allow-projects").checked,
          seed_project_title: document.getElementById("f-seed-title").value || null,
          seed_project_description: document.getElementById("f-seed-desc").value || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Impossible de créer le webinaire.");
      }
      const data = await res.json();
      Boussole.setHostToken(data.code, data.host_token);
      window.location.href = `/w/${data.code}/host`;
    } catch (err) {
      Boussole.toast(err.message, "error");
      submitBtn.disabled = false;
      submitBtn.textContent = "Créer et obtenir le code";
    }
  });

  const joinForm = document.getElementById("join-form");
  const joinError = document.getElementById("join-error");
  joinForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    joinError.style.display = "none";
    const code = document.getElementById("join-code").value.trim().toUpperCase();
    if (!code) return;
    try {
      const res = await fetch(`/api/webinars/${encodeURIComponent(code)}`);
      if (!res.ok) throw new Error("Code introuvable. Vérifiez auprès de l'animateur.");
      window.location.href = `/w/${code}`;
    } catch (err) {
      joinError.textContent = err.message;
      joinError.style.display = "block";
    }
  });

  document.getElementById("join-code").addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase();
  });
});
