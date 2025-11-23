FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MIDI_ROOT=/midis \
    FLASK_ENV=production

EXPOSE 8000

VOLUME ["/midis"]

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]
