FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rapid_aio.py .
COPY heretic_client.py .
COPY heretic_prompts ./heretic_prompts
COPY app.py .
COPY image_gallery.py gallery.js gallery.css .
COPY generation_progress.py progress.js progress.css .
COPY qwen_quality.py .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
