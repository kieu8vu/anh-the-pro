// src/utils/canvas.js — Tiện ích xử lý Canvas & ảnh phía client
// ================================================================

/**
 * Chuyển File/Blob thành Data URL (base64)
 */
export function fileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = e => resolve(e.target.result);
    reader.onerror = () => reject(new Error("Không đọc được file"));
    reader.readAsDataURL(file);
  });
}

/**
 * Chuyển Data URL thành HTMLImageElement (đã load)
 */
export function dataURLToImage(dataURL) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload  = () => resolve(img);
    img.onerror = () => reject(new Error("Ảnh không hợp lệ"));
    img.src = dataURL;
  });
}

/**
 * Chuyển base64 string thành Blob
 */
export function b64ToBlob(b64, mimeType = "image/png") {
  const binary = atob(b64);
  const arr    = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
  return new Blob([arr], { type: mimeType });
}

/**
 * Scale ảnh xuống nếu quá lớn (tối ưu upload)
 * @param {File} file        - File ảnh gốc
 * @param {number} maxPx     - Chiều dài tối đa (px)
 * @returns {Promise<Blob>}
 */
export async function downscaleIfNeeded(file, maxPx = 2000) {
  const dataURL = await fileToDataURL(file);
  const img     = await dataURLToImage(dataURL);

  const { naturalWidth: w, naturalHeight: h } = img;
  if (w <= maxPx && h <= maxPx) return file; // Không cần scale

  const ratio  = Math.min(maxPx / w, maxPx / h);
  const canvas = document.createElement("canvas");
  canvas.width  = Math.round(w * ratio);
  canvas.height = Math.round(h * ratio);
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);

  return new Promise(resolve =>
    canvas.toBlob(resolve, "image/jpeg", 0.92)
  );
}

/**
 * Tạo lưới ảnh thẻ in (N ảnh trên 1 tờ A4)
 * @param {string} imgSrc    - Data URL ảnh thẻ đã xử lý
 * @param {Object} sizeInfo  - { px_w, px_h, width_mm, height_mm }
 * @param {number} cols      - Số cột
 * @param {number} rows      - Số hàng
 * @returns {string}         - Data URL PNG của tờ in
 */
export function createPrintSheet(imgSrc, sizeInfo, cols = 4, rows = 5) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const gap    = 10; // khoảng cách px giữa các ảnh
      const margin = 20;
      const cw     = sizeInfo.px_w;
      const ch     = sizeInfo.px_h;

      const canvasW = margin * 2 + cols * cw + (cols - 1) * gap;
      const canvasH = margin * 2 + rows * ch + (rows - 1) * gap;

      const canvas = document.createElement("canvas");
      canvas.width  = canvasW;
      canvas.height = canvasH;
      const ctx = canvas.getContext("2d");

      // Nền trắng
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvasW, canvasH);

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const x = margin + c * (cw + gap);
          const y = margin + r * (ch + gap);
          ctx.drawImage(img, x, y, cw, ch);
        }
      }

      resolve(canvas.toDataURL("image/jpeg", 0.95));
    };
    img.onerror = reject;
    img.src = imgSrc;
  });
}

/**
 * Tải file xuống từ Data URL
 */
export function downloadDataURL(dataURL, filename) {
  const a = document.createElement("a");
  a.href     = dataURL;
  a.download = filename;
  a.click();
}

/**
 * Đọc Blob thành ArrayBuffer (dùng cho kiểm tra MIME)
 */
export function blobToArrayBuffer(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = e => resolve(e.target.result);
    reader.onerror = reject;
    reader.readAsArrayBuffer(blob);
  });
}
