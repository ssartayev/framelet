const get = (id) => document.getElementById(id);
const genre = get("genre");
const narrator = get("narrator");
const background = get("background");
const backgroundPreview = get("background-preview");
const film = get("story-film");
const download = get("story-download");
const notice = get("story-notice");
const storyCard = get("story-card");
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
  for (const id of ["genre", "narrator", "duration", "background", "story-idea", "export", "twist"]) {
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

function showStory(story) {
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
    radio.checked = index === 0;
    label.append(radio, document.createTextNode(hook));
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = `/hooks/${activeId}/hook-${index}.wav`;
    audio.setAttribute("aria-label", `Opening ${index + 1}`);
    audio.addEventListener("play", () => {
      for (const other of get("hooks").querySelectorAll("audio")) {
        if (other !== audio) other.pause();
      }
    });
    row.append(label, audio);
    return row;
  }));
  storyCard.hidden = false;
}

get("story-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const job = await runJob(() => api("/api/stories", {
    idea: get("story-idea").value.trim(), category: genre.value, voice: narrator.value,
    duration: Number(get("duration").value),
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

get("export").addEventListener("click", async () => {
  const hook = Number(get("hooks").querySelector("input:checked").value);
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
  genre.replaceChildren(...options.categories.map((name) => new Option(name, name)));
  narrator.replaceChildren(...Object.entries(options.voices).map(([id, name]) => new Option(name, id)));
  background.replaceChildren(...Object.entries(options.backgrounds).map(([id, name]) => new Option(name, id)));
  showBackground();
  ready = options.ready;
  busy(false);
  if (!ready) {
    get("story-status").textContent = "MODEL SETUP";
    announce("Run setup.sh to install the local story models, then refresh.", true);
  }
} catch (error) {
  announce(error.message, true);
}
