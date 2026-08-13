#!/bin/sh
set -eu

fc-cache -f
exec /headless-shell/run.sh "$@"
