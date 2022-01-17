FROM python:3-slim

# install git; needed for pip if the repo git dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get purge -y --auto-remove && \
    rm -rf /var/lib/apt/lists/*
# Sample from https://hub.docker.com/_/python

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5004

CMD [ "python3", "./vmvpd-proxy.py" ]
