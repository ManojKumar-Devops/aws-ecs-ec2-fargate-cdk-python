#!/bin/bash
set -e
# Example: update nginx home page from deployed artifact if present
if [ -f /opt/myapp/index.html ]; then
  cp -f /opt/myapp/index.html /usr/share/nginx/html/index.html
fi
