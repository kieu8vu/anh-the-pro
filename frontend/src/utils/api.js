// src/utils/api.js — Gọi Backend API
// ============================================================

// ⚠️  Đổi URL này thành domain Render.com của bạn sau Bước 3
const API_BASE = window.API_BASE || "https://anh-the-pro-api.onrender.com";

/**
 * Lấy danh sách kích thước ảnh thẻ chuẩn từ server
 */
export async function fetchSizes() {
  const res = await fetch(`${API_BASE}/api/sizes`);
  if (!res.ok) throw new Error("Không lấy được danh sách kích thước");
  return res.json();
}

/**
 * Xoá nền ảnh (AI)
 * @param {File|Blob} imageFile
 * @returns {{ image_b64: string, format: string }}
 */
export async function removeBackground(imageFile) {
  const form = new FormData();
  form.append("file", imageFile, "photo.png");

  const res = await fetch(`${API_BASE}/api/remove-bg`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Lỗi xoá nền");
  }
  return res.json();
}

/**
 * Xử lý ảnh thẻ đầy đủ
 * @param {File|Blob} imageFile
 * @param {Object}    options
 * @param {string}    options.sizeKey    - Mã kích thước (vd: "the_3x4")
 * @param {string}    options.bgColor    - Màu nền   (vd: "white", "blue")
 * @param {boolean}   options.enhance    - Cải thiện chất lượng
 * @param {string}    options.outputFmt  - "jpeg" | "png"
 */
export async function processPhoto(imageFile, options = {}) {
  const {
    sizeKey   = "the_3x4",
    bgColor   = "white",
    enhance   = true,
    outputFmt = "jpeg",
  } = options;

  const form = new FormData();
  form.append("file",       imageFile, "photo.png");
  form.append("size_key",   sizeKey);
  form.append("bg_color",   bgColor);
  form.append("enhance",    enhance ? "true" : "false");
  form.append("output_fmt", outputFmt);

  const res = await fetch(`${API_BASE}/api/process`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Lỗi xử lý ảnh");
  }
  return res.json();
}

/**
 * Health check
 */
export async function checkHealth() {
  const res = await fetch(`${API_BASE}/health`);
  return res.json();
}
