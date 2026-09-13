#!/bin/bash
# Entry point used by build.ps1 when building inside a Docker container.
# The project is mounted at /src and a persistent volume at /build.
set -euo pipefail
export SRC_DIR=/src
export BUILD_DIR=/build
sed 's/\r$//' /src/build.sh > /tmp/build.sh
exec bash /tmp/build.sh
