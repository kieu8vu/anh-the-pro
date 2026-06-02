# 📸 Ứng Dụng Ảnh Thẻ Chuyên Nghiệp (ID Photo Pro)

Ứng dụng web + PWA chụp/upload ảnh, xử lý AI (xóa nền, thay màu, chuẩn hoá kích thước), xuất file ảnh thẻ chuẩn. Stack: **FastAPI (Python) + Vanilla JS PWA**.

---

## 🗺️ Tổng Quan Kiến Trúc

```
[Browser / PWA]  ←→  [FastAPI Backend]  ←→  [AI: rembg / Pillow]
      |                      |
  Netlify/CF             Render.com
```

---

## 📋 Các Bước Triển Khai

### Bước 1: Thiết lập Repository

### Bước 2: Backend AI (FastAPI)

### Bước 3: Deploy Backend (Render.com)

### Bước 4: Frontend PWA

### Bước 5: Deploy Frontend (Netlify)

---

## 🚀 Chạy Local

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
# Dùng live-server hoặc Python http.server
python3 -m http.server 3000
# Hoặc: npx live-server --port=3000
```

Truy cập: http://localhost:3000

---

## 🌐 URLs Production

- **Backend API**: https://anh-the-pro.onrender.com
- **Frontend**: https://anh-the-pro.netlify.app

---

## 📁 Cấu Trúc Thư Mục

```
anh-the-pro/
├── README.md
├── backend/
│   ├── main.py              # FastAPI app chính
│   ├── requirements.txt     # Python dependencies
│   ├── Procfile             # Render.com config
│   ├── render.yaml          # Render deploy config
│   └── .env.example         # Biến môi trường mẫu
└── frontend/
    ├── index.html           # App chính
    ├── manifest.json        # PWA manifest
    ├── sw.js                # Service Worker
    ├── netlify.toml         # Netlify config
    ├── _redirects           # Netlify redirects
    └── src/
        ├── app.js           # Logic chính
        ├── components/
        │   ├── camera.js    # Webcam capture
        │   ├── editor.js    # Chỉnh sửa ảnh
        │   └── preview.js   # Xem trước & xuất
        └── utils/
            ├── api.js       # Gọi backend API
            └── canvas.js    # Xử lý canvas
```
