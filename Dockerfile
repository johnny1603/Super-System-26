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

COPY . .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "core.api_server:app", "--host", "0.0.0.0", "--port", "8080"]
