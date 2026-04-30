# build 2026.04.30
FROM ghcr.io/home-assistant/base-python:latest

RUN pip3 install --no-cache-dir aiohttp

COPY app/ /app/
COPY icon.png /app/icon.png

COPY run.sh /
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
