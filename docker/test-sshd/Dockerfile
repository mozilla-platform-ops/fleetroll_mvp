# Debian-based SSH server for E2E integration testing
# Uses Debian bookworm-slim because the audit script relies on GNU stat format

FROM debian:bookworm-slim

# Install SSH server and utilities needed for audit script
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server \
        sudo \
        coreutils \
        procps \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create test user with passwordless sudo
RUN useradd -m -s /bin/bash testuser && \
    echo "testuser ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/testuser && \
    chmod 0440 /etc/sudoers.d/testuser

# Configure sshd: disable password auth, enable pubkey auth
RUN mkdir -p /run/sshd && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

# Prepare .ssh directory for runtime key injection (via volume mount)
RUN mkdir -p /home/testuser/.ssh && \
    chown testuser:testuser /home/testuser/.ssh && \
    chmod 700 /home/testuser/.ssh

# Plant fixture files at Linux paths expected by audit script
# Use non-hex values to avoid detect-secrets flags
RUN echo "gecko-t-linux-talos" > /etc/puppet_role

# Create puppet metadata JSON (use obviously-fake, non-hex values for SHAs)
# Fields match what the audit script expects: git_branch, git_sha, etc.
RUN mkdir -p /etc/puppet && \
    echo '{ \
      "git_branch": "main", \
      "git_sha": "test_commit_sha_not_a_real_hex_value_placeholder_pad", \
      "ts": "2024-01-15T10:30:00Z" \
    }' > /etc/puppet/last_run_metadata.json

# Create sample ronin_settings override file
RUN mkdir -p /etc/puppet && \
    echo '# Test override settings\nkey1=value1\nkey2=value2' > /etc/puppet/ronin_settings && \
    chown testuser:testuser /etc/puppet/ronin_settings && \
    chmod 644 /etc/puppet/ronin_settings

# Create sample vault.yaml
RUN mkdir -p /root && \
    echo '---\nsecret_key: test_secret_value\napi_token: test_api_token_value' > /root/vault.yaml && \
    chmod 640 /root/vault.yaml

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose SSH port
EXPOSE 22

# Start sshd via entrypoint (generates host keys first)
ENTRYPOINT ["/entrypoint.sh"]
