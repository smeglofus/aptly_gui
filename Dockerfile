FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV APTLY_API_URL=http://aptly:8080
EXPOSE 8080

CMD ["uvicorn", "aptly_gui.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
