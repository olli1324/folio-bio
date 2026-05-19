# Folio production image.
#
# Why a Dockerfile and not Render's bare Python runtime: WeasyPrint depends
# on Pango + Cairo + libffi as system libraries (apt packages on Debian).
# The bare Python build environment doesn't install those, so the server
# PDF endpoint would fail at import time. With this Dockerfile, Render's
# build step runs `apt-get install` for us.

FROM python:3.13-slim

# System libraries WeasyPrint needs. `libpango-1.0-0`, `libpangoft2-1.0-0`,
# and `libcairo2` are the runtime libs; `libffi-dev` is required for cffi
# (the FFI layer WeasyPrint uses to call into Pango). `fonts-dejavu-core`
# gives us a baseline font family so PDFs render even before Google Fonts
# loads.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so Docker can cache the layer when only source
# files change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy the application code.
COPY . .

# Render injects PORT; we read it in app.py. EXPOSE is just documentation.
EXPOSE 8000

CMD ["python", "app.py"]
