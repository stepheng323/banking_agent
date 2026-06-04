#!/bin/sh
set -eu

apt-get update
apt-get install -y gcc
rm -rf /var/lib/apt/lists/*
