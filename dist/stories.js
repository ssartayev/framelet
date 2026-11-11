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
let ready = false;

async function api(path, payload) {
  const options = payload === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The local studio returned an error.");
  return data;
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
  for (const id of ["genre", "narrator", "language", "duration", "background", "story-premise", "story-idea", "export", "twist", "preview-hook", "edit-scope", "edit-request", "rewrite"]) {
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

async function runJob(request, target) {
  busy(true);
  try {
    const { id } = await request();
    while (true) {
      const job = await api(`/api/jobs/${id}`);
      if (job.status === "error") throw new Error(job.detail);
      announce(job.detail);
      if (job.status === target) return { ...job, id };
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  } catch (error) {
    announce(error.message, true);
  } finally {
    busy(false);
  }
}

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
  get("ending").textContent = job.story.ending.join(" ");
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
  const job = await runJob(() => api(`/api/stories/${activeId}/preview`, { hook: selectedHook() }), "pitch");
  if (!job) return;
  hookPreview.src = `${job.preview}?v=${Date.now()}`;
  hookPreview.hidden = false;
  hookPreview.play().catch(() => {});
});

get("export").addEventListener("click", async () => {
  hookPreview.pause();
  const hook = selectedHook();
  const job = await runJob(() => api(`/api/stories/${activeId}/render`, {
    hook, background: background.value,
  }), "ready");
  if (!job) return;
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
    get("story-status").textContent = ready ? "STORY STUDIO" : "MODEL SETUP";
    busy(false);
    announce(ready ? "" : "Run setup.sh to install the local story models, then refresh.", !ready);
  }
  get("language").addEventListener("change", updateVoices);
  updateVoices();
} catch (error) {
  announce(error.message, true);
}
