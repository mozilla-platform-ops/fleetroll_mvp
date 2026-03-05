# Windows Support

## Notes

### Metadata Script and Output

The fleetroll script is located at C:\management_scripts\ fleetroll_mvp_collect.ps1 it produces ronin_puppet_run.json in the same dir. it will overwrite the json if it exists.

### Hostnames

all of the host names can be pulled from here: https://github.com/mozilla-platform-ops/worker-images/blob/main/provisioners/windows/MDC1Windows/pools.yml

pools.yml:

```yaml
pools:
  - name: "win11-64-24h2-hw-ref"
    openvox_version: "8.19.2"
    puppet_version: "8.10.0"
```

TODO: Store the script we use to generate the hostlist for this so we can later incorporate into the host list generator (mvp-1u6).

### SSH Access

You will need the win _audit key out of 1password and ssh adminstrator@. It will be powershell shell, so you should be able to invoke the script by the path.
