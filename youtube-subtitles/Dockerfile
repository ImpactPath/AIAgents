FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Hugging Face Spaces (Docker SDK) runs containers as user id 1000.
RUN useradd --create-home --uid 1000 user

WORKDIR /home/user/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user app ./app
COPY --chown=user:user static ./static

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

EXPOSE 7860

# PORT lets Render (or any host) override the port; Hugging Face Spaces uses the 7860 default.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
