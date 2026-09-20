# Music Phoenix -- Docker build for Render.
#
# Why Docker (Option B, Poke's bulletproof seam):
#   Render's NATIVE Python buildpack is a bare datacenter container: no ffmpeg
#   in $PATH, Python 3.10 (deprecated), and no project-level provisioning seam
#   can put a transcoder on the image.  PyTgCalls 2.3.3 streams audio through a
#   native WebRTC pipe that REQUIRES ffmpeg to transcode into Opus -- without
#   it, the VC worker starves at ~20s with zero Python traceback (exactly the
#   Render log Gums pasted).  A static-binary curl (Option A) is fragile; apt's
#   ffmpeg is the way the SDK expects it and lands in system $PATH.
#
# Base: python:3.11-slim -- ALSO upgrades Render off the deprecated 3.10 and
# onto the exact CPython line py-tgcalls 2.3.3 targets.

FROM python:3.11-slim

# ffmpeg + ffprobe onto system $PATH (the WebRTC pipe transcodes via these).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# main.py lives under tg_bot/ -- WORKDIR is /app so `from tg_bot.*` resolve
# (no pyproject => plain-import surface; repo root is sys.path[0] for the CMD).
CMD ["python", "tg_bot/main.py"]
