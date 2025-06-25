# Zeal PDF Utility API v2.0

A comprehensive PDF toolkit API with compression, trimming, merging, splitting, watermarking, password protection, and cache management.

## Authentication

All endpoints require an API key in the `x-api-key` header:
```bash
curl -H "x-api-key: your-api-key-here" ...
```

## Common Parameters

### Input Methods
- **File Upload**: Use `file` parameter with multipart form data
- **URL**: Use `file_url` parameter with direct PDF URL

### Return Types
- **`base64`** (default): Returns JSON with base64-encoded content
- **`binary`**: Returns direct file download
- **`url`**: Returns temporary URL (expires in 60 minutes)

## Endpoints

### 🗜️ PDF Compression
**POST** `/compress`

Compress PDF files with quality control and optional page limiting.

**Parameters:**
- `file` (file, optional): PDF file upload
- `file_url` (string, optional): Direct PDF URL
- `compression_level` (enum): `screen`, `ebook`, `printer`, `prepress` (default: `ebook`)
- `max_pages` (int, optional): Limit output to first X pages
- `return_type` (string): `base64`, `binary`, or `url`

**Example:**
```bash
curl -X POST "https://your-app.com/compress" \
  -H "x-api-key: your-key" \
  -F "file=@document.pdf" \
  -F "compression_level=ebook" \
  -F "return_type=url"
```

### ✂️ PDF Trimming
**POST** `/trim`

Extract specific page ranges from PDFs.

**Parameters:**
- `file` / `file_url`: Input PDF
- `start_page` (int): Starting page (1-indexed)
- `end_page` (int): Ending page (1-indexed)
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/trim" \
  -H "x-api-key: your-key" \
  -F "file_url=https://example.com/doc.pdf" \
  -F "start_page=5" \
  -F "end_page=10" \
  -F "return_type=base64"
```

### 🔗 PDF Merging
**POST** `/merge`

Combine multiple PDFs into a single document.

**Parameters:**
- `files` (array, optional): Multiple file uploads
- `file_urls` (string, optional): Comma-separated URLs
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/merge" \
  -H "x-api-key: your-key" \
  -F "files=@doc1.pdf" \
  -F "files=@doc2.pdf" \
  -F "return_type=url"

# Or with URLs:
curl -X POST "https://your-app.com/merge" \
  -H "x-api-key: your-key" \
  -F "file_urls=https://example.com/doc1.pdf,https://example.com/doc2.pdf" \
  -F "return_type=binary"
```

### 📄 PDF Splitting
**POST** `/split`

Split PDF into individual pages or specific page ranges.

**Parameters:**
- `file` / `file_url`: Input PDF
- `split_range` (string, optional): Page specification (e.g., "1,3-5,7")
- `return_type`: Output format

**Notes:**
- Without `split_range`: Splits all pages
- Single page: Returns PDF file
- Multiple pages: Returns ZIP archive

**Example:**
```bash
# Split specific pages
curl -X POST "https://your-app.com/split" \
  -H "x-api-key: your-key" \
  -F "file=@document.pdf" \
  -F "split_range=1,5-8,12" \
  -F "return_type=url"

# Split all pages
curl -X POST "https://your-app.com/split" \
  -H "x-api-key: your-key" \
  -F "file_url=https://example.com/doc.pdf" \
  -F "return_type=base64"
```

### 🖼️ Image to PDF
**POST** `/image-to-pdf`

Convert images to PDF with optional formatting.

**Parameters:**
- `file` / `file_url`: Input image (JPEG, PNG, etc.)
- `title` (string, optional): Title to display at top of page
- `fit_to_letter` (bool): Resize image to fit letter size (default: false)
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/image-to-pdf" \
  -H "x-api-key: your-key" \
  -F "file=@photo.jpg" \
  -F "title=Monthly Report" \
  -F "fit_to_letter=true" \
  -F "return_type=url"
```

### 🔢 Add Page Numbers
**POST** `/add-page-numbers`

Add page numbers to existing PDF.

**Parameters:**
- `file` / `file_url`: Input PDF
- `start_page` (int): Starting page number (default: 1)
- `skip_first` (bool): Skip numbering first page (default: false)
- `position` (string): `bottom-left`, `bottom-center`, `bottom-right`
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/add-page-numbers" \
  -H "x-api-key: your-key" \
  -F "file=@document.pdf" \
  -F "start_page=2" \
  -F "skip_first=true" \
  -F "position=bottom-center" \
  -F "return_type=binary"
```

### 📏 Resize to Letter
**POST** `/resize-to-letter`

Resize any PDF to standard letter size (8.5x11).

**Parameters:**
- `file` / `file_url`: Input PDF
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/resize-to-letter" \
  -H "x-api-key: your-key" \
  -F "file_url=https://example.com/oversized.pdf" \
  -F "return_type=url"
```

### 🔍 Extract Text (OCR)
**POST** `/extract-text`

Extract text from PDF using OCR for image-based content.

**Parameters:**
- `file` / `file_url`: Input PDF
- `ocr_images` (bool): Use OCR for image-based pages (default: true)

**Example:**
```bash
curl -X POST "https://your-app.com/extract-text" \
  -H "x-api-key: your-key" \
  -F "file=@scanned.pdf" \
  -F "ocr_images=true"
```

**Response:**
```json
{
  "total_pages": 3,
  "pages": [
    {"page": 1, "text": "Extracted text from page 1..."},
    {"page": 2, "text": "Extracted text from page 2..."}
  ],
  "full_text": "=== Page 1 ===\nExtracted text..."
}
```

### 🔖 Merge with Bookmarks
**POST** `/merge-with-bookmarks`

Merge PDFs and create bookmarks for each document.

**Parameters:**
- `files` (array, optional): Multiple file uploads
- `file_urls` (string, optional): Comma-separated URLs
- `titles` (string): Comma-separated bookmark titles (must match file order)
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/merge-with-bookmarks" \
  -H "x-api-key: your-key" \
  -F "files=@chapter1.pdf" \
  -F "files=@chapter2.pdf" \
  -F "titles=Introduction,Main Content" \
  -F "return_type=url"

# Or with URLs:
curl -X POST "https://your-app.com/merge-with-bookmarks" \
  -H "x-api-key: your-key" \
  -F "file_urls=https://example.com/doc1.pdf,https://example.com/doc2.pdf" \
  -F "titles=Chapter 1,Chapter 2" \
  -F "return_type=binary"
```

### 🏷️ PDF Watermarking
**POST** `/watermark`

Add text watermarks to all PDF pages.

**Parameters:**
- `file` / `file_url`: Input PDF
- `text` (string): Watermark text
- `opacity` (float): 0.0 to 1.0 (default: 0.3)
- `position` (enum): `center`, `top_left`, `top_right`, `bottom_left`, `bottom_right`
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/watermark" \
  -H "x-api-key: your-key" \
  -F "file=@document.pdf" \
  -F "text=CONFIDENTIAL" \
  -F "opacity=0.5" \
  -F "position=center" \
  -F "return_type=binary"
```

### 🔒 Password Protection
**POST** `/password-protect`

Encrypt PDF with password protection.

**Parameters:**
- `file` / `file_url`: Input PDF
- `password` (string): Protection password
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/password-protect" \
  -H "x-api-key: your-key" \
  -F "file=@document.pdf" \
  -F "password=secret123" \
  -F "return_type=url"
```

### 🔓 Password Removal
**POST** `/password-remove`

Remove password protection from encrypted PDFs.

**Parameters:**
- `file` / `file_url`: Input PDF (encrypted)
- `password` (string): Current password
- `return_type`: Output format

**Example:**
```bash
curl -X POST "https://your-app.com/password-remove" \
  -H "x-api-key: your-key" \
  -F "file=@encrypted.pdf" \
  -F "password=secret123" \
  -F "return_type=base64"
```

## Cache Management

### 📊 Cache Status
**GET** `/cache/status`

View all cached files with metadata.

**Example:**
```bash
curl -H "x-api-key: your-key" "https://your-app.com/cache/status"
```

**Response:**
```json
{
  "file_count": 15,
  "total_size_kb": 2048.5,
  "files": [
    {
      "filename": "out_abc123.pdf",
      "size_kb": 145.2,
      "created_at": "2025-06-25T10:30:00Z",
      "age_minutes": 5.5
    }
  ]
}
```

### 🗑️ Delete Specific Files
**DELETE** `/delete`

Delete specific cached files.

**Parameters:**
- `filenames` (string): Comma-separated filenames

**Example:**
```bash
curl -X DELETE "https://your-app.com/delete" \
  -H "x-api-key: your-key" \
  -F "filenames=out_abc123.pdf,in_def456.pdf"
```

### 🧹 Clear Cache
**DELETE** `/clear-cache`

Delete all cached files or files older than specified time.

**Parameters:**
- `older_than_minutes` (int, optional): Only delete files older than X minutes

**Examples:**
```bash
# Clear all files
curl -X DELETE "https://your-app.com/clear-cache" \
  -H "x-api-key: your-key"

# Clear files older than 30 minutes
curl -X DELETE "https://your-app.com/clear-cache" \
  -H "x-api-key: your-key" \
  -F "older_than_minutes=30"
```

## Response Formats

### Base64 Response
```json
{
  "filename": "processed.pdf",
  "content_type": "application/pdf",
  "content_base64": "JVBERi0xLjQK...",
  "original_size_kb": 1024.0,
  "processed_size_kb": 512.0,
  "original_size_mb": 1.0,
  "processed_size_mb": 0.5,
  "mb_saved": 0.5,
  "percent_reduction": 50.0
}
```

### URL Response
```json
{
  "url": "https://your-app.com/temp/processed_abc123.pdf",
  "expires_at": "2025-06-25T11:30:00Z",
  "original_size_kb": 1024.0,
  "processed_size_kb": 512.0,
  "mb_saved": 0.5,
  "percent_reduction": 50.0
}
```

### Binary Response
Direct file download with appropriate content-type headers.

## Error Handling

**4xx Client Errors:**
- `400`: Invalid parameters or request format
- `403`: Invalid or missing API key

**5xx Server Errors:**
- `500`: Processing errors (corrupted PDFs, system issues)

**Error Response Format:**
```json
{
  "detail": "Error description"
}
```

## Rate Limits & File Cleanup

- Temporary files are automatically cleaned up after processing
- Temporary URLs expire after 60 minutes
- Use cache management endpoints to monitor and clean storage
- No built-in rate limiting (implement at reverse proxy level)

## Development

### Local Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export API_KEY="your-development-key"
export BASE_URL="http://localhost:8000"

# Run server
uvicorn app.main:app --reload --port 8000
```

### Docker Deployment
```bash
# Build image
docker build -t zeal-pdf-utility .

# Run container
docker run -p 8080:8080 \
  -e API_KEY="your-production-key" \
  -e BASE_URL="https://your-domain.com" \
  zeal-pdf-utility
```

## Security Notes

- Always use HTTPS in production
- Rotate API keys regularly
- Monitor cache directory size
- Consider implementing request logging
- Validate file types and sizes at reverse proxy level