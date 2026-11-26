FROM python:3.12-slim

WORKDIR /app

# FriBidi for Pillow's raqm text shaping (Hebrew text slides, core/slide_renderer).
# Pillow's wheel bundles raqm + HarfBuzz and loads libfribidi at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The background-removal model (core/background_removal): rembg's u2netp,
# pinned by SHA-256 — the build fails rather than bake in another file.
RUN mkdir -p /app/models && python -c "import hashlib, urllib.request; \
url = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx'; \
data = urllib.request.urlopen(url, timeout=120).read(); \
digest = hashlib.sha256(data).hexdigest(); \
assert digest == '309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8', digest; \
open('/app/models/u2netp.onnx', 'wb').write(data)"
ENV BG_MODEL_PATH=/app/models/u2netp.onnx

# The Hebrew font library (core/font_library): every Google Fonts family with
# the Hebrew subset, every weight it ships. The set is PINNED in
# assets/fonts/hebrew-fonts.lock.json (URL + SHA-256 per file) — the build
# fails rather than ship without a pinned family or with a different file.
COPY assets/fonts/hebrew-fonts.lock.json /app/assets/fonts/hebrew-fonts.lock.json
COPY scripts/fetch_hebrew_fonts.py /app/scripts/fetch_hebrew_fonts.py
RUN python scripts/fetch_hebrew_fonts.py --install --dest /app/assets/fonts/library

COPY . .

# The FINAL image against the lock, read back from disk (after the code is
# copied, so nothing copied over the library can slip through): every pinned
# font file with its SHA-256, nothing unpinned. A mismatch fails the build —
# an image without its fonts would silently draw every brand in Rubik.
RUN python scripts/fetch_hebrew_fonts.py --check --dest /app/assets/fonts/library

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "core.api_server:app", "--host", "0.0.0.0", "--port", "8080"]
