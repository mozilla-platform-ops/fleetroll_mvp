#!/bin/bash
# Entrypoint for test SSH server
# Generates host keys and starts sshd in foreground

set -e

# Generate SSH host keys if they don't exist
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    echo "Generating SSH host keys..."
    ssh-keygen -A
fi

echo "Starting SSH server..."
exec /usr/sbin/sshd -D -e
