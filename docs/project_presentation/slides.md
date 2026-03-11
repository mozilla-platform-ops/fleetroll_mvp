---
theme: default
title: fleetroll mvp presentation
---

# fleetroll mvp presentation
https://github.com/mozilla-platform-ops/fleetroll_mvp

---

# origins

- born from fleetroll spec
  - https://github.com/mozilla-platform-ops/fleetroll_mvp/blob/main/specs/FleetRoll_Product_Spec_v5.md
  - goal: 'inventory and maintenance of the hw fleet'
  - MVP focuses on a subset of features in fleetroll
    - focused on linux and mac hw maintenance (pupppet-based, vault yamls and override files)
      - solved a need I had... gets confusing managing various rollouts of branches.

---

# host actions

- set vault.yml file (`fleetroll host-set-vault`): Mac/Linux source for puppet/hiera secrets
- set/unset override file (`fleetroll host-set-override`): allows setting a host to use a branch persistently

---

# data collection

  - host data (`fleetroll host-audit`)
    - uses SSH. bash for mac and linux and powershell for windows.
      - `fleetroll debug-host-script (--windows)`
  	- gathers info like role, puppet run metadata, uptime, vault, and override info
  - additional data
    - github (`fleetroll gh-fetch`): expected puppet git SHA for mac and linux (master or override) and win (worker_images file)
    - taskcluster (`fleetroll tc-fetch`): last time in touch with TC, job status (including pass/fail, duration)
      - worker_type is determined via lookup tables/patterns based on role
  - `watch -n 600 tools/gather-generic.sh config/host-lists/BLAH`
    - handles collection of all three (`tc-fetch` calls `gh-fetch`)
    - allows more frequent collection of interesting hosts (3m for canaries, 15m for rest)

---

# data display
  - `fleetroll host-monitor`
    - `--once` mode
        - `--json` mode for scripts/LLMs
    - interactive TUI
        - `?` for help

---

# new features
  - sqlite db (was append-only jsonl, got large)
  - notes (persisted in git, jsonl)
    `fleetroll note-add` for now, ability to do in TUI soon?
  - windows support

---

# future plans

either via evolution or full rewrite... TBD

  - view modes
    - windows mode: hide columns that aren't useful
    - 'manager' mode: showing hardware type/spec -> host -> pool
  - integration of more data (inventory, infoblox, ???)
  - client/server architecture, k8s hosting
    - figure out security concerns... use a read-only user-account

---

# dev workflow
  - developed very detailed spec with ChatGPT
    - ChatGPT said it was a thorough technical spec
  - started decomposing spec into beads tasks with Claude
  - once fully decomposed, tell Claude to grab the next bead and plan it's implementation
  	- `/model opusplan` in Claude
  	- iterate until happy, then have it execute.
  	- verify tests pass, pre-commit passes. commit, then close the bead.
  	- repeat until program is complete.
  - focused on my initial need (being able to set override files and monitor hosts) then grew (set vault files, more info, more integrations, etc)
  - tests are important.
