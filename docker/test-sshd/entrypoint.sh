#!/bin/bash
# Entrypoint for test SSH server
# Generates host keys, sets up authorized_keys, and starts sshd in foreground

set -e

# Generate SSH host keys if they don't exist
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    echo "Generating SSH host keys..."
    ssh-keygen -A
fi

# Copy and fix permissions for authorized_keys if provided via volume mount
if [ -f /tmp/authorized_keys_mount ]; then
    echo "Setting up authorized_keys..."
    cp /tmp/authorized_keys_mount /home/testuser/.ssh/authorized_keys
    chown testuser:testuser /home/testuser/.ssh/authorized_keys
    chmod 600 /home/testuser/.ssh/authorized_keys
fi

echo "Starting SSH server..."
exec /usr/sbin/sshd -D -e
