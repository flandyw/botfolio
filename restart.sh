#!/usr/bin/env bash
set -euo pipefail

services=(botfolio-events.service)
for service in botfolio-dashboard.service botfolio-dashboard-tailscale.service \
    botfolio-web.service botfolio-web-js.service; do
    state=$(systemctl --user show --property=LoadState --value "$service")
    if [[ "$state" == "loaded" ]]; then
        services+=("$service")
    fi
done

systemctl --user restart "${services[@]}"
