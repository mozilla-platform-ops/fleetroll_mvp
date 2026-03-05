#!/usr/bin/env bash

set -e
set -x

# idea: use these ranges for an agent to generate an initial changelog

git log --oneline -- pyproject.toml | grep -iE 'version|release'
