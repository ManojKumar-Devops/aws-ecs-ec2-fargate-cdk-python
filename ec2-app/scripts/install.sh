#!/bin/bash
set -e
yum install -y nginx || true
mkdir -p /opt/hello
cp -f /opt/hello/index.html /usr/share/nginx/html/index.html || true
systemctl enable nginx
