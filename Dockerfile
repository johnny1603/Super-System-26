FROM python:3.12-slim

WORKDIR /app

# FriBidi for Pillow's raqm text shaping (Hebrew text slides, core/slide_renderer).
# Pillow's wheel bundles raqm + HarfBuzz and loads libfribidi at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "core.api_server:app", "--host", "0.0.0.0", "--port", "8080"]
