FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# Compile translations before installing so the .mo files ship inside the package
# and never lag behind the .po they came from.
RUN pip install --no-cache-dir babel \
 && pybabel compile -d src/aptly_gui/i18n/locales \
 && pip install --no-cache-dir .

ENV APTLY_API_URL=http://aptly:8080 \
    APTLY_GUI_DATABASE_URL=sqlite+aiosqlite:////data/aptly-gui.db

VOLUME /data
EXPOSE 8080

CMD ["uvicorn", "aptly_gui.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
