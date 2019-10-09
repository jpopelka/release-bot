#!/usr/bin/bash

exec release-bot -c /home/release-bot/.config/conf.yaml &
exec celery -A release_bot.celery_task worker -l debug
