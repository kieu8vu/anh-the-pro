"""
ID Photo Pro — Backend v3.0
════════════════════════════════════════════════════════════════════
Pipeline xử lý ảnh thẻ chuẩn ICAO 9303:

  POST /api/process
    Input:  ảnh chân dung (jpg/png) + tham số kích thước, màu nền
    Output: ảnh thẻ đã:
      1. Nhận diện khuôn mặt (OpenCV Haar + DNN nếu có)
      2. Tính toán crop chuẩn ICAO (IED-based)
      3. Xoay thẳng mặt nếu nghiêng
      4. Xoá nền (rembg AI nếu có, GrabCut nếu không)
      5. Thêm màu nền chuẩn
      6. Enhance: brightness, contrast, sharpen, color
      7. Resize đúng kích thước ảnh thẻ (px @ 300dpi)

  GET  /health    — health check
  GET  /api/sizes — danh sách kích thước
════════════════════════════════════════════════════════════════════
"""

import io, base64, logging, math
from typing import Optional, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageEnhance, ImageFilter

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ── rembg (optional — tải model lần đầu ~4MB, nhẹ hơn u2net) ───────────────
REMBG_AVAILABLE = False
REMBG_SESSION   = None
try:
    from rembg import remove as rembg_remove, new_session
    # u2netp là model nhỏ nhất (~4MB), đủ dùng cho ảnh thẻ
    REMBG_SESSION   = new_session("u2netp")
    REMBG_AVAILABLE = True
    logger.info("✓ rembg u2netp loaded")
except Exception as e:
    logger.warning(f"rembg unavailable ({e}) — dùng GrabCut fallback")

# ── OpenCV Haar Cascades ─────────────────────────────────────────────────────
_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_EYE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_eye.xml"
)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="ID Photo Pro API v3", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Tiêu chuẩn ảnh thẻ (ICAO 9303) ─────────────────────────────────────────
# head_ratio: tỷ lệ chiều cao đầu (đỉnh→cằm) / chiều cao ảnh
# eye_line:   vị trí đường mắt từ trên (0.0–1.0)
PHOTO_SIZES = {
    # Việt Nam
    "vn_cccd":       {"W":22,"H":28,"px_w":260,"px_h":330, "label":"CCCD/CMND",           "head_ratio":0.72,"eye_line":0.42},
    "the_3x4":       {"W":30,"H":40,"px_w":354,"px_h":472, "label":"Ảnh thẻ 3×4 cm",      "head_ratio":0.72,"eye_line":0.42},
    "the_4x6":       {"W":40,"H":60,"px_w":472,"px_h":709, "label":"Ảnh thẻ 4×6 cm",      "head_ratio":0.72,"eye_line":0.42},
    "the_2x3":       {"W":20,"H":30,"px_w":236,"px_h":354, "label":"Ảnh thẻ 2×3 cm",      "head_ratio":0.72,"eye_line":0.42},
    "chung_minh":    {"W":22,"H":28,"px_w":260,"px_h":330, "label":"CCCD/CMND",           "head_ratio":0.72,"eye_line":0.42},
    "passport":      {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Hộ chiếu 35×45mm",   "head_ratio":0.75,"eye_line":0.43},
    # Quốc tế
    "visa_us":       {"W":51,"H":51,"px_w":600,"px_h":600, "label":"Visa Mỹ 2×2 inch",   "head_ratio":0.65,"eye_line":0.40},
    "uk_passport":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"UK Passport",         "head_ratio":0.75,"eye_line":0.43},
    "eu_schengen":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"EU / Schengen",       "head_ratio":0.76,"eye_line":0.44},
    "ca_passport":   {"W":50,"H":70,"px_w":591,"px_h":827, "label":"Canada",              "head_ratio":0.68,"eye_line":0.40},
    "jp_passport":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Nhật Bản",            "head_ratio":0.75,"eye_line":0.43},
    "kr_passport":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Hàn Quốc",            "head_ratio":0.75,"eye_line":0.43},
    "au_passport":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Úc",                  "head_ratio":0.75,"eye_line":0.43},
    "cn_visa":       {"W":33,"H":48,"px_w":390,"px_h":567, "label":"Visa Trung Quốc",     "head_ratio":0.72,"eye_line":0.43},
    "in_passport":   {"W":51,"H":51,"px_w":600,"px_h":600, "label":"Ấn Độ",               "head_ratio":0.65,"eye_line":0.40},
    "sg_passport":   {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Singapore",           "head_ratio":0.75,"eye_line":0.43},
    "visa_schengen": {"W":35,"H":45,"px_w":413,"px_h":531, "label":"Visa Schengen",       "head_ratio":0.76,"eye_line":0.44},
}

BG_COLORS = {
    "white":      (255, 255, 255),
    "light_grey": (240, 240, 240),
    "light_blue": (214, 228, 240),
    "blue":       (67,  114, 196),
    "red":        (204,  0,   0),
    "gray":       (200, 200, 200),
}

# ════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def pil_to_cv(img: Image.Image) -> np.ndarray:
    """PIL → OpenCV BGR"""
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)

def cv_to_pil(img: np.ndarray) -> Image.Image:
    """OpenCV BGR → PIL RGB"""
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

def pil_to_bytes(img: Image.Image, fmt: str = "JPEG", quality: int = 95) -> bytes:
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=quality, dpi=(300, 300))
    return buf.getvalue()

# ── 1. NHẬN DIỆN KHUÔN MẶT ──────────────────────────────────────────────────

def detect_face(img_np: np.ndarray) -> Optional[dict]:
    """
    Phát hiện khuôn mặt bằng OpenCV Haar Cascade.
    Trả về dict: {x, y, w, h, cx, cy, eyes: [(ex,ey),...]}
    Hoặc None nếu không tìm thấy.
    """
    gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    # Thử nhiều scale để tăng độ nhạy
    for scale, neighbors in [(1.05, 3), (1.1, 4), (1.15, 5)]:
        faces = _FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=scale,
            minNeighbors=neighbors,
            minSize=(max(30, img_np.shape[1]//20), max(30, img_np.shape[0]//20)),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        if len(faces) > 0:
            break

    if len(faces) == 0:
        logger.warning("Không phát hiện khuôn mặt — dùng crop giữa")
        return None

    # Lấy khuôn mặt lớn nhất
    face = max(faces, key=lambda f: f[2] * f[3])
    x, y, w, h = map(int, face)

    # Phát hiện mắt trong vùng khuôn mặt (nửa trên)
    face_roi = gray[y : y + h // 2, x : x + w]
    eyes = _EYE_CASCADE.detectMultiScale(face_roi, 1.1, 5, minSize=(10, 10))
    eyes_coords = [(int(x + ex + ew // 2), int(y + ey + eh // 2)) for ex, ey, ew, eh in eyes]

    return {
        "x": x, "y": y, "w": w, "h": h,
        "cx": x + w // 2,
        "cy": y + h // 2,
        "eyes": eyes_coords[:2],  # tối đa 2 mắt
    }

# ── 2. TÍNH TOÁN CROP CHUẨN ICAO ────────────────────────────────────────────

def compute_crop(
    img_h: int,
    img_w: int,
    face: dict,
    target_w: int,
    target_h: int,
    head_ratio: float,
    eye_line: float,
) -> Tuple[int, int, int, int]:
    """
    Tính vùng crop dựa trên khuôn mặt, theo chuẩn ICAO.

    Nguyên tắc IED (Inter-Eye Distance):
      - IED ≈ face_width × 0.45
      - faceH (mắt→cằm)  = IED × 1.8
      - headH (đỉnh→cằm) = faceH × 1.55
      - cropH = headH / head_ratio
      - cropTop = eye_y - eye_line × cropH
      - cropLeft = face_center_x - cropW / 2
    """
    fx, fy, fw, fh = face["x"], face["y"], face["w"], face["h"]

    # Ước lượng IED từ chiều rộng khuôn mặt
    ied = fw * 0.45

    # Nếu có 2 mắt → tính IED chính xác hơn
    if len(face["eyes"]) == 2:
        e1, e2 = face["eyes"]
        ied = math.sqrt((e2[0]-e1[0])**2 + (e2[1]-e1[1])**2)

    face_h_eye2chin = ied * 1.8
    head_h = face_h_eye2chin * 1.55
    crop_h = head_h / head_ratio
    crop_w = crop_h * (target_w / target_h)

    # Vị trí mắt: nếu có 2 mắt dùng chính xác, không thì ước lượng
    if len(face["eyes"]) >= 2:
        eye_y = (face["eyes"][0][1] + face["eyes"][1][1]) / 2
        eye_x = (face["eyes"][0][0] + face["eyes"][1][0]) / 2
    else:
        eye_y = fy + fh * 0.35
        eye_x = float(face["cx"])

    crop_top  = eye_y - eye_line * crop_h
    crop_left = eye_x - crop_w / 2

    # Clamp trong ảnh
    crop_left = max(0.0, min(crop_left, img_w - crop_w))
    crop_top  = max(0.0, min(crop_top,  img_h - crop_h))
    crop_w    = min(crop_w, float(img_w))
    crop_h    = min(crop_h, float(img_h))

    return int(crop_left), int(crop_top), int(crop_w), int(crop_h)

# ── 3. XOÁ NỀN ───────────────────────────────────────────────────────────────

def remove_background_rembg(pil_img: Image.Image) -> Image.Image:
    """Xoá nền bằng rembg AI (u2netp)."""
    img_bytes = pil_to_bytes(pil_img, "PNG")
    result    = rembg_remove(img_bytes, session=REMBG_SESSION)
    return Image.open(io.BytesIO(result)).convert("RGBA")

def remove_background_grabcut(
    img_np: np.ndarray,
    face: Optional[dict],
) -> np.ndarray:
    """
    Xoá nền bằng GrabCut.
    Dùng bounding box khuôn mặt để xác định vùng foreground.
    Trả về mask uint8 (255=foreground, 0=background).
    """
    h, w = img_np.shape[:2]

    if face is None:
        # Không có mặt → dùng vùng trung tâm 70%
        mx, my = int(w * 0.15), int(h * 0.05)
        mw, mh = int(w * 0.70), int(h * 0.90)
    else:
        fx, fy, fw, fh = face["x"], face["y"], face["w"], face["h"]
        pad_x = int(fw * 1.2)
        pad_top = int(fh * 0.4)
        pad_bot = int(fh * 3.5)
        mx = max(0, fx - pad_x)
        my = max(0, fy - pad_top)
        mw = min(w - mx, fw + 2 * pad_x)
        mh = min(h - my, fh + pad_top + pad_bot)

    mask   = np.zeros((h, w), np.uint8)
    bgd    = np.zeros((1, 65), np.float64)
    fgd    = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(img_np, mask, (mx, my, mw, mh), bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
        fg_mask = np.where((mask == 2) | (mask == 0), 0, 255).astype(np.uint8)
    except Exception as e:
        logger.warning(f"GrabCut failed: {e} — fallback to rect mask")
        fg_mask = np.zeros((h, w), np.uint8)
        fg_mask[my : my + mh, mx : mx + mw] = 255

    # Làm mịn cạnh
    kernel  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    fg_mask = cv2.GaussianBlur(fg_mask, (5, 5), 0)
    _, fg_mask = cv2.threshold(fg_mask, 127, 255, cv2.THRESH_BINARY)

    return fg_mask

def apply_new_background(
    img_np: np.ndarray,
    mask: np.ndarray,
    bg_color: Tuple[int, int, int],
) -> np.ndarray:
    """Thay nền bằng màu mới dùng mask."""
    result = img_np.copy()
    bg_color_bgr = (bg_color[2], bg_color[1], bg_color[0])
    bg = np.full_like(img_np, bg_color_bgr)
    mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    # Blend
    result = np.where(mask3 == 255, result, bg)
    return result

def remove_bg_and_add_color(
    pil_img: Image.Image,
    face: Optional[dict],
    bg_color: Tuple[int, int, int],
) -> Image.Image:
    """
    Xoá nền và thêm màu nền mới.
    Dùng rembg nếu có, không thì GrabCut.
    """
    if REMBG_AVAILABLE:
        try:
            img_nobg = remove_background_rembg(pil_img)  # RGBA
            # Thêm màu nền
            bg = Image.new("RGBA", img_nobg.size, bg_color + (255,))
            bg.paste(img_nobg, mask=img_nobg.split()[3])
            return bg.convert("RGB")
        except Exception as e:
            logger.warning(f"rembg failed: {e} — using GrabCut")

    # Fallback: GrabCut
    img_cv  = pil_to_cv(pil_img)
    fg_mask = remove_background_grabcut(img_cv, face)
    result  = apply_new_background(img_cv, fg_mask, bg_color)
    return cv_to_pil(result)

# ── 4. XOAY THẲNG MẶT ────────────────────────────────────────────────────────

def rotate_to_align(img_np: np.ndarray, face: dict) -> np.ndarray:
    """
    Xoay ảnh để khuôn mặt thẳng dựa trên góc 2 mắt.
    Chỉ xoay nếu có ít nhất 2 mắt được phát hiện.
    """
    eyes = face.get("eyes", [])
    if len(eyes) < 2:
        return img_np

    e1, e2 = sorted(eyes, key=lambda e: e[0])  # trái → phải
    dx, dy = e2[0] - e1[0], e2[1] - e1[1]
    angle  = math.degrees(math.atan2(dy, dx))

    if abs(angle) < 0.5:
        return img_np

    # Xoay quanh điểm giữa 2 mắt
    cx = (e1[0] + e2[0]) / 2
    cy = (e1[1] + e2[1]) / 2
    h, w = img_np.shape[:2]
    M   = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    rotated = cv2.warpAffine(
        img_np, M, (w, h),
        flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    logger.info(f"Rotated {angle:.1f}°")
    return rotated

# ── 5. ENHANCE CHẤT LƯỢNG ─────────────────────────────────────────────────────

def enhance_portrait(img: Image.Image) -> Image.Image:
    """
    Cải thiện chất lượng ảnh thẻ chuyên nghiệp:
    - Độ sáng nhẹ
    - Contrast tốt hơn
    - Sharpen khuôn mặt
    - Màu sắc tự nhiên
    - Noise reduction nhẹ
    """
    # Giảm noise trước khi sharpen
    img_cv = pil_to_cv(img)
    img_cv = cv2.bilateralFilter(img_cv, d=5, sigmaColor=40, sigmaSpace=40)
    img = cv_to_pil(img_cv)

    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3))
    return img

# ── 6. CROP & RESIZE ───────────────────────────────────────────────────────────

def crop_and_resize(
    img: Image.Image,
    crop_left: int,
    crop_top: int,
    crop_w: int,
    crop_h: int,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """Crop ảnh và resize lên kích thước ảnh thẻ chuẩn."""
    cropped = img.crop((crop_left, crop_top, crop_left + crop_w, crop_top + crop_h))
    return cropped.resize((target_w, target_h), Image.LANCZOS)

def center_crop_no_face(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Crop giữa ảnh khi không phát hiện khuôn mặt."""
    ratio = target_w / target_h
    iw, ih = img.size
    if iw / ih > ratio:
        new_w = int(ih * ratio)
        left = (iw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ih))
    else:
        new_h = int(iw / ratio)
        top = (ih - new_h) // 2
        img = img.crop((0, top, iw, top + new_h))
    return img.resize((target_w, target_h), Image.LANCZOS)

# ════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":  "ok",
        "rembg":   REMBG_AVAILABLE,
        "opencv":  cv2.__version__,
        "version": "3.0.0",
        "mode":    "rembg+opencv" if REMBG_AVAILABLE else "opencv-grabcut",
    }

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": PHOTO_SIZES}

@app.post("/api/process")
async def process_photo(
    file:        UploadFile = File(...),
    size_key:    str  = Form("the_3x4"),
    bg_color:    str  = Form("white"),
    bg_hex:      str  = Form(""),        # Hex tuỳ chỉnh từ frontend
    enhance:     bool = Form(True),
    output_fmt:  str  = Form("jpeg"),
    head_ratio:  float = Form(0.0),      # Override từ frontend (0=dùng default)
    eye_line:    float = Form(0.0),
):
    """
    Pipeline xử lý ảnh thẻ đầy đủ:
    1. Nhận diện khuôn mặt (OpenCV)
    2. Xoay thẳng nếu nghiêng
    3. Crop chuẩn ICAO (IED-based)
    4. Xoá nền (rembg AI hoặc GrabCut)
    5. Thêm màu nền
    6. Enhance chất lượng
    7. Resize & export
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    size_info = PHOTO_SIZES.get(size_key)
    if not size_info:
        # Fallback về passport nếu key không hợp lệ
        size_info = PHOTO_SIZES["passport"]
        logger.warning(f"Unknown size_key '{size_key}', using passport")

    # Màu nền
    if bg_hex and bg_hex.startswith("#") and len(bg_hex) == 7:
        # Hex tuỳ chỉnh từ frontend
        try:
            r = int(bg_hex[1:3], 16)
            g = int(bg_hex[3:5], 16)
            b = int(bg_hex[5:7], 16)
            bg_rgb = (r, g, b)
        except:
            bg_rgb = BG_COLORS.get(bg_color, (255, 255, 255))
    else:
        bg_rgb = BG_COLORS.get(bg_color, (255, 255, 255))

    # Tỷ lệ ICAO
    hr = head_ratio if head_ratio > 0 else size_info.get("head_ratio", 0.72)
    el = eye_line   if eye_line   > 0 else size_info.get("eye_line",   0.42)
    target_w = size_info["px_w"]
    target_h = size_info["px_h"]
    fmt = "jpeg" if output_fmt.lower() == "jpeg" else "png"

    try:
        data = await file.read()
        pil_img = Image.open(io.BytesIO(data)).convert("RGB")

        # Giới hạn kích thước input (tránh OOM)
        MAX_DIM = 2400
        if max(pil_img.size) > MAX_DIM:
            ratio = MAX_DIM / max(pil_img.size)
            new_size = (int(pil_img.width * ratio), int(pil_img.height * ratio))
            pil_img = pil_img.resize(new_size, Image.LANCZOS)

        img_cv = pil_to_cv(pil_img)
        ih, iw = img_cv.shape[:2]

        logger.info(f"process: {iw}×{ih} → {size_key} bg={bg_color} hr={hr:.2f} el={el:.2f}")

        # ── BƯỚC 1: Nhận diện khuôn mặt ─────────────────────────────────
        face = detect_face(img_cv)
        face_detected = face is not None

        # ── BƯỚC 2: Xoay thẳng mặt ──────────────────────────────────────
        if face_detected:
            img_cv = rotate_to_align(img_cv, face)
            # Detect lại sau khi xoay
            face = detect_face(img_cv) or face
            pil_img = cv_to_pil(img_cv)

        # ── BƯỚC 3: Xoá nền + thêm màu nền ─────────────────────────────
        img_with_bg = remove_bg_and_add_color(pil_img, face, bg_rgb)
        img_cv_bg   = pil_to_cv(img_with_bg)

        # ── BƯỚC 4: Crop chuẩn ICAO ──────────────────────────────────────
        if face_detected:
            cl, ct, cw, ch = compute_crop(ih, iw, face, target_w, target_h, hr, el)
            result = crop_and_resize(img_with_bg, cl, ct, cw, ch, target_w, target_h)
        else:
            result = center_crop_no_face(img_with_bg, target_w, target_h)

        # ── BƯỚC 5: Enhance ──────────────────────────────────────────────
        if enhance:
            result = enhance_portrait(result)

        # ── BƯỚC 6: Export ───────────────────────────────────────────────
        out_bytes = pil_to_bytes(result, fmt.upper())
        b64 = base64.b64encode(out_bytes).decode()

        return JSONResponse({
            "success":       True,
            "image_b64":     b64,
            "format":        fmt,
            "width":         target_w,
            "height":        target_h,
            "size_label":    size_info["label"],
            "face_detected": face_detected,
            "rembg_used":    REMBG_AVAILABLE,
            "mode":          "rembg" if REMBG_AVAILABLE else "grabcut",
        })

    except Exception as e:
        logger.error(f"process error: {e}", exc_info=True)
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
