"""
ID Photo Pro — Backend v4.0
════════════════════════════════════════════════════════════════
Mô hình như PhotoGov: Upload → AI xóa nền → Crop ICAO → PDF + PNG

Pipeline:
  1. Nhận diện khuôn mặt (OpenCV)
  2. Xoay thẳng mặt
  3. Xóa nền (Remove.bg API → rembg → GrabCut fallback)
  4. Crop chuẩn ICAO (IED-based)
  5. Thêm màu nền
  6. Enhance chất lượng
  7. Xuất PNG đơn + PDF tờ in A4

Endpoints:
  POST /api/process   → xử lý đầy đủ, trả PNG + PDF base64
  GET  /api/sizes     → danh sách kích thước
  GET  /health        → health check
════════════════════════════════════════════════════════════════
"""

import io, base64, logging, math, os
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Remove.bg API key (set qua env var REMOVE_BG_KEY) ────────────────────────
REMOVE_BG_KEY = os.environ.get("REMOVE_BG_KEY", "")

# ── rembg (fallback 1) — lazy load để không block startup port binding ─────────
REMBG_AVAILABLE = False
REMBG_SESSION   = None

def _init_rembg():
    """Chạy trong background thread — không block uvicorn startup"""
    global REMBG_AVAILABLE, REMBG_SESSION
    try:
        from rembg import remove as _r, new_session
        REMBG_SESSION   = new_session("u2netp")
        REMBG_AVAILABLE = True
        logger.info("✓ rembg u2netp loaded (background thread)")
    except Exception as e:
        logger.warning(f"rembg not available: {e}")

import threading as _threading
try:
    import rembg as _rembg_pkg  # noqa — just check if importable
    _threading.Thread(target=_init_rembg, daemon=True).start()
    logger.info("rembg: loading u2netp in background...")
except ImportError:
    logger.warning("rembg package missing — GrabCut only mode")

# ── OpenCV cascades ──────────────────────────────────────────────────────────
_FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_EYE  = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

app = FastAPI(title="ID Photo Pro API v4", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup_event():
    logger.info("=== ID Photo Pro v4 starting up ===")
    logger.info(f"OpenCV: {cv2.__version__}")
    logger.info("Server ready — rembg loading in background if available")

# ════════════════════════════════════════════════════════════════
# DATABASE TIÊU CHUẨN ICAO (30+ quốc gia)
# ════════════════════════════════════════════════════════════════
PHOTO_SIZES = {
    "vn_cccd":       {"W":22,"H":28,"px_w":260,"px_h":330,"label":"CCCD/CMND VN",          "hr":0.72,"el":0.42,"bg":"white"},
    "the_3x4":       {"W":30,"H":40,"px_w":354,"px_h":472,"label":"Ảnh 3×4 cm",            "hr":0.72,"el":0.42,"bg":"white"},
    "the_4x6":       {"W":40,"H":60,"px_w":472,"px_h":709,"label":"Ảnh 4×6 cm",            "hr":0.72,"el":0.42,"bg":"white"},
    "the_2x3":       {"W":20,"H":30,"px_w":236,"px_h":354,"label":"Ảnh 2×3 cm",            "hr":0.72,"el":0.42,"bg":"white"},
    "chung_minh":    {"W":22,"H":28,"px_w":260,"px_h":330,"label":"CCCD/CMND",             "hr":0.72,"el":0.42,"bg":"white"},
    "passport":      {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Hộ chiếu 35×45mm",     "hr":0.75,"el":0.43,"bg":"white"},
    "visa_us":       {"W":51,"H":51,"px_w":600,"px_h":600,"label":"US Passport/Visa",      "hr":0.65,"el":0.40,"bg":"white"},
    "uk_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"UK Passport",           "hr":0.75,"el":0.43,"bg":"light_grey"},
    "eu_schengen":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"EU / Schengen",         "hr":0.76,"el":0.44,"bg":"white"},
    "de_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Đức",                   "hr":0.76,"el":0.44,"bg":"light_grey"},
    "fr_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Pháp",                  "hr":0.76,"el":0.44,"bg":"light_grey"},
    "ca_passport":   {"W":50,"H":70,"px_w":591,"px_h":827,"label":"Canada",                "hr":0.68,"el":0.40,"bg":"white"},
    "jp_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Nhật Bản",              "hr":0.75,"el":0.43,"bg":"white"},
    "jp_visa":       {"W":45,"H":45,"px_w":531,"px_h":531,"label":"Visa Nhật",             "hr":0.70,"el":0.42,"bg":"white"},
    "kr_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Hàn Quốc",              "hr":0.75,"el":0.43,"bg":"white"},
    "cn_visa":       {"W":33,"H":48,"px_w":390,"px_h":567,"label":"Visa TQ",               "hr":0.72,"el":0.43,"bg":"white"},
    "in_passport":   {"W":51,"H":51,"px_w":600,"px_h":600,"label":"Ấn Độ",                 "hr":0.65,"el":0.40,"bg":"white"},
    "sg_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Singapore",             "hr":0.75,"el":0.43,"bg":"white"},
    "au_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Úc",                    "hr":0.75,"el":0.43,"bg":"white"},
    "nz_passport":   {"W":35,"H":45,"px_w":413,"px_h":531,"label":"New Zealand",           "hr":0.75,"el":0.43,"bg":"white"},
    "ae_visa":       {"W":35,"H":45,"px_w":413,"px_h":531,"label":"UAE / Dubai",           "hr":0.75,"el":0.43,"bg":"white"},
    "visa_schengen": {"W":35,"H":45,"px_w":413,"px_h":531,"label":"Visa Schengen",         "hr":0.76,"el":0.44,"bg":"white"},
}

BG_COLORS = {
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
def pil_to_cv(img): return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
def cv_to_pil(img): return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
def img_to_bytes(img, fmt="JPEG", quality=95):
    buf = io.BytesIO()
    if fmt.upper()=="JPEG": img = img.convert("RGB")
    img.save(buf, format=fmt, quality=quality, dpi=(300,300))
    return buf.getvalue()

# ════════════════════════════════════════════════════════════════
# 1. NHẬN DIỆN KHUÔN MẶT (OpenCV)
# ════════════════════════════════════════════════════════════════
def detect_face(img_np):
    gray = cv2.equalizeHist(cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY))
    for scale, neighbors in [(1.05,3),(1.1,4),(1.15,5)]:
        faces = _FACE.detectMultiScale(gray, scale, neighbors,
            minSize=(max(30, img_np.shape[1]//20), max(30, img_np.shape[0]//20)),
            flags=cv2.CASCADE_SCALE_IMAGE)
        if len(faces) > 0: break
    if len(faces)==0: return None

    face = max(faces, key=lambda f: f[2]*f[3])
    x,y,w,h = map(int, face)
    face_roi = gray[y:y+h//2, x:x+w]
    eyes = _EYE.detectMultiScale(face_roi, 1.1, 5, minSize=(10,10))
    eyes_coords = [(int(x+ex+ew//2), int(y+ey+eh//2)) for ex,ey,ew,eh in eyes]
    return {"x":x,"y":y,"w":w,"h":h,"cx":x+w//2,"cy":y+h//2,"eyes":eyes_coords[:2]}

# ════════════════════════════════════════════════════════════════
# 2. XOAY THẲNG MẶT
# ════════════════════════════════════════════════════════════════
def rotate_face(img_np, face):
    eyes = face.get("eyes",[])
    if len(eyes) < 2: return img_np
    e1,e2 = sorted(eyes, key=lambda e: e[0])
    angle = math.degrees(math.atan2(e2[1]-e1[1], e2[0]-e1[0]))
    if abs(angle) < 0.5: return img_np
    cx = (e1[0]+e2[0])/2; cy = (e1[1]+e2[1])/2
    h,w = img_np.shape[:2]
    M = cv2.getRotationMatrix2D((cx,cy), angle, 1.0)
    return cv2.warpAffine(img_np, M, (w,h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101)

# ════════════════════════════════════════════════════════════════
# 3. XÓA NỀN (3 tầng fallback)
# ════════════════════════════════════════════════════════════════
def remove_bg_removebg_api(pil_img):
    """Remove.bg API — chất lượng cao nhất"""
    if not REMOVE_BG_KEY: return None
    try:
        buf = io.BytesIO(); pil_img.save(buf, "PNG"); buf.seek(0)
        resp = http_requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={"image_file": buf},
            data={"size": "auto"},
            headers={"X-Api-Key": REMOVE_BG_KEY},
            timeout=30
        )
        if resp.status_code == 200:
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        logger.warning(f"remove.bg error: {resp.status_code}")
    except Exception as e:
        logger.warning(f"remove.bg failed: {e}")
    return None

def remove_bg_rembg(pil_img):
    """rembg u2netp — fallback 1"""
    if not REMBG_AVAILABLE or REMBG_SESSION is None: return None
    try:
        from rembg import remove as rembg_remove
        result = rembg_remove(img_to_bytes(pil_img,"PNG"), session=REMBG_SESSION)
        return Image.open(io.BytesIO(result)).convert("RGBA")
    except Exception as e:
        logger.warning(f"rembg failed: {e}")
    return None

def remove_bg_grabcut(img_np, face):
    """OpenCV GrabCut — fallback 2"""
    h,w = img_np.shape[:2]
    if face is None:
        mx,my,mw,mh = int(w*.15),int(h*.05),int(w*.70),int(h*.90)
    else:
        fx,fy,fw,fh = face["x"],face["y"],face["w"],face["h"]
        px,pt,pb = int(fw*1.2), int(fh*0.4), int(fh*3.5)
        mx=max(0,fx-px); my=max(0,fy-pt)
        mw=min(w-mx,fw+2*px); mh=min(h-my,fh+pt+pb)

    mask=np.zeros((h,w),np.uint8)
    bgd=np.zeros((1,65),np.float64); fgd=np.zeros((1,65),np.float64)
    try:
        cv2.grabCut(img_np,mask,(mx,my,mw,mh),bgd,fgd,5,cv2.GC_INIT_WITH_RECT)
        fg=np.where((mask==2)|(mask==0),0,255).astype(np.uint8)
    except:
        fg=np.zeros((h,w),np.uint8); fg[my:my+mh,mx:mx+mw]=255

    ker=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7))
    fg=cv2.morphologyEx(fg,cv2.MORPH_CLOSE,ker,iterations=2)
    fg=cv2.GaussianBlur(fg,(5,5),0)
    _,fg=cv2.threshold(fg,127,255,cv2.THRESH_BINARY)

    rgba=cv2.cvtColor(img_np,cv2.COLOR_BGR2RGBA); rgba[:,:,3]=fg
    return Image.fromarray(rgba).convert("RGBA")

def remove_background(pil_img, face):
    """Thử lần lượt: Remove.bg → rembg → GrabCut"""
    result = remove_bg_removebg_api(pil_img)
    if result: logger.info("✓ Used Remove.bg API"); return result, "removebg_api"

    result = remove_bg_rembg(pil_img)
    if result: logger.info("✓ Used rembg"); return result, "rembg"

    img_cv = pil_to_cv(pil_img)
    result = remove_bg_grabcut(img_cv, face)
    logger.info("✓ Used GrabCut"); return result, "grabcut"

def apply_bg_color(img_rgba, bg_rgb):
    """Thêm màu nền vào ảnh RGBA"""
    bg = Image.new("RGBA", img_rgba.size, bg_rgb+(255,))
    bg.paste(img_rgba, mask=img_rgba.split()[3])
    return bg.convert("RGB")

# ════════════════════════════════════════════════════════════════
# 4. CROP CHUẨN ICAO (IED-based)
# ════════════════════════════════════════════════════════════════
def icao_crop(img, face, target_w, target_h, hr, el):
    """
    Crop theo chuẩn ICAO dùng Inter-Eye Distance (IED).
    Nếu không có face → crop giữa với đúng tỷ lệ.
    """
    iw, ih = img.size
    if face is None:
        ratio = target_w/target_h
        if iw/ih > ratio:
            nw=int(ih*ratio); img=img.crop(((iw-nw)//2,0,(iw+nw)//2,ih))
        else:
            nh=int(iw/ratio); img=img.crop((0,(ih-nh)//2,iw,(ih+nh)//2))
        return img.resize((target_w,target_h),Image.LANCZOS)

    fx,fy,fw,fh = face["x"],face["y"],face["w"],face["h"]
    eyes = face.get("eyes",[])

    # IED
    if len(eyes)>=2:
        e1,e2=eyes[0],eyes[1]
        ied=math.sqrt((e2[0]-e1[0])**2+(e2[1]-e1[1])**2)
        eye_y=(e1[1]+e2[1])/2; eye_x=(e1[0]+e2[0])/2
    else:
        ied=fw*0.45; eye_y=fy+fh*0.35; eye_x=float(face["cx"])

    head_h = ied*1.8*1.55          # mắt→cằm × 1.8, ×1.55 cho đỉnh đầu
    crop_h = head_h / hr
    crop_w = crop_h * (target_w/target_h)
    crop_top  = eye_y - el*crop_h
    crop_left = eye_x - crop_w/2

    crop_left=max(0,min(crop_left, iw-crop_w))
    crop_top =max(0,min(crop_top,  ih-crop_h))
    crop_w=min(crop_w, float(iw)); crop_h=min(crop_h, float(ih))

    cropped=img.crop((int(crop_left),int(crop_top),
                      int(crop_left+crop_w),int(crop_top+crop_h)))
    return cropped.resize((target_w,target_h),Image.LANCZOS)

# ════════════════════════════════════════════════════════════════
# 5. ENHANCE CHẤT LƯỢNG
# ════════════════════════════════════════════════════════════════
def enhance(img):
    cv=pil_to_cv(img)
    cv=cv2.bilateralFilter(cv,5,40,40)   # noise reduction
    img=cv_to_pil(cv)
    img=ImageEnhance.Brightness(img).enhance(1.05)
    img=ImageEnhance.Contrast(img).enhance(1.12)
    img=ImageEnhance.Sharpness(img).enhance(2.0)
    img=ImageEnhance.Color(img).enhance(1.05)
    img=img.filter(ImageFilter.UnsharpMask(radius=0.8,percent=120,threshold=3))
    return img

# ════════════════════════════════════════════════════════════════
# 6. TẠO PDF TỜ IN A4 (như PhotoGov)
# ════════════════════════════════════════════════════════════════
def create_print_pdf(photo_pil, W_mm, H_mm, label):
    """
    Tạo PDF A4 landscape chứa lưới ảnh thẻ.
    Giống file 'For-Print-(A4).pdf' của PhotoGov.
    """
    buf = io.BytesIO()
    A4_W, A4_H = A4  # points (595.27 × 841.89)
    # Landscape
    page_w, page_h = A4_H, A4_W  # 841 × 595

    gap_mm    = 3   # khoảng cách giữa ảnh (mm)
    margin_mm = 10  # lề (mm)

    gap    = gap_mm    * mm
    margin = margin_mm * mm
    pw     = W_mm * mm
    ph     = H_mm * mm

    cols = max(1, int((page_w - 2*margin + gap) / (pw + gap)))
    rows = max(1, int((page_h - 2*margin + gap) / (ph + gap)))

    c = pdf_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setTitle(f"ID Photo — {label}")

    # Metadata
    c.setAuthor("ID Photo Pro")
    c.setSubject(f"{W_mm}×{H_mm}mm @ 300dpi — {label}")

    # Header
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin, page_h - margin + 4*mm, f"ID Photo: {label}  |  {W_mm}×{H_mm}mm  |  {cols}×{rows} photos  |  Print at 100% scale")

    # Lưu ảnh tạm vào buffer
    img_buf = io.BytesIO()
    photo_pil.convert("RGB").save(img_buf, "JPEG", quality=97, dpi=(300,300))
    img_buf.seek(0)

    from reportlab.lib.utils import ImageReader
    img_reader = ImageReader(img_buf)

    # Vẽ lưới ảnh
    for row in range(rows):
        for col in range(cols):
            x = margin + col*(pw+gap)
            y = page_h - margin - ph - row*(ph+gap)
            c.drawImage(img_reader, x, y, pw, ph, preserveAspectRatio=False)
            # Đường kẻ cắt nhẹ
            c.setStrokeColorRGB(.8,.8,.8); c.setLineWidth(0.3)
            c.rect(x, y, pw, ph, stroke=1, fill=0)

    # Footer
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(.5,.5,.5)
    c.drawCentredString(page_w/2, margin/2,
        "Print at actual size (100%) — do not scale to fit page")

    c.save()
    return buf.getvalue()

# ════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "version":      "4.0.0",
        "remove_bg_api": bool(REMOVE_BG_KEY),
        "rembg":        REMBG_AVAILABLE,
        "opencv":       cv2.__version__,
        "reportlab":    True,
        "bg_mode":      "remove.bg" if REMOVE_BG_KEY else ("rembg" if REMBG_AVAILABLE else "grabcut"),
    }

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": PHOTO_SIZES}

@app.post("/api/process")
async def process_photo(
    file:       UploadFile = File(...),
    size_key:   str   = Form("the_3x4"),
    bg_color:   str   = Form("white"),
    bg_hex:     str   = Form(""),
    do_enhance: bool  = Form(True),
    output_fmt: str   = Form("jpeg"),
    head_ratio: float = Form(0.0),
    eye_line:   float = Form(0.0),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    sz = PHOTO_SIZES.get(size_key, PHOTO_SIZES["passport"])
    hr = head_ratio if head_ratio > 0 else sz["hr"]
    el = eye_line   if eye_line   > 0 else sz["el"]
    tw, th = sz["px_w"], sz["px_h"]
    fmt = "jpeg" if output_fmt.lower()=="jpeg" else "png"

    # Màu nền
    if bg_hex and bg_hex.startswith("#") and len(bg_hex)==7:
        try:
            bg_rgb=(int(bg_hex[1:3],16),int(bg_hex[3:5],16),int(bg_hex[5:7],16))
        except:
            bg_rgb = BG_COLORS.get(bg_color,(255,255,255))
    else:
        bg_rgb = BG_COLORS.get(bg_color,(255,255,255))

    try:
        data = await file.read()
        pil  = Image.open(io.BytesIO(data)).convert("RGB")

        # Giới hạn input
        MAX_DIM=2400
        if max(pil.size)>MAX_DIM:
            r=MAX_DIM/max(pil.size)
            pil=pil.resize((int(pil.width*r),int(pil.height*r)),Image.LANCZOS)

        logger.info(f"process: {pil.size} → {size_key} bg={bg_color}")

        # ── 1. Nhận diện khuôn mặt ──────────────────────────────────
        img_cv = pil_to_cv(pil)
        face   = detect_face(img_cv)
        face_ok= face is not None

        # ── 2. Xoay thẳng ────────────────────────────────────────────
        if face_ok:
            img_cv = rotate_face(img_cv, face)
            face   = detect_face(img_cv) or face
            pil    = cv_to_pil(img_cv)

        # ── 3. Xóa nền ───────────────────────────────────────────────
        img_nobg, bg_mode = remove_background(pil, face)

        # ── 4. Thêm màu nền ──────────────────────────────────────────
        img_bg = apply_bg_color(img_nobg, bg_rgb)

        # ── 5. Crop ICAO ─────────────────────────────────────────────
        result = icao_crop(img_bg, face, tw, th, hr, el)

        # ── 6. Enhance ───────────────────────────────────────────────
        if do_enhance:
            result = enhance(result)

        # ── 7. Xuất PNG ──────────────────────────────────────────────
        png_bytes = img_to_bytes(result, fmt.upper())
        png_b64   = base64.b64encode(png_bytes).decode()

        # ── 8. Tạo PDF tờ in A4 ──────────────────────────────────────
        pdf_bytes = create_print_pdf(result, sz["W"], sz["H"], sz["label"])
        pdf_b64   = base64.b64encode(pdf_bytes).decode()

        return JSONResponse({
            "success":       True,
            "image_b64":     png_b64,
            "pdf_b64":       pdf_b64,
            "format":        fmt,
            "width":         tw,
            "height":        th,
            "size_label":    sz["label"],
            "face_detected": face_ok,
            "bg_mode":       bg_mode,
            "rembg_used":    bg_mode in ("rembg","removebg_api"),
        })

    except Exception as e:
        logger.error(f"process error: {e}", exc_info=True)
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
