"""FleetRoll constants."""

from __future__ import annotations

# Linux paths (used when remote OS is Linux)
DEFAULT_OVERRIDE_PATH = "/etc/puppet/ronin_settings"
DEFAULT_ROLE_PATH = "/etc/puppet_role"
DEFAULT_VAULT_PATH = "/root/vault.yaml"
# DEFAULT_PUPPET_DONE_PATH = "/tmp/puppet_run_done" # only used on linux
DEFAULT_PUPPET_LAST_RUN_PATH = "/opt/puppetlabs/puppet/cache/state/last_run_report.yaml"

# Darwin/OS X paths (used when remote OS is Darwin)
# Paths are automatically selected based on OS detection in remote scripts
OSX_OVERRIDE_PATH = "/opt/puppet_environments/ronin_settings"
# OSX_ROLE_PATH: same as Linux (/etc/puppet_role)
OSX_VAULT_PATH = "/var/root/vault.yaml"
# OSX_PUPPET_DONE_PATH: not used on OS X yet
OSX_PUPPET_LAST_RUN_PATH = "TBD"

# Internal constants
CONTENT_SENTINEL = "__FLEETROLL_OVERRIDE_CONTENT__"
BACKUP_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
SSH_TIMEOUT_EXIT_CODE = 124
AUDIT_MAX_RETRIES = 1
AUDIT_RETRY_DELAY_S = 2
CONTENT_PREFIX_LEN = 12
CONTENT_PREFIX_STEP = 4
AUDIT_DIR_NAME = ".fleetroll"
AUDIT_FILE_NAME = "audit.jsonl"
HOST_OBSERVATIONS_FILE_NAME = "host_observations.jsonl"
OVERRIDES_DIR_NAME = "overrides"
VAULT_YAMLS_DIR_NAME = "vault_yamls"
DRY_RUN_PREVIEW_LIMIT = 5
DEFAULT_GITHUB_REPO = "mozilla-platform-ops/ronin_puppet"

# SQLite database settings
DB_FILE_NAME = "fleetroll.db"
DB_RETENTION_LIMIT = 10  # Keep latest N records per key in SQLite tables

# TaskCluster role to (provisioner, workerType) mapping
ROLE_TO_TASKCLUSTER = {
    "gecko_t_linux_talos": ("releng-hardware", "gecko-t-linux-talos-1804"),
    "gecko_t_linux_2404_talos": ("releng-hardware", "gecko-t-linux-talos-2404"),
    "gecko_t_linux_netperf": ("releng-hardware", "gecko-t-linux-netperf-1804"),
    "gecko_t_linux_2404_netperf": ("releng-hardware", "gecko-t-linux-netperf-2404"),
    # macOS roles - AUTO_under_to_dash converts role name (e.g., gecko_t_osx_1400_r8 -> gecko-t-osx-1400-r8)
    "gecko_t_osx_1400_r8": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_t_osx_1500_m4": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_t_osx_1015_r8": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_1_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_t_osx_1015_r8_staging": ("releng-hardware", "AUTO_under_to_dash"),
    "applicationservices_1_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "mozillavpn_b_1_osx": ("releng-hardware", "AUTO_under_to_dash"),
    "nss_3_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "mozillavpn_b_3_osx": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_3_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_t_osx_1400_r8_staging": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_1_b_osx_1015_staging": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_t_osx_1500_m4_staging": ("releng-hardware", "AUTO_under_to_dash"),
    "enterprise_1_b_osx_arm64": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_3_b_osx_arm64": ("releng-hardware", "AUTO_under_to_dash"),
    "nss_1_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "enterprise_3_b_osx_arm64": ("releng-hardware", "AUTO_under_to_dash"),
    "applicationservices_3_b_osx_1015": ("releng-hardware", "AUTO_under_to_dash"),
    "gecko_1_b_osx_arm64": ("releng-hardware", "AUTO_under_to_dash"),
}
