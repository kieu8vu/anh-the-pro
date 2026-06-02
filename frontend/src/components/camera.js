// src/components/camera.js — Webcam Capture Component
// =====================================================

export class CameraCapture {
  constructor(containerEl, onCapture) {
    this.container = containerEl;
    this.onCapture = onCapture; // callback(blob)
    this.stream    = null;
    this._build();
  }

  _build() {
    this.container.innerHTML = `
      <div class="camera-wrapper">
        <video id="cam-video" autoplay playsinline muted></video>
        <canvas id="cam-canvas" style="display:none"></canvas>

        <!-- Khung guide -->
        <div class="cam-guide">
          <div class="cam-oval"></div>
          <p class="cam-hint">Nhìn thẳng vào camera · Đủ sáng · Không đội mũ</p>
        </div>

        <div class="cam-controls">
          <button id="btn-flip"    class="cam-btn secondary" title="Đổi camera">↺</button>
          <button id="btn-capture" class="cam-btn primary">📷 Chụp</button>
          <button id="btn-close"   class="cam-btn secondary" title="Đóng">✕</button>
        </div>
      </div>
    `;

    this.video    = this.container.querySelector("#cam-video");
    this.canvas   = this.container.querySelector("#cam-canvas");
    this.btnFlip  = this.container.querySelector("#btn-flip");
    this.btnCap   = this.container.querySelector("#btn-capture");
    this.btnClose = this.container.querySelector("#btn-close");

    this.facingMode = "user"; // selfie by default

    this.btnCap.addEventListener("click",  () => this._capture());
    this.btnFlip.addEventListener("click", () => this._flipCamera());
    this.btnClose.addEventListener("click",() => this.stop());
  }

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: this.facingMode,
          width:  { ideal: 1280 },
          height: { ideal: 720  },
        },
        audio: false,
      });
      this.video.srcObject = this.stream;
      this.container.style.display = "block";
    } catch (err) {
      alert("Không truy cập được camera: " + err.message);
    }
  }

  stop() {
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
    this.container.style.display = "none";
  }

  async _flipCamera() {
    this.facingMode = this.facingMode === "user" ? "environment" : "user";
    this.stop();
    this.container.style.display = "block";
    await this.start();
  }

  _capture() {
    const v = this.video;
    this.canvas.width  = v.videoWidth;
    this.canvas.height = v.videoHeight;
    this.canvas.getContext("2d").drawImage(v, 0, 0);

    this.canvas.toBlob(blob => {
      this.stop();
      this.onCapture(blob);
    }, "image/jpeg", 0.95);
  }
}
