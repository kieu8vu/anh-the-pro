"""
ID Photo Pro — Backend v7.0
════════════════════════════════════════════════════════════════════════
Engine chính: PicWish Advanced ID Photo API
  - Face detection + ICAO crop + background removal trong 1 API call
  - Chất lượng chuyên nghiệp, không cần local AI models
  - Hỗ trợ 100+ quốc gia, outfit variants, beauty enhancement

Fallback (khi không có PICWISH_KEY):
  - Remove.bg API → xóa nền AI + local crop
  - rembg local → xóa nền local
  - Color-based → nền đơn sắc
  - Keep original → giữ nguyên

Endpoints:
  POST /api/process  — xử lý ảnh thẻ
  GET  /api/sizes    — danh sách kích thước
  GET  /health       — health check
════════════════════════════════════════════════════════════════════════
"""

import io, base64, logging, math, os, time, json
from typing import Optional, Tuple, Dict

import cv2
import numpy as np
import requests as http_req
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

# ── API Keys ──────────────────────────────────────────────────────────────────
PICWISH_KEY    = os.environ.get("PICWISH_KEY", "")
REMOVE_BG_KEY  = os.environ.get("REMOVE_BG_KEY", "")
PICWISH_URL    = "https://techsz.aoscdn.com/api/tasks/visual/external/idphoto"

# ── rembg fallback ────────────────────────────────────────────────────────────
REMBG_SESSION   = None
REMBG_AVAILABLE = False

def _load_rembg():
    global REMBG_SESSION, REMBG_AVAILABLE
    try:
        from rembg import new_session
        for m in ["u2netp", "u2net"]:
            try:
                REMBG_SESSION = new_session(m)
                REMBG_AVAILABLE = True
                logger.info(f"✓ rembg {m}")
                return
            except: pass
    except: pass

_load_rembg()

# ── OpenCV ────────────────────────────────────────────────────────────────────
_FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_EYE  = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

app = FastAPI(title="ID Photo Pro v7", version="7.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ════════════════════════════════════════════════════════════════════════
# PHOTO SIZE DATABASE
# ════════════════════════════════════════════════════════════════════════
SIZES = {
    "vn_cccd":      {"W":22,"H":28,"pw":260,"ph":330, "label":"CCCD/CMND VN",       "hr":0.72,"el":0.42,"picwish_spec":"VN"},
    "the_3x4":      {"W":30,"H":40,"pw":354,"ph":472, "label":"Ảnh 3×4 cm",         "hr":0.72,"el":0.42,"picwish_spec":"VN"},
    "the_4x6":      {"W":40,"H":60,"pw":472,"ph":709, "label":"Ảnh 4×6 cm",         "hr":0.72,"el":0.42,"picwish_spec":"VN"},
    "the_2x3":      {"W":20,"H":30,"pw":236,"ph":354, "label":"Ảnh 2×3 cm",         "hr":0.72,"el":0.42,"picwish_spec":"VN"},
    "chung_minh":   {"W":22,"H":28,"pw":260,"ph":330, "label":"CCCD/CMND",          "hr":0.72,"el":0.42,"picwish_spec":"VN"},
    "passport":     {"W":35,"H":45,"pw":413,"ph":531, "label":"Hộ chiếu 35×45mm",  "hr":0.75,"el":0.43,"picwish_spec":"EU"},
    "visa_us":      {"W":51,"H":51,"pw":600,"ph":600, "label":"US Passport/Visa",   "hr":0.65,"el":0.40,"picwish_spec":"US"},
    "uk_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"UK Passport",        "hr":0.75,"el":0.43,"picwish_spec":"GB"},
    "eu_schengen":  {"W":35,"H":45,"pw":413,"ph":531, "label":"EU/Schengen",        "hr":0.76,"el":0.44,"picwish_spec":"EU"},
    "de_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Đức",                "hr":0.76,"el":0.44,"picwish_spec":"DE"},
    "fr_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Pháp",               "hr":0.76,"el":0.44,"picwish_spec":"FR"},
    "ca_passport":  {"W":50,"H":70,"pw":591,"ph":827, "label":"Canada",             "hr":0.68,"el":0.40,"picwish_spec":"CA"},
    "jp_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Nhật Bản",           "hr":0.75,"el":0.43,"picwish_spec":"JP"},
    "jp_visa":      {"W":45,"H":45,"pw":531,"ph":531, "label":"Visa Nhật",          "hr":0.70,"el":0.42,"picwish_spec":"JP"},
    "kr_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Hàn Quốc",           "hr":0.75,"el":0.43,"picwish_spec":"KR"},
    "cn_visa":      {"W":33,"H":48,"pw":390,"ph":567, "label":"Visa TQ",            "hr":0.72,"el":0.43,"picwish_spec":"CN"},
    "in_passport":  {"W":51,"H":51,"pw":600,"ph":600, "label":"Ấn Độ",              "hr":0.65,"el":0.40,"picwish_spec":"IN"},
    "sg_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Singapore",          "hr":0.75,"el":0.43,"picwish_spec":"SG"},
    "au_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"Úc",                 "hr":0.75,"el":0.43,"picwish_spec":"AU"},
    "nz_passport":  {"W":35,"H":45,"pw":413,"ph":531, "label":"New Zealand",        "hr":0.75,"el":0.43,"picwish_spec":"NZ"},
    "ae_visa":      {"W":35,"H":45,"pw":413,"ph":531, "label":"UAE/Dubai",          "hr":0.75,"el":0.43,"picwish_spec":"AE"},
    "visa_schengen":{"W":35,"H":45,"pw":413,"ph":531, "label":"Visa Schengen",      "hr":0.76,"el":0.44,"picwish_spec":"EU"},
}

BG_MAP = {
    "white":      (255,255,255),
    "light_grey": (240,240,240),
    "light_blue": (214,228,240),
    "blue":       (67,114,196),
    "red":        (204,0,0),
}

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.bool_):    return bool(obj)
        if isinstance(obj, np.integer):  return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray):  return obj.tolist()
        return super().default(obj)

# ── Utils ─────────────────────────────────────────────────────────────────────
def to_cv(pil): return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
def to_pil(cv_img): return Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def img_to_bytes(pil, fmt="JPEG", q=95):
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, format=fmt, quality=q, dpi=(300,300))
    return buf.getvalue()

def bytes_to_b64(b): return base64.b64encode(b).decode()

def url_to_pil(url: str) -> Optional[Image.Image]:
    """Tải ảnh từ URL về PIL Image."""
    try:
        resp = http_req.get(url, timeout=30)
        if resp.status_code == 200:
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        logger.warning(f"url_to_pil failed: {e}")
    return None

def export_jpeg_300dpi(pil, min_kb=300, max_kb=1000):
    """Xuất JPEG @ 300DPI, binary search quality để đạt target size."""
    img = pil.convert("RGB")
    lo, hi, best = 60, 97, None
    for _ in range(8):
        mid = (lo+hi)//2
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=mid, dpi=(300,300), optimize=True)
        kb = buf.tell()//1024
        if kb < min_kb: lo = mid+1
        elif kb > max_kb: hi = mid-1
        else: best = (buf.getvalue(), mid, kb); break
        best = (buf.getvalue(), mid, kb)
    if not best:
        buf = io.BytesIO(); img.save(buf,"JPEG",quality=85,dpi=(300,300))
        best = (buf.getvalue(), 85, buf.tell()//1024)
    return best[0], {"quality":best[1],"size_kb":best[2],"dpi":300,"in_range":bool(min_kb<=best[2]<=max_kb)}

# ════════════════════════════════════════════════════════════════════════
# ENGINE 1: PicWish Advanced ID Photo API
# ════════════════════════════════════════════════════════════════════════
def process_via_picwish(
    img_bytes: bytes,
    sz: dict,
    bg_rgb: Tuple,
    do_beauty: bool = True,
) -> Optional[Tuple[Image.Image, str]]:
    """
    Gọi PicWish Advanced ID Photo API.
    Một call xử lý tất cả: face detect → crop → remove bg → add bg color.
    
    Trả về (result_image, "picwish") hoặc None nếu thất bại.
    """
    if not PICWISH_KEY:
        return None

    # Convert màu nền sang hex string
    bg_hex_str = "{:02x}{:02x}{:02x}".format(*bg_rgb)
    spec       = sz.get("picwish_spec", "EU")
    tw, th     = sz["pw"], sz["ph"]

    # Build form data
    files   = {"image_file": ("photo.jpg", img_bytes, "image/jpeg")}
    payload = {
        "sync":     "1",               # synchronous mode
        "spec":     spec,              # country spec
        "bg_color": bg_hex_str,        # background color
        "size":     f"{tw}x{th}",      # output size in pixels
    }
    if do_beauty:
        payload["auto_bright"] = "1"
        payload["auto_smooth"] = "1"
        payload["auto_sharp"]  = "1"

    headers = {"X-API-KEY": PICWISH_KEY}

    try:
        logger.info(f"  → PicWish API: spec={spec} bg=#{bg_hex_str} size={tw}x{th}")
        resp = http_req.post(
            PICWISH_URL,
            headers=headers,
            files=files,
            data=payload,
            timeout=60,
        )
        data = resp.json()
        logger.info(f"  ← PicWish status={resp.status_code} code={data.get('status')}")

        if resp.status_code != 200 or data.get("status") != 200:
            logger.warning(f"  PicWish error: {data}")
            return None

        state = data.get("data", {}).get("state", 0)
        if state == 1:
            img_url = data["data"].get("image")
            if img_url:
                result = url_to_pil(img_url)
                if result:
                    # Resize về đúng kích thước target nếu PicWish trả khác
                    if result.size != (tw, th):
                        result = result.resize((tw, th), Image.LANCZOS)
                    logger.info(f"  ✓ PicWish success: {result.size}")
                    return result, "picwish"

        # Async: poll nếu sync trả về task_id
        task_id = data.get("data", {}).get("task_id")
        if task_id:
            return _picwish_poll(task_id, tw, th)

    except Exception as e:
        logger.warning(f"  PicWish exception: {e}")
    return None

def _picwish_poll(task_id: str, tw: int, th: int, max_wait: int = 30) -> Optional[Tuple]:
    """Polling PicWish task result."""
    headers = {"X-API-KEY": PICWISH_KEY}
    poll_url = f"{PICWISH_URL}/{task_id}"

    for i in range(max_wait):
        if i > 0: time.sleep(1)
        try:
            resp = http_req.get(poll_url, headers=headers, timeout=10)
            d    = resp.json().get("data", {})
            state = d.get("state", 0)
            if state == 1:
                img_url = d.get("image")
                if img_url:
                    result = url_to_pil(img_url)
                    if result:
                        if result.size != (tw, th):
                            result = result.resize((tw, th), Image.LANCZOS)
                        logger.info(f"  ✓ PicWish poll success ({i+1}s)")
                        return result, "picwish"
            elif state < 0:
                logger.warning(f"  PicWish poll error: {d}")
                return None
        except: pass
    logger.warning("  PicWish poll timeout")
    return None

# ════════════════════════════════════════════════════════════════════════
# ENGINE 2: FALLBACK PIPELINE
# (Remove.bg → rembg → color-based → keep original)
# ════════════════════════════════════════════════════════════════════════
def detect_face(img_np):
    H, W = img_np.shape[:2]
    scale = 1.0
    work  = img_np
    if max(H,W) > 800:
        scale = 800/max(H,W)
        work  = cv2.resize(img_np, (int(W*scale),int(H*scale)))
    dH,dW = work.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY))
    min_sz = (max(30,dW//15), max(30,dH//15))
    faces = []
    for sc,nb in [(1.05,3),(1.08,4),(1.12,5)]:
        f = _FACE.detectMultiScale(gray, sc, nb, minSize=min_sz)
        if len(f): faces=f; break
    if not len(faces): return None
    dx,dy,dw,dh = map(int, max(faces, key=lambda f:f[2]*f[3]))
    roi = gray[dy:dy+dh//2, dx:dx+dw]
    eyes_raw = _EYE.detectMultiScale(roi, 1.08, 4, minSize=(8,8))
    eyes = sorted([(int((dx+ex+ew//2)/scale), int((dy+ey+eh//2)/scale)) for ex,ey,ew,eh in eyes_raw], key=lambda e:e[0])[:2]
    inv = 1.0/scale
    x,y,w,h = int(dx*inv),int(dy*inv),int(dw*inv),int(dh*inv)
    return {"x":x,"y":y,"w":w,"h":h,"cx":x+w//2,"cy":y+h//2,"eyes":eyes}

def straighten(img_np, face):
    eyes = face.get("eyes",[])
    if len(eyes)<2: return img_np, 0.0
    e1,e2 = eyes[0],eyes[1]
    angle = math.degrees(math.atan2(e2[1]-e1[1], e2[0]-e1[0]))
    if abs(angle)<0.3: return img_np, 0.0
    cx,cy = (e1[0]+e2[0])/2, (e1[1]+e2[1])/2
    H,W = img_np.shape[:2]
    M = cv2.getRotationMatrix2D((cx,cy), angle, 1.0)
    return cv2.warpAffine(img_np, M, (W,H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101), angle

def icao_crop(pil, face, tw, th, hr, el):
    iw,ih = pil.size
    if face is None:
        r = tw/th
        if iw/ih>r: nw=int(ih*r); pil=pil.crop(((iw-nw)//2,0,(iw+nw)//2,ih))
        else: nh=int(iw/r); pil=pil.crop((0,(ih-nh)//2,iw,(ih+nh)//2))
        return pil.resize((tw,th),Image.LANCZOS)
    fx,fy,fw,fh = face["x"],face["y"],face["w"],face["h"]
    eyes = face.get("eyes",[])
    if len(eyes)>=2:
        e1,e2=eyes[0],eyes[1]
        ied=math.hypot(e2[0]-e1[0],e2[1]-e1[1])
        ey=(e1[1]+e2[1])/2; ex=(e1[0]+e2[0])/2
    else:
        ied=fw*0.45; ey=fy+fh*0.35; ex=float(face["cx"])
    head_h = ied*1.8*1.55
    crop_h = head_h/hr
    crop_w = crop_h*(tw/th)
    ct = ey-el*crop_h; cl = ex-crop_w/2
    # Centering fix
    crop_cx = cl+crop_w/2
    off = ex-crop_cx
    if abs(off)>crop_w*0.08: cl = max(0,min(cl+off*0.5, iw-crop_w))
    cl=max(0,min(cl,iw-crop_w)); ct=max(0,min(ct,ih-crop_h))
    crop_w=min(crop_w,float(iw)); crop_h=min(crop_h,float(ih))
    return pil.crop((int(cl),int(ct),int(cl+crop_w),int(ct+crop_h))).resize((tw,th),Image.LANCZOS)

def remove_bg_api(pil):
    if not REMOVE_BG_KEY: return None
    try:
        buf=io.BytesIO(); pil.save(buf,"PNG"); buf.seek(0)
        r=http_req.post("https://api.remove.bg/v1.0/removebg",
            files={"image_file":("p.png",buf,"image/png")},
            data={"size":"auto"}, headers={"X-Api-Key":REMOVE_BG_KEY}, timeout=30)
        if r.status_code==200:
            logger.info("  ✓ Remove.bg API")
            return Image.open(io.BytesIO(r.content)).convert("RGBA")
        logger.warning(f"  Remove.bg {r.status_code}")
    except Exception as e: logger.warning(f"  Remove.bg: {e}")
    return None

def remove_bg_rembg(pil):
    if not REMBG_AVAILABLE: return None
    try:
        from rembg import remove as fn
        raw=img_to_bytes(pil,"PNG"); result=fn(raw,session=REMBG_SESSION)
        logger.info("  ✓ rembg")
        return Image.open(io.BytesIO(result)).convert("RGBA")
    except Exception as e: logger.warning(f"  rembg: {e}"); return None

def remove_bg_color(pil):
    img=to_cv(pil); H,W=img.shape[:2]; s=max(15,min(30,W//15,H//15))
    corners=[img[0:s,0:s],img[0:s,W-s:W],img[H-s:H,0:s],img[H-s:H,W-s:W]]
    bg=np.mean([c.reshape(-1,3).mean(0) for c in corners],0)
    lab=cv2.cvtColor(img,cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_bgr=np.clip(bg,0,255).astype(np.uint8).reshape(1,1,3)
    bg_lab=cv2.cvtColor(bg_bgr,cv2.COLOR_BGR2LAB).astype(np.float32)[0,0]
    dist=np.sqrt(np.sum((lab-bg_lab)**2,2))
    stds=[float(np.std(c)) for c in corners]; unif=float(np.mean(stds))
    thresh=max(18,min(40,25+unif*0.4))
    fg=(dist>thresh).astype(np.uint8)*255
    ker=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9))
    fg=cv2.morphologyEx(fg,cv2.MORPH_CLOSE,ker,iterations=3)
    fg=cv2.morphologyEx(fg,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
    n,labels,stats,_=cv2.connectedComponentsWithStats(fg)
    if n>1: largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA])); fg=((labels==largest)*255).astype(np.uint8)
    if float(fg.sum())/(255*H*W)<0.10:
        logger.warning("  Color-based: fg<10%, keep original"); return pil.convert("RGBA")
    fg=cv2.GaussianBlur(fg,(7,7),0)
    rgba=cv2.cvtColor(img,cv2.COLOR_BGR2RGBA); rgba[:,:,3]=fg
    logger.info(f"  ✓ Color-based (thresh={thresh:.1f})")
    return Image.fromarray(rgba).convert("RGBA")

def clean_alpha(rgba):
    r,g,b,a=rgba.split(); an=np.array(a)
    an=cv2.erode(an,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)),iterations=1)
    an=cv2.GaussianBlur(an,(3,3),0)
    an=np.where(an<10,0,np.where(an>245,255,an))
    return Image.merge("RGBA",[r,g,b,Image.fromarray(an.astype(np.uint8))])

def apply_bg(rgba, bg_rgb):
    rgba=clean_alpha(rgba); W,H=rgba.size
    bg=Image.new("RGB",(W,H),bg_rgb); bg.paste(rgba,mask=rgba.split()[3])
    arr=np.array(bg); t=np.array(bg_rgb,np.uint8); s=5
    for sy,ey,sx,ex in [(0,s,0,s),(0,s,W-s,W),(H-s,H,0,s),(H-s,H,W-s,W)]: arr[sy:ey,sx:ex]=t
    return Image.fromarray(arr)

def remove_background_fallback(pil, face):
    """Fallback pipeline khi không có PicWish."""
    # Kiểm tra uniformity nền
    img_cv=to_cv(pil); H,W=img_cv.shape[:2]; s=20
    corners=[img_cv[0:s,0:s],img_cv[0:s,W-s:W],img_cv[H-s:H,0:s],img_cv[H-s:H,W-s:W]]
    unif=float(np.mean([np.std(c) for c in corners]))

    r = remove_bg_api(pil)
    if r: return r, "removebg_api"
    r = remove_bg_rembg(pil)
    if r: return r, "rembg"
    if unif < 40: return remove_bg_color(pil), "color_based"
    logger.info("  Keep original"); return pil.convert("RGBA"), "keep_original"

def enhance_photo(pil):
    cv=to_cv(pil); cv=cv2.bilateralFilter(cv,5,35,35)
    img=to_pil(cv)
    img=ImageEnhance.Brightness(img).enhance(1.03)
    img=ImageEnhance.Contrast(img).enhance(1.08)
    img=ImageEnhance.Sharpness(img).enhance(1.3)
    img=ImageEnhance.Color(img).enhance(1.03)
    img=img.filter(ImageFilter.UnsharpMask(radius=0.5,percent=80,threshold=4))
    return img

def check_quality(pil, face_ok, bg_mode):
    """Kiểm tra blur, noise, clothing, background."""
    gray=cv2.cvtColor(to_cv(pil),cv2.COLOR_BGR2GRAY)
    lap=float(cv2.Laplacian(gray,cv2.CV_64F).var())
    blur_ok=bool(lap>=50)
    blurred=cv2.GaussianBlur(gray,(5,5),0)
    noise=float(np.std(gray.astype(np.float32)-blurred.astype(np.float32)))
    noise_ok=bool(noise<12)
    # Background uniformity
    arr=np.array(pil); W,H=pil.size; s=20
    corners=[arr[0:s,0:s],arr[0:s,W-s:W],arr[H-s:H,0:s],arr[H-s:H,W-s:W]]
    cm=[float(np.mean(c)) for c in corners]
    bg_ok=bool(max(cm)-min(cm)<40)
    # Clothing
    y1,y2=int(H*0.55),H; x1,x2=int(W*0.2),int(W*0.8)
    cl_reg=to_cv(pil)[y1:y2,x1:x2]
    sat=float(np.mean(cv2.cvtColor(cl_reg,cv2.COLOR_BGR2HSV)[:,:,1])) if cl_reg.size>0 else 0
    cloth_ok=bool(sat<80)
    return {
        "blur":     {"score":round(lap,1),"level":"sharp" if lap>200 else "acceptable" if lap>=50 else "blurry","ok":blur_ok},
        "noise":    {"score":round(noise,1),"level":"clean" if noise<5 else "acceptable" if noise<12 else "noisy","ok":noise_ok},
        "background":{"uniform":bg_ok,"ok":bg_ok},
        "clothing": {"saturation":round(sat,1),"is_neutral":cloth_ok,"ok":cloth_ok},
        "overall":  bool(blur_ok and noise_ok and bg_ok),
    }

def make_pdf(photo, W_mm, H_mm, label):
    buf=io.BytesIO(); pw,ph=A4[1],A4[0]
    margin=10*mm; gap=3*mm; iw,ih=W_mm*mm,H_mm*mm
    cols=max(1,int((pw-2*margin+gap)/(iw+gap)))
    rows=max(1,int((ph-2*margin+gap)/(ih+gap)))
    c=pdf_canvas.Canvas(buf,pagesize=(pw,ph))
    c.setTitle(f"ID Photo — {label}")
    c.setFont("Helvetica-Bold",8)
    c.drawString(margin,ph-margin+3*mm,f"{label} | {W_mm}×{H_mm}mm | {cols}×{rows} ảnh | In 100%")
    ib=io.BytesIO(); photo.convert("RGB").save(ib,"JPEG",quality=97,dpi=(300,300)); ib.seek(0)
    ir=ImageReader(ib)
    c.setStrokeColorRGB(.7,.7,.7); c.setLineWidth(0.25)
    for row in range(rows):
        for col in range(cols):
            x=margin+col*(iw+gap); y=ph-margin-ih-row*(ih+gap)
            c.drawImage(ir,x,y,iw,ih,preserveAspectRatio=False)
            c.rect(x,y,iw,ih,stroke=1,fill=0)
    c.setFont("Helvetica",6); c.setFillColorRGB(.4,.4,.4)
    c.drawCentredString(pw/2,margin/3,"In ở kích thước thực 100% — không phóng to")
    c.save(); return buf.getvalue()

# ════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":        "ok",
        "version":       "7.0.0",
        "picwish":       bool(PICWISH_KEY),
        "remove_bg_api": bool(REMOVE_BG_KEY),
        "rembg":         REMBG_AVAILABLE,
        "opencv":        cv2.__version__,
        "engine":        "picwish" if PICWISH_KEY else ("remove.bg" if REMOVE_BG_KEY else "rembg" if REMBG_AVAILABLE else "color_based"),
    }

@app.get("/api/sizes")
async def get_sizes():
    return {"sizes": SIZES}

@app.post("/api/process")
async def process_photo(
    file:          UploadFile = File(...),
    size_key:      str   = Form("the_3x4"),
    bg_color:      str   = Form("white"),
    bg_hex:        str   = Form(""),
    do_enhance:    bool  = Form(True),
    do_variants:   bool  = Form(False),
    do_compliance: bool  = Form(True),
    output_fmt:    str   = Form("jpeg"),
    head_ratio:    float = Form(0.0),
    eye_line:      float = Form(0.0),
):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Chỉ chấp nhận file ảnh")

    sz  = SIZES.get(size_key, SIZES["passport"])
    hr  = head_ratio if head_ratio > 0 else sz["hr"]
    el  = eye_line   if eye_line   > 0 else sz["el"]
    tw, th = sz["pw"], sz["ph"]

    try:
        bg_rgb = hex_to_rgb(bg_hex) if (bg_hex and bg_hex.startswith("#") and len(bg_hex)==7) \
                 else BG_MAP.get(bg_color, (255,255,255))
    except:
        bg_rgb = BG_MAP.get(bg_color, (255,255,255))

    try:
        raw = await file.read()
        pil = Image.open(io.BytesIO(raw)).convert("RGB")

        # Giới hạn kích thước input
        MAX_DIM = 2000
        if max(pil.size) > MAX_DIM:
            sc = MAX_DIM/max(pil.size)
            pil = pil.resize((int(pil.width*sc),int(pil.height*sc)),Image.LANCZOS)

        logger.info(f"▶ process {pil.size} → {size_key} bg={bg_color}")

        engine   = "unknown"
        result   = None
        face_ok  = False
        angle    = 0.0

        # ══ ENGINE 1: PicWish (tốt nhất — 1 call xử lý tất cả) ══════════
        if PICWISH_KEY:
            img_bytes = img_to_bytes(pil, "JPEG", 95)
            pw_result = process_via_picwish(img_bytes, sz, bg_rgb, do_enhance)
            if pw_result:
                result, engine = pw_result
                face_ok = True  # PicWish luôn detect và crop đúng

        # ══ ENGINE 2: Fallback pipeline ═══════════════════════════════════
        if result is None:
            logger.info("  → Fallback pipeline")
            # 1. Face detect
            img_cv = to_cv(pil)
            face   = detect_face(img_cv)
            face_ok = face is not None

            # 2. Straighten
            if face_ok:
                img_cv, angle = straighten(img_cv, face)
                if abs(angle)>0.3: face = detect_face(img_cv) or face
                pil = to_pil(img_cv)

            # 3. ICAO Crop
            cropped = icao_crop(pil, face, tw, th, hr, el)

            # 4. Remove background
            nobg_rgba, engine = remove_background_fallback(cropped, face)

            # 5. Apply background
            result = apply_bg(nobg_rgba, bg_rgb)

            # 6. Enhance
            if do_enhance:
                result = enhance_photo(result)

            # 7. Whitening background
            if bg_rgb[0] >= 230:
                arr = np.array(result)
                arr[np.all(arr>=238, axis=2)] = list(bg_rgb)
                result = Image.fromarray(arr)

        # ══ Quality checks ════════════════════════════════════════════════
        quality_info = check_quality(result, face_ok, engine) if do_compliance else {}

        # ══ Export JPEG @ 300 DPI ═════════════════════════════════════════
        img_bytes, export_info = export_jpeg_300dpi(result, 300, 1000)
        img_b64 = bytes_to_b64(img_bytes)

        # ══ PDF tờ in ════════════════════════════════════════════════════
        pdf_bytes = make_pdf(result, sz["W"], sz["H"], sz["label"])
        pdf_b64   = bytes_to_b64(pdf_bytes)

        logger.info(f"  ✓ Done engine={engine} size={export_info['size_kb']}KB")

        resp_data = {
            "success":        True,
            "image_b64":      img_b64,
            "pdf_b64":        pdf_b64,
            "format":         "jpeg",
            "width":          tw,
            "height":         th,
            "size_label":     sz["label"],
            "face_detected":  bool(face_ok),
            "rotation_angle": round(float(angle), 2),
            "engine":         engine,
            "bg_mode":        engine,
            "quality":        quality_info,
            "export":         export_info,
            "spec_summary": {
                "format":     f"JPEG {tw}×{th}px",
                "background": "#{:02X}{:02X}{:02X}".format(*bg_rgb),
                "dpi":        "300 DPI",
                "file_size":  f"{export_info['size_kb']}KB",
            }
        }
        return JSONResponse(content=json.loads(json.dumps(resp_data, cls=NumpyEncoder)))

    except Exception as e:
        logger.error(f"process error: {e}", exc_info=True)
        raise HTTPException(500, f"Lỗi xử lý ảnh: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
