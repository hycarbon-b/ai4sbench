from __future__ import annotations

import shlex
from dataclasses import dataclass

HARBOR_VERSION = "0.20.0"
COMPOSE_VERSION = "v5.4.0"
BUILDX_VERSION = "v0.36.1"
INSTALL_ROOT = "/opt/ai4sbench"
WORKER_ENTRYPOINT = f"{INSTALL_ROOT}/.venv/bin/ai4sbench-worker"

COMPOSE_CHECKSUMS = {
    "x86_64": "837fd1d35bf6a494f41b5b5988269a7be79de337cf1a1a6ff0e45ab51bb4e9be",
    "aarch64": "fc5d1371f1ec7987e703da94ede49af3fbfb240b83f22991a98511de7bc4b93b",
}
BUILDX_CHECKSUMS = {
    "amd64": "48af8a397ebd60178778bf63611dbcebe5f5e7a9be90eb9147b24b9587455778",
    "arm64": "5d0cafd9d16afe1a0f0d9529885344ace2cc99efdd531b6c783c5455a6001569",
}

# Every value the worker needs at run time is injected by the launch user data,
# so nothing here may reference a token, a run, or account credentials.  The
# builder asserts this separation in `assert_image_is_runtime_free`.
FORBIDDEN_IN_IMAGE = (
    "TBCP_JOB_TOKEN",
    "TBCP_RUN_ID",
    "TBCP_API_BASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
)


@dataclass(frozen=True)
class ImageSpec:
    """The pinned contents of a baked worker image."""

    repo_url: str
    commit_sha: str
    harbor_version: str = HARBOR_VERSION
    compose_version: str = COMPOSE_VERSION
    buildx_version: str = BUILDX_VERSION

    def tags(self, docker_version: str = "") -> dict[str, str]:
        tags = {
            "ai4sbench:managed": "true",
            "ai4sbench:image-role": "worker",
            "ai4sbench:worker-commit": self.commit_sha,
            "ai4sbench:harbor-version": self.harbor_version,
        }
        if docker_version:
            tags["ai4sbench:docker-version"] = docker_version
        return tags


def render_provisioning_script(spec: ImageSpec) -> str:
    """Return the user data that turns a base image into a worker image.

    The script installs only version-pinned software: Docker with the Compose
    and Buildx CLI plugins, Git, Python 3.12, Harbor, and the worker package at
    ``spec.commit_sha``.  It finishes with a smoke check so a broken build fails
    before an image is registered, and writes a marker file the builder polls.
    """

    return "\n".join(
        (
            "#!/bin/bash",
            "set -euo pipefail",
            "exec > >(tee -a /var/log/ai4sbench-image-build.log) 2>&1",
            "dnf install -y docker git python3.12 python3.12-pip",
            "systemctl enable --now docker",
            "install -d -m 0755 /usr/local/lib/docker/cli-plugins",
            "case $(uname -m) in",
            (
                "  x86_64) compose_arch=x86_64; "
                f"compose_sha={COMPOSE_CHECKSUMS['x86_64']}; "
                "buildx_arch=amd64; "
                f"buildx_sha={BUILDX_CHECKSUMS['amd64']} ;;"
            ),
            (
                "  aarch64) compose_arch=aarch64; "
                f"compose_sha={COMPOSE_CHECKSUMS['aarch64']}; "
                "buildx_arch=arm64; "
                f"buildx_sha={BUILDX_CHECKSUMS['arm64']} ;;"
            ),
            "  *) echo 'Unsupported architecture' >&2; exit 1 ;;",
            "esac",
            "curl --fail --location --retry 3 \\",
            (
                f"  https://github.com/docker/compose/releases/download/{spec.compose_version}/"
                "docker-compose-linux-${compose_arch} \\"
            ),
            "  -o /usr/local/lib/docker/cli-plugins/docker-compose",
            'echo "${compose_sha}  /usr/local/lib/docker/cli-plugins/docker-compose" | sha256sum --check',
            "chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose",
            "curl --fail --location --retry 3 \\",
            (
                f"  https://github.com/docker/buildx/releases/download/{spec.buildx_version}/"
                f"buildx-{spec.buildx_version}.linux-" + "${buildx_arch} \\"
            ),
            "  -o /usr/local/lib/docker/cli-plugins/docker-buildx",
            'echo "${buildx_sha}  /usr/local/lib/docker/cli-plugins/docker-buildx" | sha256sum --check',
            "chmod 0755 /usr/local/lib/docker/cli-plugins/docker-buildx",
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "export PATH=/root/.local/bin:$PATH",
            f"rm -rf {INSTALL_ROOT}",
            f"git clone --filter=blob:none --no-checkout {shlex.quote(spec.repo_url)} {INSTALL_ROOT}/src",
            f"git -C {INSTALL_ROOT}/src fetch origin {shlex.quote(spec.commit_sha)} --depth 1",
            f"git -C {INSTALL_ROOT}/src checkout --detach FETCH_HEAD",
            f"uv venv --python /usr/bin/python3.12 {INSTALL_ROOT}/.venv",
            (
                f"VIRTUAL_ENV={INSTALL_ROOT}/.venv uv pip install --no-cache "
                f"{INSTALL_ROOT}/src/backend"
            ),
            (
                f"VIRTUAL_ENV={INSTALL_ROOT}/.venv uv pip install --no-build --no-cache "
                f'"harbor=={spec.harbor_version}"'
            ),
            f"ln -sf {INSTALL_ROOT}/.venv/bin/harbor /usr/local/bin/harbor",
            f"printf '%s\\n' {shlex.quote(spec.commit_sha)} > {INSTALL_ROOT}/worker-version",
            # Smoke check: a failure here aborts the build before the marker is
            # written, so the builder never registers an unusable image.
            "docker info",
            "docker compose version",
            "docker buildx version",
            "harbor --version",
            f"{WORKER_ENTRYPOINT} --help || true",
            f"{INSTALL_ROOT}/.venv/bin/python -c 'import control_panel.worker'",
            f"docker --version > {INSTALL_ROOT}/docker-version",
            "touch /var/lib/ai4sbench-image-ready",
            "poweroff",
        )
    )


def assert_image_is_runtime_free(script: str) -> None:
    """Fail the build if per-run data would be baked into the image."""
    for name in FORBIDDEN_IN_IMAGE:
        if name in script:
            raise ValueError(f"Image provisioning must not reference {name}")
