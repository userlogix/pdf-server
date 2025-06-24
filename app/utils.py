import subprocess, requests, os
from datetime import datetime, timedelta
from fastapi import HTTPException

API_KEY = os.environ.get("API_KEY")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
EXPIRATION_MINUTES = 60

def validate_api_key(key):
    if not key or key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

def compress_pdf(in_path, out_path, compression_level="ebook"):
    allowed = ["screen", "ebook", "printer", "prepress"]
    setting = f"/{compression_level}" if compression_level in allowed else "/ebook"

    subprocess.run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={setting}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={out_path}", in_path
    ], check=True)

def download_pdf(url, save_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, stream=True, headers=headers, timeout=60)
        content_type = r.headers.get("Content-Type", "")
        if r.status_code != 200 or not any(t in content_type for t in ["application/pdf", "application/octet-stream", "binary/octet-stream"]):
            raise HTTPException(status_code=400, detail=f"Failed to download PDF: {r.status_code}, content_type: {content_type}")

        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

def generate_temp_url(path):
    filename = os.path.basename(path)
    expires = datetime.utcnow() + timedelta(minutes=EXPIRATION_MINUTES)
    return f"{BASE_URL}/temp/{filename}", expires