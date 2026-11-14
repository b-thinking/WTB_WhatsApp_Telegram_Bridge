FROM docker.io/library/python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# RUN apt-get update \
#       && apt-get install -y --no-install-recommends \
#          ffmpeg \
#          ca-certificates 

RUN apt-get update \
      && apt-get install -y --no-install-recommends \
         libmagic1 ffmpeg ca-certificates

WORKDIR /app

COPY requirements.txt ./
COPY src/wtb.py ./src/
RUN python -m pip install --no-cache-dir --upgrade pip \
      && python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
      && chown -R appuser:appuser /app
USER appuser

CMD ["python", "src/wtb.py"]
