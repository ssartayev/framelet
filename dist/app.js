const get = (id) => document.getElementById(id);
const idea = get("idea");
const pitch = get("pitch-card");
const renderButton = get("render-button");
const surprise = get("surprise");
const pitchButton = get("pitch-button");
const notice = get("notice");
const progress = get("progress");
const film = get("film");
const download = get("download");
let activeId;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "The local server returned an error.");
    error.status = response.status;
    throw error;
  }
  return data;
}

async function checkModels() {
  const status = get("local-status");
  try {
    const models = await api("/api/status");
    const ready = models.writer && models.video;
    const pendingModel = models.writer ? "VIDEO MODEL SETUP" : "WRITER MODEL SETUP";
    status.dataset.ready = ready;
    status.lastElementChild.textContent = ready ? "LOCAL STUDIO" : pendingModel;
    if (ready) return;
  } catch {
    status.dataset.ready = false;
    status.lastElementChild.textContent = "SERVER OFFLINE";
  }
  setTimeout(checkModels, 5000);
}

checkModels();

function announce(message, isError = false) {
  notice.textContent = message;
  notice.hidden = !message;
  notice.dataset.error = isError;
}

function setBusy(busy) {
  for (const button of [pitchButton, surprise, renderButton]) button.disabled = busy;
}

function showPitch(plan) {
  get("pitch-heading").textContent = plan.title;
  get("pitch-text").textContent = plan.pitch;
  get("shot-list").replaceChildren(...plan.shots.map(({ visual }) => {
    const item = document.createElement("li");
    item.textContent = visual;
    return item;
  }));
  get("shots").open = false;
  pitch.hidden = false;
}

function updateProgress(value) {
  progress.hidden = false;
  progress.setAttribute("aria-valuenow", value);
  progress.firstElementChild.style.width = `${value}%`;
}

async function waitForJob(id, target) {
  while (true) {
    const job = await api(`/api/jobs/${id}`);
    if (job.status === "error") throw new Error(job.detail);
    if (job.status === target) return job;
    announce(job.detail);
    if (typeof job.progress === "number") updateProgress(job.progress);
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function createPitch(text) {
  setBusy(true);
  try {
    activeId = undefined;
    pitch.hidden = true;
    announce("Writing a three-shot pitch…");
    const { id } = await api("/api/pitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idea: text }),
    });
    const job = await waitForJob(id, "pitch");
    activeId = id;
    showPitch(job.plan);
    announce("");
  } catch (error) {
    announce(error.message, true);
  } finally {
    setBusy(false);
  }
}

get("idea-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!idea.value.trim()) {
    announce("Enter a short idea, or use Surprise me.");
    idea.focus();
    return;
  }
  createPitch(idea.value.trim());
});

surprise.addEventListener("click", () => createPitch(""));

renderButton.addEventListener("click", async () => {
  if (!activeId) return;
  setBusy(true);
  film.hidden = true;
  film.removeAttribute("src");
  film.load();
  download.hidden = true;
  get("placeholder").hidden = false;
  get("screen-title").textContent = "Generating your film";
  get("screen-caption").textContent = "Each shot is rendered on this Mac.";
  updateProgress(5);
  try {
    const { id } = await api(`/api/jobs/${activeId}/render`, { method: "POST" });
    const job = await waitForJob(id, "ready");
    activeId = undefined;
    get("placeholder").hidden = true;
    film.src = job.video;
    film.hidden = false;
    download.href = job.video;
    download.hidden = false;
    announce("Your film is ready.");
  } catch (error) {
    const waiting = error.status === 503;
    get("screen-title").textContent = waiting ? "Setting up the video model" : "Render could not finish";
    get("screen-caption").textContent = waiting ? "Your pitch is ready to retry." : "See the status message for details.";
    announce(error.message, !waiting);
  } finally {
    progress.hidden = true;
    setBusy(false);
    renderButton.disabled = !activeId;
  }
});
