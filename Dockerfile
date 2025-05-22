FROM dock.mau.dev/maubot/maubot:latest
RUN apk add gcc
COPY ./requirements.txt ./inviterbot-requirements.txt
RUN pip3 install --break-system-packages -r inviterbot-requirements.txt
