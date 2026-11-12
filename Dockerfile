FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-stories.txt .
RUN pip install --no-cache-dir -r requirements-stories.txt
COPY job_store.py story_quality.py app.py stories.py story_captions.py story_validation.py story_writer.py STORY_RULES.md ./
COPY assets ./assets
COPY dist ./dist
RUN mkdir -p runtime/renders && chown -R 65534:65534 runtime
USER 65534:65534
ENV FRAMELET_FFMPEG=/usr/bin/ffmpeg
EXPOSE 8765
CMD ["python", "app.py", "--stories-only", "--host", "0.0.0.0"]
