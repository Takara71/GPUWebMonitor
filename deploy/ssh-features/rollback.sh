#!/bin/sh
set -eu
systemctl disable --now frp-ssh-features-agent.service
if [ -f /etc/systemd/system/frp-ssh-features-receiver.service ]; then
    systemctl disable --now frp-ssh-features-report.timer frp-ssh-features-receiver.service
    sed -i '\|include /etc/nginx/snippets/ssh-features-location.conf;|d' /etc/nginx/sites-available/lab-status
    nginx -t
    systemctl reload nginx
fi
# Preserve data and backups. Existing FRP/SSH guard rules are untouched.
