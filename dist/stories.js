const get = (id) => document.getElementById(id);
const genre = get("genre");
const narrator = get("narrator");
const background = get("background");
const backgroundPreview = get("background-preview");
const film = get("story-film");
const download = get("story-download");
const notice = get("story-notice");
const storyCard = get("story-card");
const hookPreview = get("hook-preview");
const premise = get("story-premise");
const premiseSource = get("premise-source");
const preparedDraft = get("prepared-draft");
let episodePremises = new Map();
let preparedStoryReady = false;

premise.addEventListener("change", () => {
  const entry = episodePremises.get(premise.value);
  preparedDraft.hidden = !entry || !preparedStoryReady;
  premiseSource.hidden = !entry;
  if (!entry) {
    premiseSource.removeAttribute("href");
    return;
  }
  premiseSource.href = entry.source;
  get("story-idea").value = entry.idea;
  if ([...genre.options].some((option) => option.value === entry.category)) {
    genre.value = entry.category;
  }
});

let activeId;
let currentStory;
get("sentence-select").addEventListener("change", () => {
  const [section, index] = get("sentence-select").value.split(":");
  get("sentence-text").value = currentStory?.[section]?.[Number(index)] || "";
});
get("sentence-form").addEventListener("submit", async event => {
  event.preventDefault();
  const [section, index] = get("sentence-select").value.split(":");
  const hook = selectedHook();
  const job = await runJob(() => api(`/api/stories/${activeId}/edit`, {
    section, index: Number(index), text: get("sentence-text").value.trim(),
  }), "pitch");
  if (job) { showStory(job.story, hook); showBackground(); }
});
let ready = false;

const saved = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  remove(key) { try { localStorage.removeItem(key); } catch {} },
};
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
async function api(path, payload, requestId = crypto.randomUUID()) {
  const options = payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": requestId }, body: JSON.stringify(payload),
  };
  if (payload !== undefined) saved.set("framelet-pending", JSON.stringify({ path, payload, requestId }));
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch(path, { ...options, signal: AbortSignal.timeout(15000) });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || "The studio could not finish this request.");
        error.status = response.status;
        if (response.status < 500 && payload !== undefined) saved.remove("framelet-pending");
        throw error;
      }
      if (payload !== undefined) saved.remove("framelet-pending");
      return data;
    } catch (error) {
      if ((error.status && error.status < 500) || attempt === 2) throw error;
      announce("Connection interrupted. Reconnecting without starting a second job…");
      await delay(800 * 2 ** attempt);
    }
  }
}

function announce(text, error = false) {
  notice.hidden = !text;
  notice.textContent = text;
  notice.dataset.error = error;
}

function busy(state) {
  get("draft").disabled = state || !ready;
  preparedDraft.disabled = state || !preparedStoryReady;
  get("hooks").closest("fieldset").disabled = state;
  for (const id of ["genre", "narrator", "language", "duration", "background", "story-premise", "story-idea", "export", "twist", "preview-hook", "edit-scope", "edit-request", "rewrite", "projects", "sentence-select", "sentence-text", "save-sentence", "caption-style"]) {
    get(id).disabled = state;
  }
}

function showBackground() {
  film.pause();
  film.hidden = true;
  download.hidden = true;
  backgroundPreview.hidden = false;
  backgroundPreview.src = `/backgrounds/${background.value}.mp4`;
  get("preview-label").textContent = "Background preview";
}

async function pollJob(id, target) {
  const deadline = Date.now() + 10 * 60 * 1000;
  while (Date.now() < deadline) {
    const job = await api(`/api/jobs/${id}`);
    announce(job.detail);
    if (job.status === "error") throw new Error(job.detail);
    if (job.status === target || ["pitch", "ready"].includes(job.status)) return { ...job, id };
    await delay(1200);
  }
  throw new Error("This job is taking longer than expected. It is saved; reopen it from Saved projects.");
}

async function refreshProjects() {
  const { projects } = await api("/api/projects");
  get("projects").replaceChildren(new Option("New story", ""), ...projects.map(project => new Option(project.title, project.id)));
  if (activeId) get("projects").value = activeId;
}

async function runJob(request, target) {
  busy(true);
  try {
    const { id } = await request();
    activeId = id;
    saved.set("framelet-active", id);
    const job = await pollJob(id, target);
    refreshProjects().catch(() => {});
    return job;
  } catch (error) {
    announce(error.message, true);
  } finally {
    busy(false);
  }
}

async function restoreProject(id) {
  busy(true);
  try {
    let job = await api(`/api/jobs/${id}`);
    activeId = id;
    get("projects").value = id;
    saved.set("framelet-active", id);
    if (["queued", "writing", "rendering"].includes(job.status)) job = await pollJob(id);
    if (job.story) {
      get("language").value = job.language;
      get("language").dispatchEvent(new Event("change"));
      get("duration").value = job.duration;
      narrator.value = job.voice;
      background.value = job.background || "neon";
      get("caption-style").value = job.caption_style || "bold";
      showBackground();
      showStory(job.story, job.selected_hook || 0);
      get("story-narrator").textContent = `Narrator · ${narrator.selectedOptions[0]?.text || job.voice}`;
      if (job.video) {
        backgroundPreview.pause(); backgroundPreview.hidden = true;
        film.src = `${job.video}?v=${job.updated_at}`; film.hidden = false;
        download.href = film.src; download.hidden = false;
      }
    }
    announce(job.detail, job.status === "error");
  } catch (error) { announce(error.message, true); }
  finally { busy(false); }
}
get("projects").addEventListener("change", () => {
  if (get("projects").value) restoreProject(get("projects").value);
  else { activeId = null; saved.remove("framelet-active"); storyCard.hidden = true; showBackground(); }
});

function selectedHook() {
  return Number(get("hooks").querySelector("input:checked")?.value ?? 0);
}

function clearHookPreview() {
  hookPreview.pause();
  hookPreview.removeAttribute("src");
  hookPreview.load();
  hookPreview.hidden = true;
}

function showStory(story, selected = 0) {
  clearHookPreview();
  currentStory = story;
  const sentences = ["hooks", "setup", "ending"].flatMap(section => story[section].map((text, index) =>
    new Option(`${({ hooks: "Opening", setup: "Story", ending: "Ending" })[section]} ${index + 1}: ${text}`, `${section}:${index}`)));
  get("sentence-select").replaceChildren(...sentences);
  get("sentence-select").dispatchEvent(new Event("change"));
  get("story-title").textContent = story.title;
  get("setup").textContent = story.setup.join(" ");
  get("ending").textContent = story.ending.join(" ");
  get("hooks").replaceChildren(...story.hooks.map((hook, index) => {
    const row = document.createElement("div");
    row.className = "hook";
    const label = document.createElement("label");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "hook";
    radio.value = index;
    radio.checked = index === selected;
    radio.addEventListener("change", clearHookPreview);
    label.append(radio, document.createTextNode(hook));
    row.append(label);
    return row;
  }));
  get("story-details").open = true;
  storyCard.hidden = false;
}

get("story-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const isPrepared = event.submitter === preparedDraft;
  if (isPrepared) {
    get("language").value = "ru";
    get("duration").value = "30";
    get("language").dispatchEvent(new Event("change"));
  }
  const job = await runJob(() => api("/api/stories", {
    idea: get("story-idea").value.trim(), category: genre.value, voice: narrator.value,
    duration: Number(get("duration").value), language: get("language").value,
    ...(isPrepared ? { premise: premise.value } : {}),
  }), "pitch");
  if (!job) return;
  activeId = job.id;
  get("story-narrator").textContent = `Narrator · ${narrator.selectedOptions[0].text}`;
  showBackground();
  showStory(job.story);
});

get("twist").addEventListener("click", async () => {
  const job = await runJob(() => api(`/api/stories/${activeId}/twist`, {}), "pitch");
  if (!job) return;
  showStory(job.story, selectedHook());
  get("story-details").open = true;
  showBackground();
});

get("rewrite-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const instruction = get("edit-request").value.trim();
  if (!instruction) {
    announce("Enter an edit request.", true);
    return;
  }
  const hook = selectedHook();
  const job = await runJob(() => api(`/api/stories/${activeId}/rewrite`, {
    scope: get("edit-scope").value, instruction,
  }), "pitch");
  if (!job) return;
  showStory(job.story, hook);
  get("edit-request").value = "";
  showBackground();
});

get("preview-hook").addEventListener("click", async () => {
  clearHookPreview();
  const job = await runJob(() => api(`/api/stories/${activeId}/preview`, { hook: selectedHook(), voice: narrator.value }), "pitch");
  if (!job) return;
  hookPreview.src = `${job.preview}?v=${Date.now()}`;
  hookPreview.hidden = false;
  hookPreview.play().catch(() => {});
});

get("export").addEventListener("click", async () => {
  hookPreview.pause();
  const hook = selectedHook();
  const job = await runJob(() => api(`/api/stories/${activeId}/render`, {
    hook, voice: narrator.value, background: background.value, caption_style: get("caption-style").value,
  }), "ready");
  if (!job) return;
  showStory(job.story, hook);
  backgroundPreview.pause();
  backgroundPreview.hidden = true;
  film.src = `${job.video}?v=${Date.now()}`;
  film.hidden = false;
  download.href = film.src;
  download.hidden = false;
  get("preview-label").textContent = "Fictional story";
});

background.addEventListener("change", showBackground);

try {
  const options = await api("/api/stories/options");
  if (options.storiesOnly) get("ai-video-tab").hidden = true;
  preparedStoryReady = options.preparedStoryReady;
  episodePremises = new Map(options.premises.map((entry) => [entry.id, entry]));
  premise.append(...options.premises.map((entry) => new Option(entry.title, entry.id)));
  genre.replaceChildren(...options.categories.map((name) => new Option(name, name)));
  get("language").replaceChildren(...Object.entries(options.languages).map(([id, name]) => new Option(name, id)));
  background.replaceChildren(...Object.entries(options.backgrounds).map(([id, name]) => new Option(name, id)));
  showBackground();
  function updateVoices() {
    const language = get("language").value;
    const voices = options.voicesByLanguage[language];
    const previousVoice = narrator.value;
    narrator.replaceChildren(...Object.entries(voices).map(([id, name]) => new Option(name, id)));
    if (Object.hasOwn(voices, previousVoice)) narrator.value = previousVoice;
    ready = options.readyByLanguage[language];
    get("story-status").textContent = ready ? "STORY STUDIO" : "API SETUP";
    busy(false);
    announce(ready ? "" : "Configure a cloud writer key and an ElevenLabs voice for this language, then refresh.", !ready);
  }
  get("language").addEventListener("change", updateVoices);
  updateVoices();
  await refreshProjects();
  const pending = saved.get("framelet-pending");
  if (pending) {
    const request = JSON.parse(pending);
    const job = await runJob(() => api(request.path, request.payload, request.requestId));
    if (job) await restoreProject(job.id);
  } else if (saved.get("framelet-active")) {
    await restoreProject(saved.get("framelet-active"));
  }
} catch (error) {
  announce(error.message, true);
}
