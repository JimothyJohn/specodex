#!/usr/bin/env bash
# Build the Python FastAPI Lambda asset bundle.
#
# Output: app/backend_py/dist/  — everything the Lambda needs to run,
# laid out so `app.backend_py.src.main.handler` resolves and all
# imports succeed.
#
# Bundling strategy:
#   1. Use the Lambda base Docker image (public.ecr.aws/sam/build-python3.12)
#      if Docker is available — guarantees correct wheels for the
#      Lambda runtime.
#   2. Fall back to local `pip install --target` if Docker isn't
#      available — sufficient for local synth + smoke; risky for
#      deploy if the host platform differs from Lambda's amd64.
#
# CDK's api-stack.ts will conditionally include the Python Lambda
# only when this directory exists. Run before `cdk deploy` for the
# v2 path; safe to skip otherwise.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/app/backend_py/dist"
SPECODEX_DIR="${REPO_ROOT}/specodex"
BACKEND_PY_DIR="${REPO_ROOT}/app/backend_py"
PYTHON_IMAGE="public.ecr.aws/sam/build-python3.12"

# Versions match app/backend_py/pyproject.toml.
DEPS=(
    "fastapi>=0.115.0"
    "mangum>=0.17.0"
    "boto3>=1.43.6"
    "pydantic[email]>=2.13.4"
    "python-jose[cryptography]>=3.3.0"
    "httpx>=0.27.0"
)

echo "==> Cleaning ${DIST_DIR}"
rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}/app"

bundle_with_docker() {
    echo "==> Bundling with Docker (${PYTHON_IMAGE})"
    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -v "${REPO_ROOT}:/asset-input:delegated" \
        -v "${DIST_DIR}:/asset-output:delegated" \
        -w /asset-input \
        "${PYTHON_IMAGE}" \
        bash -c "
            pip install --target /asset-output ${DEPS[*]} \
            && cp -r /asset-input/specodex /asset-output/ \
            && touch /asset-output/app/__init__.py \
            && cp -r /asset-input/app/backend_py /asset-output/app/ \
            && touch /asset-output/app/backend_py/__init__.py \
            && rm -rf /asset-output/app/backend_py/tests \
            && rm -f /asset-output/app/backend_py/uv.lock \
            && rm -rf /asset-output/app/backend_py/dist
        "
}

bundle_locally() {
    echo "==> Docker not available; bundling locally with host pip"
    echo "    NOTE: This may produce binary wheels incompatible with Lambda."
    echo "    Prefer Docker bundling for production deploys."

    python3 -m pip install --quiet --target "${DIST_DIR}" "${DEPS[@]}"

    cp -r "${SPECODEX_DIR}" "${DIST_DIR}/"
    touch "${DIST_DIR}/app/__init__.py"
    cp -r "${BACKEND_PY_DIR}" "${DIST_DIR}/app/"
    touch "${DIST_DIR}/app/backend_py/__init__.py"
    rm -rf "${DIST_DIR}/app/backend_py/tests"
    rm -f "${DIST_DIR}/app/backend_py/uv.lock"
    rm -rf "${DIST_DIR}/app/backend_py/dist"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    bundle_with_docker
else
    bundle_locally
fi

# Sanity check — fail loud if the layout isn't what CDK expects.
if [[ ! -f "${DIST_DIR}/app/backend_py/src/main.py" ]]; then
    echo "ERROR: ${DIST_DIR}/app/backend_py/src/main.py missing after build" >&2
    exit 1
fi
if [[ ! -d "${DIST_DIR}/specodex" ]]; then
    echo "ERROR: ${DIST_DIR}/specodex/ missing after build" >&2
    exit 1
fi
if [[ ! -d "${DIST_DIR}/fastapi" ]]; then
    echo "ERROR: ${DIST_DIR}/fastapi/ missing after build (pip install failed?)" >&2
    exit 1
fi

SIZE_MB=$(du -sm "${DIST_DIR}" | awk '{print $1}')
echo "==> Build complete: ${DIST_DIR} (${SIZE_MB}MB)"
echo "    Handler: app.backend_py.src.main.handler"
