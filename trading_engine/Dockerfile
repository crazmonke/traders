FROM python:3.12-slim

WORKDIR /app

COPY trading_engine/requirements.txt ./trading_engine/requirements.txt
RUN pip install --no-cache-dir -r trading_engine/requirements.txt

COPY trading_engine ./trading_engine

CMD ["python", "-m", "trading_engine.main"]
