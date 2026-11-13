FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080 BOT_DB_PATH=/data/bot.sqlite3
WORKDIR /app
RUN mkdir /data && chown 10001:10001 /data
COPY --chown=10001:10001 *.py ./
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/healthz', timeout=3).close()"
CMD ["python", "bot.py"]
