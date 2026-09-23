# syntax = docker/dockerfile:1.1-experimental
FROM alpine

RUN apk --update --no-cache add git bash py3-pip openssh

RUN apk add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing hub

COPY entrypoint.sh /entrypoint.sh
COPY publish_commit.py /publish_commit.py

ENTRYPOINT ["/entrypoint.sh"]
