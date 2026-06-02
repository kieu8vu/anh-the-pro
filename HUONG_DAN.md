# 🚀 Hướng Dẫn Triển Khai Chi Tiết (5 Bước)

---

## ✅ Bước 1: Thiết lập Repository

### 1.1 Tạo GitHub repo

```bash
# Trên GitHub.com → New Repository
# Tên: anh-the-pro
# Public hoặc Private đều được
# Tích vào "Add README" → Create
```

### 1.2 Clone và push code

```bash
git clone https://github.com/YOUR_USERNAME/anh-the-pro.git
cd anh-the-pro

# Copy toàn bộ file từ folder này vào:
# backend/  →  backend/
# frontend/ →  frontend/
# .gitignore, README.md, HUONG_DAN.md

git add .
git commit -m "feat: initial project structure"
git push origin main
```

### 1.3 Kiểm tra cấu trúc repo

```
anh-the-pro/          ← GitHub root
├── .gitignore
├── README.md
├── HUONG_DAN.md
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── render.yaml
│   ├── Procfile
│   └── .env.example
└── frontend/
    ├── index.html
    ├── manifest.json
    ├── sw.js
    ├── netlify.toml
    └── src/
        ├── app.js
        ├── utils/
        │   ├── api.js
        │   └── canvas.js
        └── components/
            ├── camera.js
            └── preview.js
```

---

## ✅ Bước 2: Xây dựng Backend AI (Python + FastAPI)

### 2.1 Tạo môi trường ảo

```bash
cd backend

# Python 3.11+ khuyến nghị
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
# hoặc: venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

> ⚠️ **Lưu ý**: `rembg` cần ~500MB cho model AI (u2net.onnx)
> Lần đầu chạy sẽ tự download model này (~5 phút tuỳ mạng)

### 2.2 Chạy dev server

```bash
uvicorn main:app --reload --port 8000
```

### 2.3 Kiểm tra API

```bash
# Health check
curl http://localhost:8000/health

# Xem danh sách kích thước
curl http://localhost:8000/api/sizes

# Test xóa nền (cần có file ảnh test.jpg)
curl -X POST http://localhost:8000/api/remove-bg \
  -F "file=@test.jpg" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); open('out.png','wb').write(__import__('base64').b64decode(d['image_b64']))"

# Test xử lý đầy đủ
curl -X POST http://localhost:8000/api/process \
  -F "file=@test.jpg" \
  -F "size_key=the_3x4" \
  -F "bg_color=white" \
  -F "enhance=true" \
  -F "output_fmt=jpeg" \
  | python3 -c "import sys,json,base64; d=json.load(sys.stdin); open('result.jpg','wb').write(base64.b64decode(d['image_b64']))"
```

### 2.4 Swagger UI

Mở http://localhost:8000/docs để xem và test API tương tác.

---

## ✅ Bước 3: Triển khai Backend lên Render.com

### 3.1 Tạo tài khoản Render

1. Vào https://render.com → Sign up (dùng GitHub account)
2. **Dashboard** → **New** → **Web Service**

### 3.2 Kết nối GitHub repo

1. Chọn repo `anh-the-pro`
2. Cấu hình:
   ```
   Name:         anh-the-pro-api
   Region:       Singapore (gần VN nhất)
   Branch:       main
   Root Dir:     backend
   Runtime:      Python 3
   Build Cmd:    pip install -r requirements.txt
   Start Cmd:    uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
3. **Instance Type**: Free (đủ dùng cho demo)
4. Click **Create Web Service**

### 3.3 Chờ deploy xong

- Lần đầu mất ~5–10 phút (tải model AI)
- Khi thấy `● Live` → copy URL: `https://anh-the-pro-api.onrender.com`

### 3.4 Cập nhật URL vào Frontend

Mở `frontend/index.html`, tìm dòng:
```javascript
window.API_BASE = "https://anh-the-pro-api.onrender.com";
```
→ Thay bằng URL thật từ Render.com của bạn

> ⚠️ **Free tier lưu ý**: Render free tier sẽ sleep sau 15 phút không dùng.
> Lần đầu gọi API sau sleep cần ~30 giây warm-up.
> Muốn không sleep → Upgrade lên $7/tháng hoặc dùng UptimeRobot ping định kỳ.

---

## ✅ Bước 4: Hoàn thiện Frontend (PWA)

### 4.1 Chạy Frontend local

```bash
cd frontend

# Option 1: Python built-in server
python3 -m http.server 3000

# Option 2: Node live-server (tự reload)
npx live-server --port=3000

# Option 3: VS Code Extension "Live Server"
```

Mở http://localhost:3000

### 4.2 Tạo icon PWA (cần để cài app)

Tạo 2 file icon trong `frontend/public/`:
- `icon-192.png` (192×192px)
- `icon-512.png` (512×512px)

Dùng công cụ online: https://realfavicongenerator.net

### 4.3 Test PWA

```
Chrome DevTools → Application tab:
- Manifest: kiểm tra manifest.json đã load
- Service Workers: kiểm tra sw.js đã register
- Storage: Cache Storage có các file được cache
- Lighthouse → PWA audit: điểm tốt là ≥ 90
```

### 4.4 Tính năng PWA

- ✅ Cài được lên màn hình điện thoại (Add to Home Screen)
- ✅ Chạy offline (assets được cache)
- ✅ Chụp ảnh bằng camera (getUserMedia API)
- ✅ Kéo thả file
- ✅ Tải file xuống (download attribute)

---

## ✅ Bước 5: Đưa Frontend lên Netlify

### Option A: Drag & Drop (Dễ nhất — 30 giây)

1. Vào https://app.netlify.com → Add new site → Deploy manually
2. Kéo thả **toàn bộ thư mục `frontend/`** vào vùng upload
3. Netlify tự tạo URL: `https://random-name-123.netlify.app`
4. (Tuỳ chọn) Site settings → Change site name → đổi tên đẹp hơn

### Option B: Deploy từ GitHub (Tự động re-deploy khi push)

1. **Netlify Dashboard** → Add new site → Import an existing project
2. Connect to GitHub → chọn repo `anh-the-pro`
3. Cấu hình:
   ```
   Base directory:   frontend
   Build command:    (để trống — không cần build)
   Publish directory: frontend
   ```
4. Click **Deploy site**

### Option C: Cloudflare Pages (Alternative)

```
1. https://pages.cloudflare.com → Create a project
2. Connect GitHub → chọn repo
3. Build settings:
   Framework preset: None
   Build command:    (để trống)
   Build output dir: frontend
4. Save and Deploy
```

### 5.1 Custom Domain (Tuỳ chọn)

```
Netlify → Domain settings → Add custom domain
→ Thêm CNAME: anh-the.yourdomain.com → <your-site>.netlify.app
→ Netlify tự cấp SSL miễn phí (Let's Encrypt)
```

### 5.2 Cập nhật CORS sau deploy

Sau khi có domain thật, cập nhật `backend/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://anh-the-pro.netlify.app",
        "https://yourdomain.com",
    ],
    ...
)
```
→ Push lên GitHub → Render auto re-deploy

---

## 🎯 Kiến trúc hoàn chỉnh

```
User (Browser / PWA Mobile)
        │ HTTPS
        ▼
┌─────────────────────────────┐
│   Netlify / Cloudflare Pages │  ← Static hosting (miễn phí)
│   frontend/index.html        │
│   + PWA (sw.js, manifest)    │
└──────────────┬──────────────┘
               │ POST /api/process
               │ HTTPS + CORS
               ▼
┌─────────────────────────────┐
│   Render.com (Singapore)    │  ← Python server ($0 free tier)
│   FastAPI + Uvicorn         │
│                             │
│   rembg (U2-Net AI)         │  ← Xóa nền bằng AI
│   Pillow (image ops)        │  ← Resize, crop, enhance
│   NumPy                     │
└─────────────────────────────┘
```

---

## 💡 Tips & Troubleshooting

### Backend không start được
```bash
# Kiểm tra version Python
python3 --version  # cần >= 3.9

# Cài lại dependencies
pip install --upgrade pip
pip install -r requirements.txt --no-cache-dir
```

### rembg lỗi khi cài
```bash
# macOS M1/M2 cần Rosetta hoặc native arm64
pip install rembg[gpu]  # nếu có CUDA GPU
pip install rembg       # CPU version (chậm hơn nhưng OK)
```

### CORS error trên browser
```
Thêm domain frontend vào allow_origins trong main.py
Hoặc tạm thời dùng allow_origins=["*"] để test
```

### Render.com free tier sleep
```
Ping endpoint /health mỗi 10 phút bằng UptimeRobot (miễn phí):
https://uptimerobot.com → New Monitor → HTTP → URL: https://your-api.onrender.com/health
```

### Camera không hoạt động
```
Chỉ hoạt động trên HTTPS hoặc localhost
→ Trên Netlify sẽ tự có HTTPS → OK
```

---

## 📊 Chi phí

| Dịch vụ        | Plan       | Giá          |
|----------------|------------|--------------|
| GitHub         | Free       | $0/tháng     |
| Render.com     | Free       | $0/tháng     |
| Netlify        | Free       | $0/tháng     |
| **Tổng cộng**  |            | **$0/tháng** |

> Nếu traffic lớn hơn → Render Starter $7/tháng, Netlify Pro $19/tháng
