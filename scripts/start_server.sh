#!/bin/bash
set -e
systemctl enable nginx
systemctl restart nginx
