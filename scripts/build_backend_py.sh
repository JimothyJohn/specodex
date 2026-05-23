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

# Copy app/backend_py/ into the bundle EXCLUDING the dist/ output dir
# itself (dist/ lives inside app/backend_py/, so a plain `cp -r` would
# recurse into its own destination — "cannot copy a directory into
# itself") and all dev-only artifacts (.venv is 98MB on its own and
# would blow Lambda's 250MB unzipped size limit). tar streams the
# tree and `--exclude` drops everything cleanly. Works with both
# GNU tar (Docker image) and BSD tar (macOS).
_copy_backend_py() {
    # $1 = source parent dir (the `app/` that contains backend_py)
    # $2 = dest parent dir   (the `<dist>/app/` to land backend_py in)
    tar -C "$1" \
        --exclude='backend_py/dist' \
        --exclude='backend_py/tests' \
        --exclude='backend_py/uv.lock' \
        --exclude='backend_py/.venv' \
        --exclude='backend_py/.pytest_cache' \
        --exclude='backend_py/.ruff_cache' \
        --exclude='backend_py/.mypy_cache' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        -cf - backend_py | tar -C "$2" -xf -
}

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
            && tar -C /asset-input/app \
                --exclude='backend_py/dist' \
                --exclude='backend_py/tests' \
                --exclude='backend_py/uv.lock' \
                --exclude='backend_py/.venv' \
                --exclude='backend_py/.pytest_cache' \
                --exclude='backend_py/.ruff_cache' \
                --exclude='backend_py/.mypy_cache' \
                --exclude='__pycache__' \
                --exclude='*.pyc' \
                -cf - backend_py | tar -C /asset-output/app -xf - \
            && touch /asset-output/app/backend_py/__init__.py
        "
}

bundle_locally() {
    echo "==> Docker not available; bundling locally with host pip"
    echo "    NOTE: This may produce binary wheels incompatible with Lambda."
    echo "    Prefer Docker bundling for production deploys."

    python3 -m pip install --quiet --target "${DIST_DIR}" "${DEPS[@]}"

    cp -r "${SPECODEX_DIR}" "${DIST_DIR}/"
    touch "${DIST_DIR}/app/__init__.py"
    _copy_backend_py "${REPO_ROOT}/app" "${DIST_DIR}/app"
    touch "${DIST_DIR}/app/backend_py/__init__.py"
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
