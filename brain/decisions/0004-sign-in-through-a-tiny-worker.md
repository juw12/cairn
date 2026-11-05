---
title: Sign-in through a tiny worker
type: decision
status: proposed
tags: [network]
created: 2026-11-05
---
## Context

GitHub won't complete OAuth from a static page.

## Options

1. A ~30-line Cloudflare Worker for the token exchange (free)
2. Users paste a personal access token

## Decision

Proposed: option 1. Needs a Cloudflare account.

## Consequences

One small piece of infrastructure to keep running.
