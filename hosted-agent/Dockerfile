FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8088

RUN PYTHONDONTWRITEBYTECODE= python -m compileall -q .

CMD ["python", "main.py"]

