"""Pytest fixtures for E2E integration tests."""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


def _docker_available() -> bool:
    """Check if Docker is available and running."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def _wait_for_ssh(host: str, port: int, timeout: int = 30) -> bool:
    """Wait for SSH server to become ready by polling TCP connection.

    Args:
        host: Hostname to connect to
        port: Port to connect to
        timeout: Maximum time to wait in seconds

    Returns:
        True if SSH became ready, False if timeout
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect((host, port))
                return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


# Check Docker availability at module load time
_DOCKER_AVAILABLE = _docker_available()

# Skip all integration tests if Docker is unavailable
pytestmark = pytest.mark.skipif(
    not _DOCKER_AVAILABLE,
    reason="Docker not available or not running",
)


@pytest.fixture(scope="session")
def ssh_keypair() -> Generator[tuple[Path, Path], None, None]:
    """Generate ephemeral ED25519 SSH key pair for testing.

    Yields:
        Tuple of (private_key_path, public_key_path)
    """
    if not _DOCKER_AVAILABLE:
        pytest.skip("Docker not available")

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "test_key"
        pub_path = Path(tmpdir) / "test_key.pub"

        # Generate ED25519 key without passphrase (required for BatchMode)
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(key_path),
                "-N",
                "",  # No passphrase
                "-C",
                "fleetroll-integration-test",
            ],
            check=True,
            capture_output=True,
        )

        yield key_path, pub_path


@pytest.fixture(scope="session")
def docker_image(ssh_keypair: tuple[Path, Path]) -> str:
    """Build Docker test image.

    Args:
        ssh_keypair: SSH key pair fixture (ensures keys exist before building)

    Returns:
        Docker image name/tag
    """
    if not _DOCKER_AVAILABLE:
        pytest.skip("Docker not available")

    # Find Dockerfile relative to this file
    dockerfile_dir = Path(__file__).parent.parent.parent / "docker" / "test-sshd"
    image_name = "fleetroll-test-sshd:latest"

    # Build image (uses Docker's build cache after first run)
    subprocess.run(
        ["docker", "build", "-t", image_name, str(dockerfile_dir)],
        check=True,
        capture_output=True,
    )

    return image_name


@pytest.fixture(scope="session")
def sshd_container(
    docker_image: str,
    ssh_keypair: tuple[Path, Path],
) -> Generator[dict[str, str | int], None, None]:
    """Start SSH server container with ephemeral keys.

    Args:
        docker_image: Docker image name from docker_image fixture
        ssh_keypair: SSH key pair from ssh_keypair fixture

    Yields:
        Dict with container connection info: {host, port, user, key_path}
    """
    if not _DOCKER_AVAILABLE:
        pytest.skip("Docker not available")

    private_key, public_key = ssh_keypair
    host_port = _find_free_port()

    # Create temporary authorized_keys file
    with tempfile.TemporaryDirectory() as tmpdir:
        auth_keys_path = Path(tmpdir) / "authorized_keys"
        auth_keys_path.write_text(public_key.read_text())
        auth_keys_path.chmod(0o600)

        # Start container with:
        # - Random host port mapped to container port 22
        # - Volume mount for authorized_keys
        container_name = f"fleetroll-test-{int(time.time())}"
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                container_name,
                "-p",
                f"{host_port}:22",
                "-v",
                f"{auth_keys_path}:/home/testuser/.ssh/authorized_keys:ro",
                docker_image,
            ],
            check=True,
            capture_output=True,
        )

        try:
            # Wait for SSH to become ready
            if not _wait_for_ssh("127.0.0.1", host_port):
                msg = f"SSH server did not become ready within timeout (port {host_port})"
                raise TimeoutError(msg)

            yield {
                "host": "127.0.0.1",
                "port": host_port,
                "user": "testuser",
                "key_path": str(private_key),
            }
        finally:
            # Clean up container
            subprocess.run(
                ["docker", "stop", container_name],
                check=False,
                capture_output=True,
            )


@pytest.fixture
def audit_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Provide isolated $HOME for audit operations.

    Sets $HOME to a temporary directory so that each test gets its own
    ~/.fleetroll/ database and audit log, preventing cross-test pollution.

    Args:
        tmp_path: Pytest's temporary directory fixture
        monkeypatch: Pytest's monkeypatch fixture

    Returns:
        Path to temporary home directory
    """
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir
