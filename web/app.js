const elements = {
  fileInput: document.querySelector("#fileInput"),
  dropZone: document.querySelector("#dropZone"),
  fileInfo: document.querySelector("#fileInfo"),
  fileName: document.querySelector("#fileName"),
  fileSize: document.querySelector("#fileSize"),
  changeFileButton: document.querySelector("#changeFileButton"),
  startButton: document.querySelector("#startButton"),
  uploadPanel: document.querySelector("#uploadPanel"),
  progressPanel: document.querySelector("#progressPanel"),
  progressPhase: document.querySelector("#progressPhase"),
  progressFileName: document.querySelector("#progressFileName"),
  progressPercent: document.querySelector("#progressPercent"),
  progressBar: document.querySelector("#progressBar"),
  progressTrack: document.querySelector(".progress-track"),
  elapsedTime: document.querySelector("#elapsedTime"),
  estimatedTime: document.querySelector("#estimatedTime"),
  resultPanel: document.querySelector("#resultPanel"),
  resultText: document.querySelector("#resultText"),
  resetButton: document.querySelector("#resetButton"),
  copyButton: document.querySelector("#copyButton"),
  downloadButton: document.querySelector("#downloadButton"),
  downloadHint: document.querySelector("#downloadHint"),
  errorBanner: document.querySelector("#errorBanner"),
  connectionBadge: document.querySelector("#connectionBadge"),
  engineLabel: document.querySelector("#engineLabel"),
};

const state = {
  file: null,
  downloadName: "",
  working: false,
  maxUploadMb: null,
  pollTimer: null,
};

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
}

function showError(message) {
  elements.errorBanner.textContent = message;
  elements.errorBanner.hidden = !message;
}

function selectFile(file) {
  showError("");
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".mp3")) {
    showError("仅支持 MP3 文件，请选择后缀为 .mp3 的音频。");
    return;
  }
  if (state.maxUploadMb && file.size > state.maxUploadMb * 1024 * 1024) {
    showError(`文件超过 ${state.maxUploadMb} MB 上限，请压缩或分割后再试。`);
    return;
  }
  state.file = file;
  elements.fileName.textContent = file.name;
  elements.fileSize.textContent = formatBytes(file.size);
  elements.fileInfo.hidden = false;
  elements.startButton.disabled = false;
  elements.dropZone.querySelector("h2").textContent = "文件已选择";
  elements.dropZone.querySelector("p").textContent = "点击下方按钮开始，或重新选择文件";
}

async function fetchJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (_error) {
    throw new Error("无法连接本地服务，请回到启动器点击“启动 / 重启服务”。");
  }
  let data;
  try {
    data = await response.json();
  } catch (_error) {
    throw new Error(`本地服务返回异常（HTTP ${response.status}），请重新启动服务。`);
  }
  if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）。`);
  return data;
}

function showProgress(filename) {
  elements.uploadPanel.hidden = true;
  elements.resultPanel.hidden = true;
  elements.progressPanel.hidden = false;
  elements.progressFileName.textContent = filename;
  updateProgress({ progress: 0, phase: "正在上传并创建任务…", elapsed_seconds: 0, estimated_seconds: null });
}

function updateProgress(job) {
  const percent = Math.max(0, Math.min(100, Math.round((job.progress || 0) * 100)));
  elements.progressPhase.textContent = job.phase || "转写中…";
  elements.progressPercent.textContent = `${percent}%`;
  elements.progressBar.style.width = `${percent}%`;
  elements.progressTrack.setAttribute("aria-valuenow", String(percent));
  elements.elapsedTime.textContent = `已用时 ${formatDuration(job.elapsed_seconds)}`;
  elements.estimatedTime.textContent = job.estimated_seconds == null
    ? "正在估算剩余时间"
    : `预计还需 ${formatDuration(job.estimated_seconds)}`;
}

async function pollJob(jobId) {
  if (!state.working) return;
  try {
    const job = await fetchJson(`/api/transcriptions/${encodeURIComponent(jobId)}`);
    if (job.status === "succeeded") {
      state.working = false;
      state.downloadName = job.download_name;
      elements.progressPanel.hidden = true;
      elements.resultPanel.hidden = false;
      elements.resultText.value = job.text || "";
      elements.downloadHint.textContent = `下载文件名：${job.download_name} · 修改文本后请重新下载`;
      elements.resultText.focus();
      return;
    }
    if (job.status === "failed") throw new Error(job.error || "转写失败，请重试。");
    updateProgress(job);
    state.pollTimer = window.setTimeout(() => pollJob(jobId), 800);
  } catch (error) {
    state.working = false;
    elements.progressPanel.hidden = true;
    elements.uploadPanel.hidden = false;
    elements.startButton.disabled = false;
    showError(error.message);
  }
}

async function startTranscription() {
  if (!state.file || state.working) return;
  showError("");
  state.working = true;
  elements.startButton.disabled = true;
  showProgress(state.file.name);
  const body = new FormData();
  body.append("file", state.file, state.file.name);
  try {
    const job = await fetchJson("/api/transcriptions", { method: "POST", body });
    await pollJob(job.id);
  } catch (error) {
    state.working = false;
    elements.progressPanel.hidden = true;
    elements.uploadPanel.hidden = false;
    elements.startButton.disabled = false;
    showError(error.message);
  }
}

function reset() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.file = null;
  state.downloadName = "";
  state.working = false;
  elements.fileInput.value = "";
  elements.fileInfo.hidden = true;
  elements.startButton.disabled = true;
  elements.resultPanel.hidden = true;
  elements.progressPanel.hidden = true;
  elements.uploadPanel.hidden = false;
  elements.resultText.value = "";
  elements.dropZone.querySelector("h2").textContent = "拖拽 MP3 到这里";
  elements.dropZone.querySelector("p").textContent = "或点击选择文件";
  showError("");
  elements.dropZone.focus();
}

async function copyText() {
  const text = elements.resultText.value;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_error) {
    elements.resultText.select();
    document.execCommand("copy");
    elements.resultText.setSelectionRange(0, 0);
  }
  const label = elements.copyButton.querySelector("span");
  label.textContent = "已复制";
  window.setTimeout(() => { label.textContent = "复制文本"; }, 1600);
}

function downloadText() {
  if (!state.downloadName) return;
  const blob = new Blob([elements.resultText.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = state.downloadName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

elements.dropZone.addEventListener("click", () => elements.fileInput.click());
elements.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    elements.fileInput.click();
  }
});
elements.changeFileButton.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => selectFile(elements.fileInput.files[0]));
elements.startButton.addEventListener("click", startTranscription);
elements.resetButton.addEventListener("click", reset);
elements.copyButton.addEventListener("click", copyText);
elements.downloadButton.addEventListener("click", downloadText);

["dragenter", "dragover"].forEach((name) => elements.dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  elements.dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => elements.dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  elements.dropZone.classList.remove("dragging");
}));
elements.dropZone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length > 1) {
    showError("一次仅支持转写一个 MP3 文件。");
    return;
  }
  selectFile(event.dataTransfer.files[0]);
});

window.addEventListener("beforeunload", (event) => {
  if (state.working) {
    event.preventDefault();
    event.returnValue = "";
  }
});

fetchJson("/api/health").then((health) => {
  state.maxUploadMb = health.max_upload_mb || null;
  elements.engineLabel.textContent = `faster-whisper ${health.model}`;
}).catch(() => {
  elements.connectionBadge.classList.add("offline");
  elements.connectionBadge.innerHTML = '<span class="status-dot"></span>本地服务未连接';
});
