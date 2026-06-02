"""
ID Photo Pro — Backend (Pillow only, không cần rembg)
Xóa nền đã chuyển sang Frontend (WebAssembly)
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import io, base64, logging
from PIL import Image, ImageFilter, ImageEnhance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ID Photo Pro API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PHOTO_SIZES = {
    "the_3x4":       {"width_mm": 30,  "height_mm": 40,  "label": "Ảnh thẻ 3×4 cm",     "px_w": 354,  "px_h": 472},
    "the_4x6":       {"width_mm": 40,  "height_mm": 60,  "label": "Ảnh thẻ 4×6 cm",     "px_w": 472,  "px_h": 709},
    "the_2x3":       {"width_mm": 20,  "height_mm": 30,  "label": "Ảnh thẻ 2×3 cm",     "px_w": 236,  "px_h": 354},
    "chung_minh":    {"width_mm": 22,  "height_mm": 28,  "label": "CCCD/CMND",           "px_w": 260,  "px_h": 330},
    "passport":      {"width_mm": 35,  "height_mm": 45,  "label": "Hộ chiếu 35×45mm",   "px_w": 413,  "px_h": 531},
    "visa_us":       {"width_mm": 51,  "height_mm": 51,  "label": "Visa Mỹ 2×2 inch",   "px_w": 600,  "px_h": 600},
    "visa_schengen": {"width_mm": 35,  "height_mm": 45,  "label": "Visa Schengen",       "px_w": 413,  "px_h": 531},
}

BG_COLORS = {
    "white":       (255, 255, 255),
    "blue":        (67,  114, 196),
    "light_blue":  (173, 216, 230),
    "red":         (204,  0,   0),
    "gray":        (200, 200, 200),
}

def bytes_to_pil(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")

def pil_to_bytes(img: Image.Image, fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=95)
    return buf.getvalue()

def add_background(img: Image.Image, color: tuple) -> Image.Image:
    """Ghép màu nền vào ảnh RGBA (đã xóa nền từ frontend)"""
    bg = Image.new("RGBA", img.size, color + (255,))
    bg.paste(img, mask=img.split()[3])
    return bg.convert("RGB")

def resize_and_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_ratio = img.width / img.height
    tgt_ratio = target_w / target_h
    if src_ratio > tgt_ratio:
        new_h = target_h
        new_w = int(img.width * target_h / img.height)
    else:
        new_w = target_w
        new_h = int(img.height * target_w / img.width)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

def enhance_portrait(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=3))
    return img

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "pillow-only", "rembg": False}

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": PHOTO_SIZES}

@app.post("/api/process")
async def process_photo(
    file:       UploadFile = File(...),
    size_key:   str  = Form("the_3x4"),
    bg_color:   str  = Form("white"),
    enhance:    bool = Form(True),
    output_fmt: str  = Form("jpeg"),
):
    """
    Nhận ảnh đã xóa nền (RGBA PNG từ frontend WebAssembly)
    → thêm màu nền → resize/crop chuẩn kích thước → trả về base64
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    size_info = PHOTO_SIZES.get(size_key)
    if not size_info:
        raise HTTPException(400, f"Kích thước không hợp lệ: {size_key}")

    color_rgb = BG_COLORS.get(bg_color, (255, 255, 255))
    fmt = "jpeg" if output_fmt.lower() == "jpeg" else "png"

    try:
        data = await file.read()
        img  = bytes_to_pil(data)
        logger.info(f"process: {file.filename} {img.size} → {size_key} bg={bg_color}")

        # Thêm màu nền (ảnh đã được xóa nền ở frontend)
        img_with_bg = add_background(img, color_rgb)

        # Resize + crop chuẩn kích thước
        img_resized = resize_and_crop(img_with_bg, size_info["px_w"], size_info["px_h"])

        # Enhance
        if enhance:
            img_resized = enhance_portrait(img_resized)

        out = pil_to_bytes(img_resized, fmt.upper())
        b64 = base64.b64encode(out).decode()

        return JSONResponse({
            "success":    True,
            "image_b64":  b64,
            "format":     fmt,
            "width":      size_info["px_w"],
            "height":     size_info["px_h"],
            "size_label": size_info["label"],
        })

    except Exception as e:
        logger.error(f"process error: {e}")
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
