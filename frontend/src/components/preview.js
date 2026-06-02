// src/components/preview.js — Xem trước & Xuất ảnh
// ==================================================

import { createPrintSheet, downloadDataURL, b64ToBlob } from "../utils/canvas.js";

export class PhotoPreview {
  constructor(containerEl) {
    this.container = containerEl;
    this.result    = null; // { image_b64, format, width, height, size_label }
  }

  show(result, sizeInfo) {
    this.result   = result;
    this.sizeInfo = sizeInfo;

    const mimeType = result.format === "png" ? "image/png" : "image/jpeg";
    const dataURL  = `data:${mimeType};base64,${result.image_b64}`;

    this.container.innerHTML = `
      <div class="preview-panel">
        <h3 class="preview-title">✅ Ảnh đã xử lý — ${result.size_label}</h3>

        <div class="preview-grid">
          <div class="preview-single">
            <img src="${dataURL}" alt="Ảnh thẻ đã xử lý" class="preview-img" />
            <p class="preview-meta">${result.width} × ${result.height} px</p>
          </div>

          <div class="preview-sheet" id="print-sheet-preview">
            <p class="preview-meta">Tờ in đang tạo…</p>
          </div>
        </div>

        <div class="preview-actions">
          <button id="btn-dl-single" class="btn primary">
            ⬇ Tải ảnh đơn (.${result.format})
          </button>
          <button id="btn-dl-sheet" class="btn secondary">
            🖨 Tải tờ in (4×5)
          </button>
          <button id="btn-redo" class="btn ghost">
            ↩ Làm lại
          </button>
        </div>
      </div>
    `;

    // Tải ảnh đơn
    this.container.querySelector("#btn-dl-single").addEventListener("click", () => {
      downloadDataURL(dataURL, `anh-the.${result.format}`);
    });

    // Tải tờ in
    this.container.querySelector("#btn-dl-sheet").addEventListener("click", async () => {
      const sheetURL = await createPrintSheet(dataURL, sizeInfo, 4, 5);
      downloadDataURL(sheetURL, "to-in-anh-the.jpg");
    });

    // Làm lại
    this.container.querySelector("#btn-redo").addEventListener("click", () => {
      this.hide();
      document.dispatchEvent(new CustomEvent("resetApp"));
    });

    // Tạo tờ in preview
    this._renderSheetPreview(dataURL, sizeInfo);
  }

  async _renderSheetPreview(dataURL, sizeInfo) {
    const el = this.container.querySelector("#print-sheet-preview");
    try {
      const sheetURL = await createPrintSheet(dataURL, sizeInfo, 4, 5);
      el.innerHTML = `
        <img src="${sheetURL}" alt="Tờ in 4×5 ảnh" class="preview-sheet-img" />
        <p class="preview-meta">Tờ in 4×5 ảnh</p>
      `;
    } catch {
      el.innerHTML = `<p class="preview-meta preview-error">Không tạo được tờ in</p>`;
    }
  }

  hide() {
    this.container.innerHTML = "";
    this.result = null;
  }
}
