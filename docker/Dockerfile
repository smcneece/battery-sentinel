FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip3 install --no-cache-dir aiohttp

COPY addon/app/ /app/
COPY addon/icon.png /app/icon.png
COPY docker/run.sh /run.sh

RUN chmod a+x /run.sh

EXPOSE 8099
CMD ["/run.sh"]
