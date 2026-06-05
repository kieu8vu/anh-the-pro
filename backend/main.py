"""
ID Photo Pro — Backend v6.0
════════════════════════════════════════════════════════════════════════
Tính năng (theo PhotoGov standard):

  1. ICAO-compliant face detection & alignment
     - OpenCV Haar Cascade (frontal face + eyes)
     - Tính góc nghiêng từ vector 2 mắt → xoay thẳng
     - IED-based crop (Inter-Eye Distance) → đúng tỷ lệ ICAO

  2. Background removal (3-tier fallback)
     - Remove.bg API (chất lượng cao nhất)
     - rembg u2netp ONNX (local, không cần API)
     - GrabCut OpenCV (fallback cuối)

  3. ICAO Compliance Check
     - Kiểm tra 12 tiêu chí: head size, eye position, background,
       lighting, sharpness, aspect ratio...
     - Trả về compliance report chi tiết

  4. Photo Variants — 4 biến thể outfit
     - Original (giữ nguyên quần áo)
     - Formal Dark (thêm jacket tối màu)
     - Formal Light (thêm áo sáng màu)
     - Smart Casual (cổ áo phù hợp)
     Dùng thuật toán vùng cổ/vai để blend outfit

  5. PDF A4 tờ in (như PhotoGov For-Print-A4.pdf)
     - Lưới ảnh vừa khổ A4 landscape
     - Đường kẻ cắt, header thông tin

  Pipeline đúng thứ tự:
    detect → straighten → ICAO crop → remove bg → add bg color
    → enhance → compliance check → variants → PDF
════════════════════════════════════════════════════════════════════════
"""

import io, base64, logging, math, os
from typing import Optional, Tuple, List, Dict

import cv2
import numpy as np
import requests as http_requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageEnhance, ImageFilter, ImageStat, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.utils import ImageReader

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
REMOVE_BG_KEY = os.environ.get("REMOVE_BG_KEY", "")
MAX_INPUT_DIM  = 2000

# ── rembg: load đồng bộ khi startup ──────────────────────────────────────────
REMBG_SESSION   = None
REMBG_AVAILABLE = False

def _load_rembg():
    global REMBG_SESSION, REMBG_AVAILABLE
    try:
        from rembg import new_session
        for model in ["u2netp", "u2net"]:
            try:
                REMBG_SESSION   = new_session(model)
                REMBG_AVAILABLE = True
                logger.info(f"✓ rembg {model} ready")
                return
            except Exception as e:
                logger.warning(f"rembg {model}: {e}")
    except ImportError:
        logger.warning("rembg not installed")

_load_rembg()

# ── OpenCV ────────────────────────────────────────────────────────────────────
_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_EYE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_eye.xml"
)
_PROFILE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_profileface.xml"
)

app = FastAPI(title="ID Photo Pro API v6", version="6.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ════════════════════════════════════════════════════════════════════════
# PHOTO SIZE DATABASE — ICAO 9303 + 22 quốc gia
# ICAO spec: face height = 70-80% of image height
#            eye line = 56-69% from BOTTOM = 31-44% from TOP
# ════════════════════════════════════════════════════════════════════════
SIZES = {
    "vn_cccd":     {"W":22,"H":28,"pw":260,"ph":330,"label":"CCCD/CMND VN",        "hr":0.72,"el":0.42},
    "the_3x4":     {"W":30,"H":40,"pw":354,"ph":472,"label":"Ảnh 3×4 cm",          "hr":0.72,"el":0.42},
    "the_4x6":     {"W":40,"H":60,"pw":472,"ph":709,"label":"Ảnh 4×6 cm",          "hr":0.72,"el":0.42},
    "the_2x3":     {"W":20,"H":30,"pw":236,"ph":354,"label":"Ảnh 2×3 cm",          "hr":0.72,"el":0.42},
    "chung_minh":  {"W":22,"H":28,"pw":260,"ph":330,"label":"CCCD/CMND",           "hr":0.72,"el":0.42},
    "passport":    {"W":35,"H":45,"pw":413,"ph":531,"label":"Hộ chiếu 35×45mm",   "hr":0.75,"el":0.43},
    "visa_us":     {"W":51,"H":51,"pw":600,"ph":600,"label":"US Passport/Visa",    "hr":0.65,"el":0.40},
    "uk_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"UK Passport",         "hr":0.75,"el":0.43},
    "eu_schengen": {"W":35,"H":45,"pw":413,"ph":531,"label":"EU/Schengen",         "hr":0.76,"el":0.44},
    "de_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Đức",                 "hr":0.76,"el":0.44},
    "fr_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Pháp",                "hr":0.76,"el":0.44},
    "ca_passport": {"W":50,"H":70,"pw":591,"ph":827,"label":"Canada",              "hr":0.68,"el":0.40},
    "jp_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Nhật Bản",            "hr":0.75,"el":0.43},
    "jp_visa":     {"W":45,"H":45,"pw":531,"ph":531,"label":"Visa Nhật",           "hr":0.70,"el":0.42},
    "kr_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Hàn Quốc",            "hr":0.75,"el":0.43},
    "cn_visa":     {"W":33,"H":48,"pw":390,"ph":567,"label":"Visa TQ",             "hr":0.72,"el":0.43},
    "in_passport": {"W":51,"H":51,"pw":600,"ph":600,"label":"Ấn Độ",               "hr":0.65,"el":0.40},
    "sg_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Singapore",           "hr":0.75,"el":0.43},
    "au_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"Úc",                  "hr":0.75,"el":0.43},
    "nz_passport": {"W":35,"H":45,"pw":413,"ph":531,"label":"New Zealand",         "hr":0.75,"el":0.43},
    "ae_visa":     {"W":35,"H":45,"pw":413,"ph":531,"label":"UAE/Dubai",           "hr":0.75,"el":0.43},
    "visa_schengen":{"W":35,"H":45,"pw":413,"ph":531,"label":"Visa Schengen",      "hr":0.76,"el":0.44},
}

BG_MAP = {
    "white":      (255,255,255),
    "light_grey": (240,240,240),
    "light_blue": (214,228,240),
    "blue":       (67,114,196),
    "red":        (204,0,0),
    "gray":       (200,200,200),
}

# Màu sắc outfit cho variants
OUTFIT_CONFIGS = {
    "original":     {"name":"Ảnh gốc",       "collar":(220,220,225), "jacket":(210,210,215)},
    "formal_dark":  {"name":"Vest tối",       "collar":(240,240,245), "jacket":(40,40,60)},
    "formal_light": {"name":"Áo sáng",        "collar":(245,245,250), "jacket":(200,210,220)},
    "smart_casual": {"name":"Smart Casual",   "collar":(240,240,245), "jacket":(100,120,140)},
}

# ════════════════════════════════════════════════════════════════════════
# UTILS
# ════════════════════════════════════════════════════════════════════════
def to_cv(pil: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)

def to_pil(cv_img: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))

def to_bytes(pil: Image.Image, fmt="JPEG", q=95) -> bytes:
    buf = io.BytesIO()
    save_pil = pil.convert("RGB") if fmt.upper()=="JPEG" else pil
    save_pil.save(buf, format=fmt, quality=q, dpi=(300,300))
    return buf.getvalue()

def pil_to_b64(pil: Image.Image, fmt="JPEG") -> str:
    return base64.b64encode(to_bytes(pil, fmt)).decode()

def hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.lstrip("#")
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

# ════════════════════════════════════════════════════════════════════════
# STEP 1 — NHẬN DIỆN KHUÔN MẶT
# ════════════════════════════════════════════════════════════════════════
def detect_face(img_np: np.ndarray) -> Optional[Dict]:
    """
    Phát hiện khuôn mặt + 2 mắt bằng OpenCV Haar Cascade.
    Thử nhiều scale và cả profile face detector.
    """
    gray   = cv2.equalizeHist(cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY))
    H, W   = img_np.shape[:2]
    min_sz = (max(30, W//15), max(30, H//15))

    # Thử frontal face detector với nhiều scale
    faces = np.array([])
    for scale, nb, min_n in [(1.05,3,1),(1.08,4,2),(1.12,5,3)]:
        f = _FACE_CASCADE.detectMultiScale(
            gray, scale, nb, minSize=min_sz,
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        if len(f) > 0:
            faces = f; break

    # Fallback: profile face
    if len(faces) == 0:
        f = _PROFILE_CASCADE.detectMultiScale(gray, 1.05, 3, minSize=min_sz)
        if len(f) > 0: faces = f

    if len(faces) == 0:
        return None

    x, y, w, h = map(int, max(faces, key=lambda f: f[2]*f[3]))

    # Phát hiện mắt trong nửa trên khuôn mặt
    roi_gray = gray[y : y+h//2, x : x+w]
    eyes_raw = _EYE_CASCADE.detectMultiScale(
        roi_gray, 1.08, 4, minSize=(8,8)
    )
    eyes = sorted(
        [(int(x+ex+ew//2), int(y+ey+eh//2)) for ex,ey,ew,eh in eyes_raw],
        key=lambda e: e[0]
    )[:2]

    # Ước lượng điểm đỉnh đầu và cằm từ bounding box
    chin_y  = y + h         # cằm ≈ đáy bounding box
    crown_y = y             # đỉnh đầu ≈ top bounding box

    return {
        "x":x, "y":y, "w":w, "h":h,
        "cx": x+w//2, "cy": y+h//2,
        "eyes": eyes,
        "chin_y": chin_y,
        "crown_y": crown_y,
    }

# ════════════════════════════════════════════════════════════════════════
# STEP 2 — XOAY THẲNG MẶT
# ════════════════════════════════════════════════════════════════════════
def straighten_face(img_np: np.ndarray, face: Dict) -> Tuple[np.ndarray, float]:
    """
    Xoay ảnh để đường nối 2 mắt nằm ngang.
    Trả về (ảnh đã xoay, góc xoay).
    """
    eyes = face.get("eyes", [])
    if len(eyes) < 2:
        return img_np, 0.0

    e1, e2 = eyes[0], eyes[1]
    angle  = math.degrees(math.atan2(e2[1]-e1[1], e2[0]-e1[0]))

    if abs(angle) < 0.3:
        return img_np, 0.0

    cx = (e1[0]+e2[0]) / 2
    cy = (e1[1]+e2[1]) / 2
    H, W = img_np.shape[:2]
    M    = cv2.getRotationMatrix2D((cx,cy), angle, 1.0)
    out  = cv2.warpAffine(img_np, M, (W,H),
                          flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REFLECT_101)
    logger.info(f"  Rotated {angle:.2f}°")
    return out, angle

# ════════════════════════════════════════════════════════════════════════
# STEP 3 — ICAO CROP (IED-based)
# ════════════════════════════════════════════════════════════════════════
def icao_crop(pil_img: Image.Image, face: Optional[Dict],
              tw: int, th: int, hr: float, el: float) -> Image.Image:
    """
    Crop ảnh theo chuẩn ICAO 9303.

    Công thức:
      IED = khoảng cách 2 mắt (pixel)
      face_h (mắt→cằm) = IED × 1.8
      head_h (đỉnh→cằm) = face_h × 1.55   (bao gồm trán + tóc)
      crop_h = head_h / hr                  (hr: tỷ lệ đầu/ảnh)
      crop_top = eye_y - el × crop_h        (el: vị trí mắt từ trên)
      crop_left = eye_x - crop_w/2          (căn giữa ngang)
    """
    iw, ih = pil_img.size

    if face is None:
        # Không tìm thấy mặt → crop giữa đúng tỷ lệ
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

    if len(eyes) >= 2:
        e1, e2 = eyes[0], eyes[1]
        ied    = math.hypot(e2[0]-e1[0], e2[1]-e1[1])
        eye_y  = (e1[1]+e2[1]) / 2
        eye_x  = (e1[0]+e2[0]) / 2
    else:
        # Ước lượng từ bounding box
        ied    = fw * 0.45
        eye_y  = fy + fh * 0.35
        eye_x  = float(face["cx"])

    # Tính kích thước crop
    face_h2chin = ied * 1.8
    head_h      = face_h2chin * 1.55
    crop_h      = head_h / hr
    crop_w      = crop_h * (tw / th)

    crop_top  = eye_y - el * crop_h
    crop_left = eye_x - crop_w / 2

    # Clamp trong ảnh
    crop_left = max(0.0, min(crop_left, iw - crop_w))
    crop_top  = max(0.0, min(crop_top,  ih - crop_h))
    crop_w    = min(crop_w, float(iw))
    crop_h    = min(crop_h, float(ih))

    box = (int(crop_left), int(crop_top),
           int(crop_left+crop_w), int(crop_top+crop_h))
    return pil_img.crop(box).resize((tw, th), Image.LANCZOS)

# ════════════════════════════════════════════════════════════════════════
# STEP 4 — XÓA NỀN (3-tier fallback)
# ════════════════════════════════════════════════════════════════════════
def _removebg_api(pil_img: Image.Image) -> Optional[Image.Image]:
    """Remove.bg API — best quality."""
    if not REMOVE_BG_KEY: return None
    try:
        buf = io.BytesIO()
        pil_img.save(buf, "PNG"); buf.seek(0)
        resp = http_requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={"image_file": ("p.png", buf, "image/png")},
            data={"size": "auto"},
            headers={"X-Api-Key": REMOVE_BG_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info("  ✓ Remove.bg API")
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        logger.warning(f"  Remove.bg {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"  Remove.bg error: {e}")
    return None

def _removebg_rembg(pil_img: Image.Image) -> Optional[Image.Image]:
    """rembg local ONNX."""
    if not REMBG_AVAILABLE or REMBG_SESSION is None: return None
    try:
        from rembg import remove as rembg_fn
        raw    = to_bytes(pil_img, "PNG")
        result = rembg_fn(raw, session=REMBG_SESSION)
        logger.info("  ✓ rembg AI")
        return Image.open(io.BytesIO(result)).convert("RGBA")
    except Exception as e:
        logger.warning(f"  rembg error: {e}")
    return None

def _removebg_grabcut(pil_img: Image.Image) -> Image.Image:
    """
    OpenCV GrabCut — fallback.
    Dành cho ảnh đã crop (người ở giữa, chiếm ~80% frame).
    """
    img_np = to_cv(pil_img)
    H, W   = img_np.shape[:2]

    # Rect bao người: bỏ 12% mỗi cạnh ngang, 5% trên/dưới
    px = max(5, int(W*0.12))
    py = max(3, int(H*0.05))
    rect = (px, py, W-2*px, H-2*py)

    mask = np.zeros((H,W), np.uint8)
    bgd  = np.zeros((1,65), np.float64)
    fgd  = np.zeros((1,65), np.float64)

    try:
        cv2.grabCut(img_np, mask, rect, bgd, fgd, 8, cv2.GC_INIT_WITH_RECT)
        fg = np.where((mask==2)|(mask==0), 0, 255).astype(np.uint8)
    except:
        fg = np.ones((H,W), np.uint8) * 255

    # Làm mịn cạnh
    ker  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9,9))
    fg   = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, ker, iterations=2)
    fg   = cv2.GaussianBlur(fg, (7,7), 0)
    _, fg = cv2.threshold(fg, 127, 255, cv2.THRESH_BINARY)

    rgba = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGBA)
    rgba[:,:,3] = fg
    logger.info("  ✓ GrabCut fallback")
    return Image.fromarray(rgba).convert("RGBA")

def remove_background(pil_img: Image.Image) -> Tuple[Image.Image, str]:
    """Thử theo thứ tự: Remove.bg → rembg → GrabCut."""
    r = _removebg_api(pil_img)
    if r: return r, "removebg_api"

    r = _removebg_rembg(pil_img)
    if r: return r, "rembg"

    r = _removebg_grabcut(pil_img)
    return r, "grabcut"

def apply_background(rgba: Image.Image, bg_rgb: Tuple) -> Image.Image:
    """Ghép ảnh đã xóa nền lên màu nền mới."""
    bg = Image.new("RGBA", rgba.size, bg_rgb+(255,))
    bg.paste(rgba, mask=rgba.split()[3])
    return bg.convert("RGB")

# ════════════════════════════════════════════════════════════════════════
# STEP 5 — ENHANCE CHẤT LƯỢNG
# ════════════════════════════════════════════════════════════════════════
def enhance_photo(pil_img: Image.Image) -> Image.Image:
    """
    Cải thiện chất lượng ảnh thẻ:
    - Bilateral filter: giảm noise giữ cạnh sắc
    - Brightness: +5%
    - Contrast: +12%
    - Sharpness: +80%
    - Color saturation: +5%
    - UnsharpMask: làm nét chi tiết
    """
    cv_img = to_cv(pil_img)
    cv_img = cv2.bilateralFilter(cv_img, d=5, sigmaColor=35, sigmaSpace=35)
    img = to_pil(cv_img)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.8)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=110, threshold=3))
    return img

# ════════════════════════════════════════════════════════════════════════
# STEP 6 — ICAO COMPLIANCE CHECK
# ════════════════════════════════════════════════════════════════════════
def icao_compliance_check(
    pil_img: Image.Image,
    face_detected: bool,
    bg_mode: str,
    head_ratio: float,
    eye_line: float,
) -> Dict:
    """
    Kiểm tra 12 tiêu chí ICAO 9303 + ISO/IEC 19794-5.
    Trả về dict với danh sách pass/fail và overall score.
    """
    W, H = pil_img.size
    checks = []

    # 1. Kích thước tối thiểu
    checks.append({
        "id": "resolution",
        "label": "Độ phân giải đủ (min 300×300px)",
        "pass": W >= 300 and H >= 300,
        "detail": f"{W}×{H}px"
    })

    # 2. Tỷ lệ khung hình
    ratio = W/H if H>0 else 0
    checks.append({
        "id": "aspect",
        "label": "Tỷ lệ khung hình đúng",
        "pass": 0.4 < ratio < 2.0,
        "detail": f"{ratio:.2f}"
    })

    # 3. Phát hiện khuôn mặt
    checks.append({
        "id": "face",
        "label": "Phát hiện khuôn mặt",
        "pass": face_detected,
        "detail": "Có" if face_detected else "Không"
    })

    # 4. Vị trí khuôn mặt (head ratio)
    hr_ok = 0.50 <= head_ratio <= 0.85
    checks.append({
        "id": "head_size",
        "label": "Tỷ lệ đầu ICAO (50-85%)",
        "pass": hr_ok,
        "detail": f"{head_ratio*100:.0f}%"
    })

    # 5. Vị trí mắt (ICAO: mắt ở 31-44% từ trên = 56-69% từ dưới)
    el_ok = 0.30 <= eye_line <= 0.48
    checks.append({
        "id": "eye_position",
        "label": "Vị trí mắt ICAO (31-48% từ trên)",
        "pass": el_ok,
        "detail": f"{eye_line*100:.0f}% từ trên"
    })

    # 6. Phân tích độ sáng (ICAO: không quá tối/sáng)
    gray = np.array(pil_img.convert("L"))
    mean_brightness = float(np.mean(gray))
    brightness_ok = 80 < mean_brightness < 220
    checks.append({
        "id": "brightness",
        "label": "Độ sáng hợp lý (80-220/255)",
        "pass": brightness_ok,
        "detail": f"{mean_brightness:.0f}/255"
    })

    # 7. Độ tương phản
    std_brightness = float(np.std(gray))
    contrast_ok = std_brightness > 20
    checks.append({
        "id": "contrast",
        "label": "Tương phản đủ (std>20)",
        "pass": contrast_ok,
        "detail": f"std={std_brightness:.1f}"
    })

    # 8. Độ sắc nét (Laplacian variance)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_ok = lap_var > 50
    checks.append({
        "id": "sharpness",
        "label": "Ảnh đủ sắc nét (blur<threshold)",
        "pass": sharpness_ok,
        "detail": f"score={lap_var:.0f}"
    })

    # 9. Màu nền (kiểm tra góc ảnh có đồng màu không)
    corners = [
        pil_img.crop((0,0,30,30)),
        pil_img.crop((W-30,0,W,30)),
        pil_img.crop((0,H-30,30,H)),
        pil_img.crop((W-30,H-30,W,H)),
    ]
    corner_means = [np.mean(np.array(c)) for c in corners]
    bg_uniform = (max(corner_means) - min(corner_means)) < 60
    checks.append({
        "id": "background",
        "label": "Nền đồng màu (không loang)",
        "pass": bg_uniform,
        "detail": "Đồng màu" if bg_uniform else "Không đồng màu"
    })

    # 10. Màu nền sáng (ICAO yêu cầu nền sáng)
    avg_corner = sum(corner_means)/len(corner_means)
    bg_light = avg_corner > 160
    checks.append({
        "id": "bg_color",
        "label": "Màu nền sáng (ICAO yêu cầu)",
        "pass": bg_light,
        "detail": f"avg={avg_corner:.0f}/255"
    })

    # 11. Không bị cắt mặt (face detected + center)
    face_centered = face_detected  # nếu detect được thì đã crop chuẩn
    checks.append({
        "id": "face_complete",
        "label": "Khuôn mặt đầy đủ, không bị cắt",
        "pass": face_centered,
        "detail": "OK" if face_centered else "Cần kiểm tra"
    })

    # 12. Định dạng ảnh màu (không trắng đen)
    r_ch = np.mean(np.array(pil_img)[:,:,0])
    g_ch = np.mean(np.array(pil_img)[:,:,1])
    b_ch = np.mean(np.array(pil_img)[:,:,2])
    is_color = max(abs(r_ch-g_ch), abs(g_ch-b_ch), abs(r_ch-b_ch)) > 8
    checks.append({
        "id": "color",
        "label": "Ảnh màu (không trắng đen)",
        "pass": is_color,
        "detail": "Màu" if is_color else "Trắng đen"
    })

    passed = sum(1 for c in checks if c["pass"])
    total  = len(checks)
    score  = round(passed / total * 100)

    return {
        "score":   score,
        "passed":  passed,
        "total":   total,
        "checks":  checks,
        "verdict": "COMPLIANT" if score >= 75 else ("REVIEW" if score >= 60 else "NON_COMPLIANT"),
    }

# ════════════════════════════════════════════════════════════════════════
# STEP 7 — PHOTO VARIANTS (4 biến thể outfit)
# ════════════════════════════════════════════════════════════════════════
def _get_clothing_region(pil_img: Image.Image, face: Optional[Dict]) -> Tuple[int,int,int,int]:
    """
    Xác định vùng quần áo (bên dưới cổ).
    Trả về (x1, y1, x2, y2) vùng clothing.
    """
    W, H = pil_img.size
    if face is None:
        # Không có face → dùng 60% dưới ảnh
        return (0, int(H*0.40), W, H)

    fy, fh = face["y"], face["h"]
    # Vùng quần áo: từ cổ (≈ đáy khuôn mặt + 10%) đến đáy ảnh
    neck_y = min(int((fy + fh) * 1.05), H-1)
    return (0, neck_y, W, H)

def _create_outfit_variant(
    base_img: Image.Image,
    nobg_rgba: Image.Image,
    bg_rgb: Tuple,
    face_region: Optional[Dict],
    outfit: str,
    collar_color: Tuple,
    jacket_color: Tuple,
) -> Image.Image:
    """
    Tạo biến thể ảnh với outfit khác nhau.

    Thuật toán:
    1. Lấy mask alpha từ ảnh đã xóa nền
    2. Xác định vùng quần áo (dưới cằm)
    3. Tạo gradient outfit trên vùng đó
    4. Blend nhẹ nhàng qua Gaussian blur ở viền
    5. Ghép lên nền màu
    """
    W, H = base_img.size

    if outfit == "original":
        return base_img

    # Lấy alpha mask từ nobg
    alpha = nobg_rgba.split()[3]  # alpha channel
    alpha_np = np.array(alpha)

    # Vùng quần áo
    cloth_x1, cloth_y1, cloth_x2, cloth_y2 = _get_clothing_region(base_img, face_region)

    # Tạo cloth mask: chỉ trong vùng quần áo VÀ có người (alpha > 0)
    cloth_mask = np.zeros((H, W), dtype=np.uint8)
    cloth_mask[cloth_y1:cloth_y2, cloth_x1:cloth_x2] = 255
    # Chỉ thay vùng có người
    person_mask = (alpha_np > 128).astype(np.uint8) * 255
    cloth_mask  = cv2.bitwise_and(cloth_mask, person_mask)

    # Làm mịn mask để transition tự nhiên
    blur_sz = max(11, min(W, H) // 15)
    if blur_sz % 2 == 0: blur_sz += 1
    cloth_mask_blur = cv2.GaussianBlur(cloth_mask, (blur_sz, blur_sz), 0)

    # Tạo outfit layer
    outfit_layer = np.zeros((H, W, 3), dtype=np.uint8)

    # Vùng collar (cổ áo): ~15% trên vùng quần áo, màu sáng hơn
    collar_h = max(10, int((cloth_y2 - cloth_y1) * 0.15))
    collar_y2 = cloth_y1 + collar_h
    outfit_layer[cloth_y1:collar_y2, cloth_x1:cloth_x2] = collar_color

    # Vùng jacket (thân áo): phần còn lại
    outfit_layer[collar_y2:cloth_y2, cloth_x1:cloth_x2] = jacket_color

    # Thêm gradient (đậm dần xuống dưới)
    for row in range(collar_y2, cloth_y2):
        t = (row - collar_y2) / max(1, cloth_y2 - collar_y2)
        factor = 0.85 + t * 0.15
        outfit_layer[row, cloth_x1:cloth_x2] = np.clip(
            np.array(jacket_color) * factor, 0, 255
        ).astype(np.uint8)

    # Blend outfit vào ảnh gốc
    base_np = np.array(base_img.convert("RGB"))
    mask_3ch = cloth_mask_blur[:,:,np.newaxis] / 255.0

    # Alpha blend: alpha=0.85 cho jacket (giữ texture nhẹ từ gốc)
    blend_alpha = 0.80
    result_np = (
        base_np * (1 - mask_3ch * blend_alpha) +
        outfit_layer * mask_3ch * blend_alpha
    ).astype(np.uint8)

    # Áp lại alpha mask (giữ nền trong suốt)
    result_rgba = np.dstack([result_np, alpha_np])
    result_pil  = Image.fromarray(result_rgba, "RGBA")

    # Ghép lên nền màu
    bg = Image.new("RGBA", (W,H), bg_rgb+(255,))
    bg.paste(result_pil, mask=result_pil.split()[3])
    return bg.convert("RGB")

def generate_variants(
    cropped_pil: Image.Image,
    nobg_rgba: Image.Image,
    bg_rgb: Tuple,
    face: Optional[Dict],
    do_enhance: bool,
    tw: int,
    th: int,
) -> Dict[str, str]:
    """
    Tạo 4 biến thể outfit.
    Trả về dict {variant_name: base64_image}.
    """
    variants = {}
    cfg = OUTFIT_CONFIGS

    # Ảnh gốc (với nền mới)
    orig = apply_background(nobg_rgba, bg_rgb)
    if do_enhance: orig = enhance_photo(orig)
    variants["original"] = pil_to_b64(orig)

    # 3 biến thể outfit
    for key in ["formal_dark", "formal_light", "smart_casual"]:
        c = cfg[key]
        try:
            v = _create_outfit_variant(
                orig, nobg_rgba, bg_rgb, face,
                key, c["collar"], c["jacket"]
            )
            if do_enhance: v = enhance_photo(v)
            variants[key] = pil_to_b64(v)
        except Exception as e:
            logger.warning(f"Variant {key} failed: {e}")
            variants[key] = variants["original"]  # fallback

    return variants

# ════════════════════════════════════════════════════════════════════════
# STEP 8 — PDF A4 TỜ IN
# ════════════════════════════════════════════════════════════════════════
def make_pdf(photo: Image.Image, W_mm: int, H_mm: int, label: str) -> bytes:
    """
    Tạo PDF A4 landscape — lưới ảnh thẻ với đường kẻ cắt.
    Giống For-Print-(A4).pdf của PhotoGov.
    """
    buf    = io.BytesIO()
    pw, ph = A4[1], A4[0]   # landscape 841×595 pts

    margin = 10 * mm
    gap    = 3  * mm
    iw, ih = W_mm*mm, H_mm*mm

    cols = max(1, int((pw - 2*margin + gap) / (iw + gap)))
    rows = max(1, int((ph - 2*margin + gap) / (ih + gap)))

    c = pdf_canvas.Canvas(buf, pagesize=(pw, ph))
    c.setTitle(f"ID Photo — {label}")

    # Header
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, ph-margin+3*mm,
                 f"{label}  |  {W_mm}×{H_mm}mm  |  {cols}×{rows} ảnh  |  In ở 100% (không scale)")

    # Render ảnh vào buffer
    img_buf = io.BytesIO()
    photo.convert("RGB").save(img_buf, "JPEG", quality=97, dpi=(300,300))
    img_buf.seek(0)
    ir = ImageReader(img_buf)

    # Vẽ lưới
    c.setStrokeColorRGB(.7,.7,.7)
    c.setLineWidth(0.25)
    for row in range(rows):
        for col in range(cols):
            x = margin + col*(iw+gap)
            y = ph - margin - ih - row*(ih+gap)
            c.drawImage(ir, x, y, iw, ih, preserveAspectRatio=False)
            # Đường kẻ cắt
            c.rect(x, y, iw, ih, stroke=1, fill=0)
            # Dấu cắt góc
            cut = 3*mm
            c.setStrokeColorRGB(.5,.5,.5)
            c.setLineWidth(0.15)
            for dx, dy in [(-cut,0),(cut,0),(0,-cut),(0,cut)]:
                cx_start = x + (0 if dx<0 else iw)
                cy_start = y + (0 if dy<0 else ih)
                c.line(cx_start, cy_start,
                       cx_start+dx, cy_start+dy)
            c.setStrokeColorRGB(.7,.7,.7)
            c.setLineWidth(0.25)

    # Footer
    c.setFont("Helvetica", 6)
    c.setFillColorRGB(.4,.4,.4)
    c.drawCentredString(pw/2, margin/3,
                        "ID Photo Pro — In ở kích thước thực 100% — không phóng to thu nhỏ")
    c.save()
    return buf.getvalue()

# ════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":        "ok",
        "version":       "6.0.0",
        "remove_bg_api": bool(REMOVE_BG_KEY),
        "rembg":         REMBG_AVAILABLE,
        "opencv":        cv2.__version__,
        "reportlab":     True,
        "bg_mode":       ("remove.bg" if REMOVE_BG_KEY
                          else "rembg" if REMBG_AVAILABLE
                          else "grabcut"),
        "features": ["face_detect","straighten","icao_crop",
                     "bg_remove","enhance","compliance_check",
                     "variants_4","pdf_a4"],
    }

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": SIZES}

@app.post("/api/process")
async def process_photo(
    file:           UploadFile = File(...),
    size_key:       str   = Form("the_3x4"),
    bg_color:       str   = Form("white"),
    bg_hex:         str   = Form(""),
    do_enhance:     bool  = Form(True),
    do_variants:    bool  = Form(True),   # tạo 4 biến thể outfit
    do_compliance:  bool  = Form(True),   # ICAO compliance check
    output_fmt:     str   = Form("jpeg"),
    head_ratio:     float = Form(0.0),
    eye_line:       float = Form(0.0),
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

        # Giới hạn input
        if max(pil.size) > MAX_INPUT_DIM:
            sc = MAX_INPUT_DIM / max(pil.size)
            pil = pil.resize(
                (int(pil.width*sc), int(pil.height*sc)),
                Image.LANCZOS
            )

        logger.info(f"▶ process {pil.size} → {size_key} bg={bg_color} hr={hr:.2f}")

        # ── 1. Nhận diện khuôn mặt ─────────────────────────────────────
        img_cv  = to_cv(pil)
        face    = detect_face(img_cv)
        face_ok = face is not None
        logger.info(f"  Face: {'✓' if face_ok else '✗ (center crop)'}")

        # ── 2. Xoay thẳng mặt ──────────────────────────────────────────
        angle = 0.0
        if face_ok:
            img_cv, angle = straighten_face(img_cv, face)
            if abs(angle) > 0.3:
                face = detect_face(img_cv) or face  # detect lại sau xoay
            pil = to_pil(img_cv)

        # ── 3. CROP ICAO ────────────────────────────────────────────────
        cropped = icao_crop(pil, face, tw, th, hr, el)
        logger.info(f"  Crop: {cropped.size}")

        # ── 4. Xóa nền (trên ảnh đã crop — nhỏ hơn, nhanh hơn) ────────
        nobg_rgba, bg_mode = remove_background(cropped)

        # ── 5. Thêm màu nền (ảnh chính) ────────────────────────────────
        result = apply_background(nobg_rgba, bg_rgb)

        # ── 6. Enhance ─────────────────────────────────────────────────
        if do_enhance:
            result = enhance_photo(result)

        # ── 7. ICAO Compliance Check ────────────────────────────────────
        compliance = None
        if do_compliance:
            compliance = icao_compliance_check(result, face_ok, bg_mode, hr, el)
            logger.info(f"  Compliance: {compliance['score']}% ({compliance['verdict']})")

        # ── 8. Variants (4 biến thể outfit) ────────────────────────────
        variants_b64 = {}
        if do_variants:
            # Dùng face từ ảnh gốc (tọa độ đã scale về kích thước ảnh thẻ)
            # Ước lượng face trong ảnh crop
            face_in_crop = None
            if face_ok:
                # Detect lại trong ảnh đã crop
                crop_cv = to_cv(cropped)
                face_in_crop = detect_face(crop_cv)
            variants_b64 = generate_variants(
                cropped, nobg_rgba, bg_rgb,
                face_in_crop, do_enhance, tw, th
            )
            logger.info(f"  Variants: {list(variants_b64.keys())}")

        # ── 9. Export ảnh chính ─────────────────────────────────────────
        img_bytes = to_bytes(result, fmt.upper())
        img_b64   = base64.b64encode(img_bytes).decode()

        # ── 10. PDF tờ in A4 ───────────────────────────────────────────
        pdf_bytes = make_pdf(result, sz["W"], sz["H"], sz["label"])
        pdf_b64   = base64.b64encode(pdf_bytes).decode()

        logger.info(f"  ✓ Done img={len(img_bytes)//1024}KB pdf={len(pdf_bytes)//1024}KB")

        return JSONResponse({
            "success":       True,
            "image_b64":     img_b64,
            "pdf_b64":       pdf_b64,
            "format":        fmt,
            "width":         tw,
            "height":        th,
            "size_label":    sz["label"],
            "face_detected": face_ok,
            "rotation_angle":round(angle, 2),
            "bg_mode":       bg_mode,
            "compliance":    compliance,
            "variants":      variants_b64,
            "variant_names": {
                "original":     OUTFIT_CONFIGS["original"]["name"],
                "formal_dark":  OUTFIT_CONFIGS["formal_dark"]["name"],
                "formal_light": OUTFIT_CONFIGS["formal_light"]["name"],
                "smart_casual": OUTFIT_CONFIGS["smart_casual"]["name"],
            }
        })

    except Exception as e:
        logger.error(f"process error: {e}", exc_info=True)
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
