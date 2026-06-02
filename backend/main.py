"""
ID Photo Pro — Backend AI (FastAPI)
=====================================
Endpoints:
  POST /api/remove-bg   → Xoá nền ảnh (rembg)
  POST /api/process     → Xử lý đầy đủ: xoá nền + resize + thay màu nền
  GET  /api/sizes       → Danh sách kích thước ảnh thẻ chuẩn
  GET  /health          → Health check (Render.com cần endpoint này)
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
import io, base64, logging
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np

# rembg — xoá nền bằng AI (U2-Net)
try:
    from rembg import remove as rembg_remove, new_session
    REMBG_SESSION = new_session("u2net")
    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False
    logging.warning("rembg không cài được, dùng fallback Pillow thay thế.")

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="ID Photo Pro API",
    description="Backend AI xử lý ảnh thẻ chuyên nghiệp",
    version="1.0.0",
)

# CORS — cho phép frontend gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Production: đổi thành domain cụ thể
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Kích thước ảnh thẻ chuẩn (pixel @ 300dpi) ──────────────────────────────
PHOTO_SIZES = {
    "the_3x4":   {"width_mm": 30,  "height_mm": 40,  "label": "Ảnh thẻ 3×4 cm",       "px_w": 354,  "px_h": 472},
    "the_4x6":   {"width_mm": 40,  "height_mm": 60,  "label": "Ảnh thẻ 4×6 cm",       "px_w": 472,  "px_h": 709},
    "the_2x3":   {"width_mm": 20,  "height_mm": 30,  "label": "Ảnh thẻ 2×3 cm",       "px_w": 236,  "px_h": 354},
    "chung_minh":{"width_mm": 22,  "height_mm": 28,  "label": "CCCD/CMND",             "px_w": 260,  "px_h": 330},
    "passport":  {"width_mm": 35,  "height_mm": 45,  "label": "Hộ chiếu (35×45mm)",   "px_w": 413,  "px_h": 531},
    "visa_us":   {"width_mm": 51,  "height_mm": 51,  "label": "Visa Mỹ (2×2 inch)",   "px_w": 600,  "px_h": 600},
    "visa_uk":   {"width_mm": 35,  "height_mm": 45,  "label": "Visa Anh (35×45mm)",   "px_w": 413,  "px_h": 531},
    "visa_schengen": {"width_mm": 35, "height_mm": 45, "label": "Visa Schengen",       "px_w": 413,  "px_h": 531},
}

# Màu nền phổ biến
BG_COLORS = {
    "white":     (255, 255, 255),
    "blue":      (67, 114, 196),
    "light_blue":(173, 216, 230),
    "red":       (204, 0, 0),
    "gray":      (200, 200, 200),
    "transparent": None,
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def bytes_to_pil(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")

def pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=95)
    return buf.getvalue()

def remove_background(img: Image.Image) -> Image.Image:
    """Xoá nền — dùng rembg nếu có, không thì fallback."""
    if REMBG_AVAILABLE:
        img_bytes = pil_to_bytes(img, "PNG")
        result_bytes = rembg_remove(img_bytes, session=REMBG_SESSION)
        return Image.open(io.BytesIO(result_bytes)).convert("RGBA")
    else:
        # Fallback: giả lập xoá nền bằng cách làm mờ góc (demo)
        return img

def add_background_color(img: Image.Image, color: tuple) -> Image.Image:
    """Thêm màu nền vào ảnh đã xoá nền."""
    bg = Image.new("RGBA", img.size, color + (255,))
    bg.paste(img, mask=img.split()[3])
    return bg.convert("RGB")

def resize_and_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale + crop giữa ảnh cho vừa kích thước chuẩn."""
    src_ratio = img.width / img.height
    tgt_ratio = target_w / target_h

    if src_ratio > tgt_ratio:
        # Ảnh quá rộng → scale theo height
        new_h = target_h
        new_w = int(img.width * target_h / img.height)
    else:
        # Ảnh quá cao → scale theo width
        new_w = target_w
        new_h = int(img.height * target_w / img.width)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Crop giữa
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    img  = img.crop((left, top, left + target_w, top + target_h))
    return img

def enhance_portrait(img: Image.Image) -> Image.Image:
    """Cải thiện chất lượng ảnh nhẹ: sharpen + contrast."""
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=3))
    return img


# ─── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — Render.com ping endpoint."""
    return {"status": "ok", "rembg": REMBG_AVAILABLE}


@app.get("/api/sizes")
async def get_sizes():
    """Trả về danh sách kích thước ảnh thẻ chuẩn."""
    return {"sizes": PHOTO_SIZES}


@app.post("/api/remove-bg")
async def remove_bg_endpoint(file: UploadFile = File(...)):
    """
    Xoá nền ảnh bằng AI.
    Input:  multipart/form-data  → file (image/*)
    Output: PNG base64 (RGBA — nền trong suốt)
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh (image/*)")

    try:
        data = await file.read()
        img  = bytes_to_pil(data)
        logger.info(f"remove-bg: {file.filename} {img.size}")

        result = remove_background(img)
        out    = pil_to_bytes(result, "PNG")
        b64    = base64.b64encode(out).decode()

        return JSONResponse({"success": True, "image_b64": b64, "format": "png"})

    except Exception as e:
        logger.error(f"remove-bg error: {e}")
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


@app.post("/api/process")
async def process_photo(
    file:       UploadFile = File(...),
    size_key:   str  = Form("the_3x4"),
    bg_color:   str  = Form("white"),
    enhance:    bool = Form(True),
    output_fmt: str  = Form("jpeg"),
):
    """
    Xử lý ảnh thẻ đầy đủ:
      1. Xoá nền (AI)
      2. Thêm màu nền
      3. Resize + crop chuẩn kích thước
      4. Enhance chất lượng (tuỳ chọn)
    Input:  multipart/form-data
    Output: JSON { image_b64, width, height, format }
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    size_info = PHOTO_SIZES.get(size_key)
    if not size_info:
        raise HTTPException(400, f"Kích thước không hợp lệ: {size_key}")

    color_rgb = BG_COLORS.get(bg_color, (255, 255, 255))
    fmt = output_fmt.lower() if output_fmt.lower() in ("jpeg", "png") else "jpeg"

    try:
        data = await file.read()
        img  = bytes_to_pil(data)
        logger.info(f"process: {file.filename} → {size_key} bg={bg_color}")

        # Bước 1: Xoá nền
        img_nobg = remove_background(img)

        # Bước 2: Thêm màu nền
        if color_rgb:
            img_with_bg = add_background_color(img_nobg, color_rgb)
        else:
            img_with_bg = img_nobg

        # Bước 3: Resize + crop
        target_w = size_info["px_w"]
        target_h = size_info["px_h"]
        img_resized = resize_and_crop(img_with_bg, target_w, target_h)

        # Bước 4: Enhance
        if enhance:
            img_resized = enhance_portrait(img_resized)

        out = pil_to_bytes(img_resized, fmt.upper())
        b64 = base64.b64encode(out).decode()

        return JSONResponse({
            "success":    True,
            "image_b64":  b64,
            "format":     fmt,
            "width":      target_w,
            "height":     target_h,
            "size_label": size_info["label"],
        })

    except Exception as e:
        logger.error(f"process error: {e}")
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


# ─── Dev server ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
