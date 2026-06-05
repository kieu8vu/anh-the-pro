"""
ID Photo Pro — Backend v5.0
════════════════════════════════════════════════════════════════
Pipeline ĐÚNG (crop trước, xóa nền sau — hiệu quả hơn):

  1. Nhận diện khuôn mặt (OpenCV Haar)
  2. Xoay thẳng mặt (từ góc 2 mắt)
  3. ICAO Crop → ảnh nhỏ vừa kích thước ảnh thẻ
  4. Xóa nền trên ảnh đã crop (Remove.bg → rembg → GrabCut)
  5. Thêm màu nền chuẩn
  6. Enhance chất lượng
  7. Xuất JPEG + PDF A4 tờ in

So với v4: crop TRƯỚC xóa nền SAU — nhanh hơn, tiết kiệm API credit,
chất lượng tốt hơn vì ảnh nhỏ hơn khi gửi Remove.bg.
════════════════════════════════════════════════════════════════
"""

import io, base64, logging, math, os, threading
from typing import Optional, Tuple

import cv2
import numpy as np
import requests as http_requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.utils import ImageReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
REMOVE_BG_KEY = os.environ.get("REMOVE_BG_KEY", "")
MAX_INPUT_DIM  = 2000   # giới hạn ảnh input
CROP_BEFORE_BG = True   # crop trước rồi mới xóa nền

# ── rembg: load đồng bộ ngay khi startup ─────────────────────────────────────
REMBG_SESSION   = None
REMBG_AVAILABLE = False

def _load_rembg():
    global REMBG_SESSION, REMBG_AVAILABLE
    try:
        from rembg import new_session
        # Thử u2netp trước (nhỏ ~4MB), fallback u2net (~170MB)
        for model in ["u2netp", "u2net"]:
            try:
                REMBG_SESSION   = new_session(model)
                REMBG_AVAILABLE = True
                logger.info(f"✓ rembg {model} ready")
                return
            except Exception as e:
                logger.warning(f"rembg {model} failed: {e}")
    except ImportError:
        logger.warning("rembg not installed")

# Load ngay (block startup nhưng đảm bảo sẵn sàng)
_load_rembg()

# ── OpenCV ────────────────────────────────────────────────────────────────────
_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_EYE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_eye.xml"
)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="ID Photo Pro API v5", version="5.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ════════════════════════════════════════════════════════════════
# PHOTO SIZE DATABASE — ICAO 9303 + quốc tế
# ════════════════════════════════════════════════════════════════
SIZES = {
    "vn_cccd":     {"W":22,"H":28,"pw":260,"ph":330,"label":"CCCD/CMND VN",       "hr":0.72,"el":0.42},
    "the_3x4":     {"W":30,"H":40,"pw":354,"ph":472,"label":"Ảnh 3×4 cm",         "hr":0.72,"el":0.42},
    "the_4x6":     {"W":40,"H":60,"pw":472,"ph":709,"label":"Ảnh 4×6 cm",         "hr":0.72,"el":0.42},
    "the_2x3":     {"W":20,"H":30,"pw":236,"ph":354,"label":"Ảnh 2×3 cm",         "hr":0.72,"el":0.42},
    "chung_minh":  {"W":22,"H":28,"pw":260,"ph":330,"label":"CCCD/CMND",          "hr":0.72,"el":0.42},
    "passport":    {"W":35,"H":45,"pw":413,"ph":531,"label":"Hộ chiếu 35×45mm",  "hr":0.75,"el":0.43},
    "visa_us":     {"W":51,"H":51,"pw":600,"ph":600,"label":"US Passport/Visa",   "hr":0.65,"el":0.40},
    "uk_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"UK Passport",        "hr":0.75,"el":0.43},
    "eu_schengen": {"W":35,"H":45,"pw":413,"ph":531,"label":"EU/Schengen",        "hr":0.76,"el":0.44},
    "de_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Đức",                "hr":0.76,"el":0.44},
    "fr_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Pháp",               "hr":0.76,"el":0.44},
    "ca_passport": {"W":50,"H":70,"pw":591,"ph":827,"label":"Canada",             "hr":0.68,"el":0.40},
    "jp_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Nhật Bản",           "hr":0.75,"el":0.43},
    "jp_visa":     {"W":45,"H":45,"pw":531,"ph":531,"label":"Visa Nhật",          "hr":0.70,"el":0.42},
    "kr_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Hàn Quốc",           "hr":0.75,"el":0.43},
    "cn_visa":     {"W":33,"H":48,"pw":390,"ph":567,"label":"Visa TQ",            "hr":0.72,"el":0.43},
    "in_passport": {"W":51,"H":51,"pw":600,"ph":600,"label":"Ấn Độ",              "hr":0.65,"el":0.40},
    "sg_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Singapore",          "hr":0.75,"el":0.43},
    "au_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Úc",                 "hr":0.75,"el":0.43},
    "nz_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"New Zealand",        "hr":0.75,"el":0.43},
    "ae_visa":     {"W":35,"H":45,"pw":413,"ph":531,"label":"UAE/Dubai",          "hr":0.75,"el":0.43},
    "visa_schengen":{"W":35,"H":45,"pw":413,"ph":531,"label":"Visa Schengen",     "hr":0.76,"el":0.44},
}

BG_MAP = {
    "white":      (255,255,255),
    "light_grey": (240,240,240),
    "light_blue": (214,228,240),
    "blue":       (67,114,196),
    "red":        (204,0,0),
    "gray":       (200,200,200),
}

# ════════════════════════════════════════════════════════════════
# UTILS
# ════════════════════════════════════════════════════════════════
def to_cv(pil):  return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
def to_pil(cv):  return Image.fromarray(cv2.cvtColor(cv, cv2.COLOR_BGR2RGB))
def to_bytes(pil, fmt="JPEG", q=95):
    buf = io.BytesIO()
    pil.convert("RGB" if fmt.upper()=="JPEG" else "RGBA").save(buf, fmt, quality=q, dpi=(300,300))
    return buf.getvalue()

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2],16) for i in (0,2,4))

# ════════════════════════════════════════════════════════════════
# STEP 1 — NHẬN DIỆN KHUÔN MẶT
# ════════════════════════════════════════════════════════════════
def detect_face(img_np):
    """
    OpenCV Haar Cascade — phát hiện khuôn mặt và 2 mắt.
    Trả về dict hoặc None.
    """
    gray = cv2.equalizeHist(cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY))
    H, W = img_np.shape[:2]
    min_face = (max(30, W//20), max(30, H//20))

    faces = []
    for scale, nb in [(1.05,3),(1.08,4),(1.12,5)]:
        f = _FACE_CASCADE.detectMultiScale(
            gray, scale, nb,
            minSize=min_face,
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        if len(f) > 0:
            faces = f
            break

    if len(faces) == 0:
        return None

    x, y, w, h = map(int, max(faces, key=lambda f: f[2]*f[3]))

    # Phát hiện mắt trong nửa trên khuôn mặt
    roi = gray[y : y + h//2, x : x + w]
    eyes_raw = _EYE_CASCADE.detectMultiScale(roi, 1.1, 4, minSize=(10,10))
    eyes = [(int(x+ex+ew//2), int(y+ey+eh//2)) for ex,ey,ew,eh in eyes_raw][:2]

    return {"x":x, "y":y, "w":w, "h":h,
            "cx": x+w//2, "cy": y+h//2,
            "eyes": sorted(eyes, key=lambda e: e[0])}

# ════════════════════════════════════════════════════════════════
# STEP 2 — XOAY THẲNG MẶT
# ════════════════════════════════════════════════════════════════
def straighten(img_np, face):
    """Xoay ảnh để 2 mắt nằm ngang."""
    eyes = face.get("eyes", [])
    if len(eyes) < 2:
        return img_np
    e1, e2 = eyes[0], eyes[1]
    angle = math.degrees(math.atan2(e2[1]-e1[1], e2[0]-e1[0]))
    if abs(angle) < 0.3:
        return img_np
    cx = (e1[0]+e2[0])/2
    cy = (e1[1]+e2[1])/2
    H, W = img_np.shape[:2]
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    rotated = cv2.warpAffine(img_np, M, (W,H),
                             flags=cv2.INTER_LANCZOS4,
                             borderMode=cv2.BORDER_REFLECT_101)
    logger.info(f"Rotated {angle:.1f}°")
    return rotated

# ════════════════════════════════════════════════════════════════
# STEP 3 — CROP CHUẨN ICAO (IED-based)
# ════════════════════════════════════════════════════════════════
def icao_crop(pil_img, face, tw, th, hr, el):
    """
    Crop ảnh theo chuẩn ICAO dùng Inter-Eye Distance.

    IED → head height → crop height → đặt mắt đúng vị trí el% từ trên.
    Nếu không phát hiện mặt → crop giữa với đúng tỷ lệ.
    """
    iw, ih = pil_img.size

    if face is None:
        # Crop giữa
        ratio = tw/th
        if iw/ih > ratio:
            nw = int(ih*ratio)
            pil_img = pil_img.crop(((iw-nw)//2, 0, (iw+nw)//2, ih))
        else:
            nh = int(iw/ratio)
            pil_img = pil_img.crop((0, (ih-nh)//2, iw, (ih+nh)//2))
        return pil_img.resize((tw, th), Image.LANCZOS)

    fx, fy, fw, fh = face["x"], face["y"], face["w"], face["h"]
    eyes = face.get("eyes", [])

    # Tính IED
    if len(eyes) >= 2:
        e1, e2 = eyes[0], eyes[1]
        ied   = math.hypot(e2[0]-e1[0], e2[1]-e1[1])
        eye_y = (e1[1]+e2[1]) / 2
        eye_x = (e1[0]+e2[0]) / 2
    else:
        ied   = fw * 0.45
        eye_y = fy + fh * 0.35
        eye_x = float(face["cx"])

    # head height = IED×1.8 (mắt→cằm) × 1.55 (thêm trán+tóc)
    head_h = ied * 1.8 * 1.55
    crop_h = head_h / hr
    crop_w = crop_h * (tw / th)

    crop_top  = eye_y - el * crop_h
    crop_left = eye_x - crop_w / 2

    # Clamp
    crop_left = max(0.0, min(crop_left, iw - crop_w))
    crop_top  = max(0.0, min(crop_top,  ih - crop_h))
    crop_w = min(crop_w, float(iw))
    crop_h = min(crop_h, float(ih))

    box = (int(crop_left), int(crop_top),
           int(crop_left+crop_w), int(crop_top+crop_h))
    return pil_img.crop(box).resize((tw, th), Image.LANCZOS)

# ════════════════════════════════════════════════════════════════
# STEP 4 — XÓA NỀN (3 tầng fallback)
# ════════════════════════════════════════════════════════════════
def bg_remove_via_api(pil_img):
    """Remove.bg API — chất lượng cao nhất, dùng credit."""
    if not REMOVE_BG_KEY:
        return None
    try:
        buf = io.BytesIO()
        pil_img.save(buf, "PNG")
        buf.seek(0)
        resp = http_requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={"image_file": ("photo.png", buf, "image/png")},
            data={"size": "auto"},
            headers={"X-Api-Key": REMOVE_BG_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info("✓ Remove.bg API success")
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        logger.warning(f"Remove.bg status {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Remove.bg exception: {e}")
    return None

def bg_remove_via_rembg(pil_img):
    """rembg local AI — không cần API, chạy offline."""
    if not REMBG_AVAILABLE or REMBG_SESSION is None:
        return None
    try:
        from rembg import remove as rembg_fn
        raw    = to_bytes(pil_img, "PNG")
        result = rembg_fn(raw, session=REMBG_SESSION)
        logger.info("✓ rembg success")
        return Image.open(io.BytesIO(result)).convert("RGBA")
    except Exception as e:
        logger.warning(f"rembg exception: {e}")
    return None

def bg_remove_grabcut(pil_img):
    """
    OpenCV GrabCut — fallback cuối.
    Dành cho ảnh thẻ đã crop (mặt ở giữa, chiếm ~70% chiều cao).
    """
    img_np = to_cv(pil_img)
    H, W = img_np.shape[:2]

    # Rect: loại bỏ ~15% viền xung quanh
    pad_x = int(W * 0.12)
    pad_y = int(H * 0.05)
    rect  = (pad_x, pad_y, W - 2*pad_x, H - 2*pad_y)

    mask = np.zeros((H, W), np.uint8)
    bgd  = np.zeros((1,65), np.float64)
    fgd  = np.zeros((1,65), np.float64)

    try:
        cv2.grabCut(img_np, mask, rect, bgd, fgd, 7, cv2.GC_INIT_WITH_RECT)
        fg = np.where((mask==2)|(mask==0), 0, 255).astype(np.uint8)
    except Exception as e:
        logger.warning(f"GrabCut failed: {e}")
        fg = np.ones((H,W), np.uint8) * 255  # giữ toàn bộ

    # Làm mịn mask
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9,9))
    fg  = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, ker, iterations=2)
    fg  = cv2.GaussianBlur(fg, (7,7), 0)
    _, fg = cv2.threshold(fg, 127, 255, cv2.THRESH_BINARY)

    rgba = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGBA)
    rgba[:,:,3] = fg
    logger.info("✓ GrabCut fallback used")
    return Image.fromarray(rgba).convert("RGBA")

def remove_background(pil_img):
    """
    Xóa nền theo thứ tự ưu tiên:
    1. Remove.bg API (tốt nhất, cần key)
    2. rembg local (tốt, không cần key)
    3. GrabCut (fallback, chấp nhận được)
    """
    result = bg_remove_via_api(pil_img)
    if result: return result, "removebg_api"

    result = bg_remove_via_rembg(pil_img)
    if result: return result, "rembg"

    result = bg_remove_grabcut(pil_img)
    return result, "grabcut"

def apply_background(rgba_img, bg_rgb):
    """Ghép ảnh RGBA lên nền màu."""
    bg = Image.new("RGBA", rgba_img.size, bg_rgb + (255,))
    bg.paste(rgba_img, mask=rgba_img.split()[3])
    return bg.convert("RGB")

# ════════════════════════════════════════════════════════════════
# STEP 5 — ENHANCE
# ════════════════════════════════════════════════════════════════
def enhance_photo(pil_img):
    """Cải thiện chất lượng ảnh thẻ."""
    # Giảm noise trước
    cv  = to_cv(pil_img)
    cv  = cv2.bilateralFilter(cv, d=5, sigmaColor=35, sigmaSpace=35)
    img = to_pil(cv)
    # Tăng sáng/contrast/sharp
    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.8)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=110, threshold=3))
    return img

# ════════════════════════════════════════════════════════════════
# STEP 6 — PDF A4
# ════════════════════════════════════════════════════════════════
def make_pdf(photo, W_mm, H_mm, label):
    """Tạo PDF A4 landscape — lưới ảnh thẻ như PhotoGov."""
    buf  = io.BytesIO()
    pw, ph = A4[1], A4[0]   # landscape: 841×595 pts

    margin = 10*mm
    gap    = 3*mm
    iw, ih = W_mm*mm, H_mm*mm

    cols = max(1, int((pw - 2*margin + gap) / (iw + gap)))
    rows = max(1, int((ph - 2*margin + gap) / (ih + gap)))

    c = pdf_canvas.Canvas(buf, pagesize=(pw, ph))
    c.setTitle(f"ID Photo — {label}")
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, ph - margin + 3*mm,
                 f"{label}  |  {W_mm}×{H_mm}mm  |  {cols}×{rows}  |  In ở 100%")

    img_buf = io.BytesIO()
    photo.convert("RGB").save(img_buf, "JPEG", quality=97, dpi=(300,300))
    img_buf.seek(0)
    ir = ImageReader(img_buf)

    c.setStrokeColorRGB(.75, .75, .75)
    c.setLineWidth(0.25)
    for r in range(rows):
        for col in range(cols):
            x = margin + col*(iw+gap)
            y = ph - margin - ih - r*(ih+gap)
            c.drawImage(ir, x, y, iw, ih, preserveAspectRatio=False)
            c.rect(x, y, iw, ih, stroke=1, fill=0)

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(.5,.5,.5)
    c.drawCentredString(pw/2, margin/2, "In ở kích thước thực 100% — không scale")
    c.save()
    return buf.getvalue()

# ════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":         "ok",
        "version":        "5.0.0",
        "remove_bg_api":  bool(REMOVE_BG_KEY),
        "rembg":          REMBG_AVAILABLE,
        "opencv":         cv2.__version__,
        "reportlab":      True,
        "bg_mode":        ("remove.bg" if REMOVE_BG_KEY
                           else "rembg" if REMBG_AVAILABLE
                           else "grabcut"),
    }

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": SIZES}

@app.post("/api/process")
async def process(
    file:        UploadFile = File(...),
    size_key:    str   = Form("the_3x4"),
    bg_color:    str   = Form("white"),
    bg_hex:      str   = Form(""),
    do_enhance:  bool  = Form(True),
    output_fmt:  str   = Form("jpeg"),
    head_ratio:  float = Form(0.0),
    eye_line:    float = Form(0.0),
):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    sz  = SIZES.get(size_key, SIZES["passport"])
    hr  = head_ratio if head_ratio > 0 else sz["hr"]
    el  = eye_line   if eye_line   > 0 else sz["el"]
    tw, th = sz["pw"], sz["ph"]
    fmt = "jpeg" if output_fmt.lower() == "jpeg" else "png"

    # Màu nền
    try:
        bg_rgb = hex_to_rgb(bg_hex) if (bg_hex and bg_hex.startswith("#") and len(bg_hex)==7) \
                 else BG_MAP.get(bg_color, (255,255,255))
    except:
        bg_rgb = BG_MAP.get(bg_color, (255,255,255))

    try:
        raw = await file.read()
        pil = Image.open(io.BytesIO(raw)).convert("RGB")

        # Giới hạn kích thước input
        if max(pil.size) > MAX_INPUT_DIM:
            scale = MAX_INPUT_DIM / max(pil.size)
            pil = pil.resize(
                (int(pil.width*scale), int(pil.height*scale)),
                Image.LANCZOS
            )

        logger.info(f"▶ process {pil.size} → {size_key} bg={bg_color}")

        # ── 1. Nhận diện khuôn mặt ──────────────────────────────────
        img_cv   = to_cv(pil)
        face     = detect_face(img_cv)
        face_ok  = face is not None
        logger.info(f"  Face: {'✓' if face_ok else '✗'}")

        # ── 2. Xoay thẳng ────────────────────────────────────────────
        if face_ok:
            img_cv = straighten(img_cv, face)
            face   = detect_face(img_cv) or face  # detect lại sau xoay
            pil    = to_pil(img_cv)

        # ── 3. CROP TRƯỚC (ảnh nhỏ hơn → xóa nền nhanh hơn) ────────
        cropped = icao_crop(pil, face, tw, th, hr, el)
        logger.info(f"  Crop: {cropped.size}")

        # ── 4. XÓA NỀN trên ảnh đã crop ────────────────────────────
        nobg, bg_mode = remove_background(cropped)
        logger.info(f"  BG remove: {bg_mode}")

        # ── 5. Thêm màu nền ──────────────────────────────────────────
        result = apply_background(nobg, bg_rgb)

        # ── 6. Enhance ───────────────────────────────────────────────
        if do_enhance:
            result = enhance_photo(result)

        # ── 7. Xuất ảnh ──────────────────────────────────────────────
        img_bytes = to_bytes(result, fmt.upper())
        img_b64   = base64.b64encode(img_bytes).decode()

        # ── 8. PDF tờ in ─────────────────────────────────────────────
        pdf_bytes = make_pdf(result, sz["W"], sz["H"], sz["label"])
        pdf_b64   = base64.b64encode(pdf_bytes).decode()

        logger.info(f"  ✓ Done: img={len(img_bytes)//1024}KB pdf={len(pdf_bytes)//1024}KB")

        return JSONResponse({
            "success":       True,
            "image_b64":     img_b64,
            "pdf_b64":       pdf_b64,
            "format":        fmt,
            "width":         tw,
            "height":        th,
            "size_label":    sz["label"],
            "face_detected": face_ok,
            "bg_mode":       bg_mode,
        })

    except Exception as e:
        logger.error(f"process error: {e}", exc_info=True)
        raise HTTPException(500, f"Lỗi: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
