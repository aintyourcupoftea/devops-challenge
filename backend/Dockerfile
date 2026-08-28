FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN useradd -r -s /bin/false app
COPY --from=deps /install /usr/local
COPY app.py ./
USER app
EXPOSE 3000
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:3000", "--access-logfile", "-", "app:app"]
