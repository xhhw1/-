FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
COPY src /src
RUN npm run build


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements-infra.txt requirements-vision.txt ./
RUN pip install --no-cache-dir \
    -r requirements.txt \
    -r requirements-infra.txt \
    -r requirements-vision.txt

COPY src ./src
COPY fixtures ./fixtures
COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY pyproject.toml README.md ./

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "ai_visual_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
