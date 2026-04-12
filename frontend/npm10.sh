#!/bin/sh
# Wrapper to use npm@10 for compatibility with Node.js v24
cd "$(dirname "$0")/frontend" && node ../npm-10/package/bin/npm-cli.js "$@"