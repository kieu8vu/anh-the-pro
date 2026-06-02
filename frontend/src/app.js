// src/app.js — Logic chính của ứng dụng
// =======================================

import { fetchSizes, processPhoto, checkHealth } from "./utils/api.js";
import { downscaleIfNeeded, fileToDataURL, b64ToBlob } from "./utils/canvas.js";
import { CameraCapture } from "./components/camera.js";
import { PhotoPreview   } from "./components/preview.js";

// ─── State ──────────────────────────────────────────────────────────────────
const state = {
  selectedFile: null,  // File/Blob ảnh gốc
  sizes: {},           // Danh sách kích thước từ API
  processing: false,
};

// ─── DOM Refs ────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const dropzone      = $("dropzone");
const inputFile     = $("input-file");
const btnCamera     = $("btn-camera");
const btnProcess    = $("btn-process");
const sizeSelect    = $("size-select");
const bgSelect      = $("bg-select");
const enhanceCheck  = $("enhance-check");
const fmtSelect     = $("fmt-select");
const previewOrig   = $("preview-original");
const cameraSection = $("camera-section");
const resultSection = $("result-section");
const settingsPanel = $("settings-panel");
const statusBadge   = $("status-badge");
const progressBar   = $("progress-bar");
const progressWrap  = $("progress-wrap");

const preview = new PhotoPreview(resultSection);
const camera  = new CameraCapture(cameraSection, onPhotoCaptured);

// ─── Khởi động ──────────────────────────────────────────────────────────────
async function init() {
  await loadSizes();
  await pingBackend();
  registerEvents();
  registerServiceWorker();
}

async function loadSizes() {
  try {
    const data = await fetchSizes();
    state.sizes = data.sizes;
    sizeSelect.innerHTML = Object.entries(data.sizes)
      .map(([k, v]) => `<option value="${k}">${v.label}</option>`)
      .join("");
  } catch {
    sizeSelect.innerHTML = `
      <option value="the_3x4">Ảnh thẻ 3×4 cm</option>
      <option value="passport">Hộ chiếu (35×45mm)</option>
      <option value="visa_us">Visa Mỹ (2×2 inch)</option>
    `;
  }
}

async function pingBackend() {
  try {
    const h = await checkHealth();
    setStatus(h.rembg ? "AI sẵn sàng ✓" : "Chế độ cơ bản", h.rembg ? "ok" : "warn");
  } catch {
    setStatus("Backend offline — kiểm tra kết nối", "error");
  }
}

function registerEvents() {
  // Drag & Drop
  dropzone.addEventListener("dragover",  e => { e.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop",      e => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) loadFile(file);
  });

  // Click to browse
  dropzone.addEventListener("click", () => inputFile.click());
  inputFile.addEventListener("change", e => {
    if (e.target.files[0]) loadFile(e.target.files[0]);
  });

  // Camera
  btnCamera.addEventListener("click", () => camera.start());

  // Process
  btnProcess.addEventListener("click", processImage);

  // Reset
  document.addEventListener("resetApp", resetApp);
}

async function loadFile(file) {
  if (!file.type.startsWith("image/")) {
    alert("Vui lòng chọn file ảnh (JPG, PNG, WEBP, …)");
    return;
  }

  // Scale xuống nếu quá lớn
  const optimised = await downscaleIfNeeded(file, 2000);
  state.selectedFile = optimised;

  // Hiện xem trước
  const dataURL = await fileToDataURL(optimised);
  previewOrig.innerHTML = `<img src="${dataURL}" alt="Ảnh gốc" />`;
  settingsPanel.style.display = "block";
  btnProcess.disabled = false;

  dropzone.querySelector(".drop-hint").textContent = "✓ Đã chọn ảnh — nhấn Xử lý";
}

function onPhotoCaptured(blob) {
  loadFile(new File([blob], "camera.jpg", { type: "image/jpeg" }));
}

async function processImage() {
  if (!state.selectedFile || state.processing) return;

  state.processing = true;
  btnProcess.disabled = true;
  showProgress(true);
  setStatus("Đang xử lý…", "loading");

  try {
    const sizeKey   = sizeSelect.value;
    const bgColor   = bgSelect.value;
    const enhance   = enhanceCheck.checked;
    const outputFmt = fmtSelect.value;

    animateProgress();

    const result = await processPhoto(state.selectedFile, {
      sizeKey, bgColor, enhance, outputFmt,
    });

    showProgress(false);
    setStatus("Hoàn thành ✓", "ok");

    const sizeInfo = state.sizes[sizeKey] || { px_w: 354, px_h: 472 };
    preview.show(result, sizeInfo);
    resultSection.scrollIntoView({ behavior: "smooth" });

  } catch (err) {
    showProgress(false);
    setStatus("Lỗi: " + err.message, "error");
    alert("Xử lý thất bại: " + err.message);
  } finally {
    state.processing = false;
    btnProcess.disabled = false;
  }
}

function resetApp() {
  state.selectedFile = null;
  previewOrig.innerHTML = "";
  settingsPanel.style.display = "none";
  btnProcess.disabled = true;
  dropzone.querySelector(".drop-hint").textContent = "Kéo thả ảnh vào đây hoặc nhấn để chọn";
  setStatus("", "");
}

// ─── UI helpers ─────────────────────────────────────────────────────────────
function setStatus(msg, type) {
  statusBadge.textContent = msg;
  statusBadge.className   = `status-badge ${type}`;
}

function showProgress(show) {
  progressWrap.style.display = show ? "block" : "none";
}

let _progInterval;
function animateProgress() {
  let val = 0;
  progressBar.style.width = "0%";
  clearInterval(_progInterval);
  _progInterval = setInterval(() => {
    val = Math.min(val + Math.random() * 8, 90);
    progressBar.style.width = val + "%";
  }, 300);
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

// ─── Start ──────────────────────────────────────────────────────────────────
init();
