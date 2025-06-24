import subprocess, requests, os
from datetime import datetime, timedelta
from fastapi import HTTPException

API_KEY = os.environ.get("API_KEY")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
EXPIRATION_MINUTES = 60

def validate_api_key(key):
    if not key or key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

def compress_pdf(in_path, out_path):
    subprocess.run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
        "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={out_path}", in_path
    ], check=True)

def download_pdf(url, save_path):
    r = requests.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to download PDF")
    with open(save_path, "wb") as f:
        f.write(r.content)

def generate_temp_url(path):
    filename = os.path.basename(path)
    expires = datetime.utcnow() + timedelta(minutes=EXPIRATION_MINUTES)
    return f"{BASE_URL}/temp/{filename}", expires