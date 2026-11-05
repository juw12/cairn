---
title: Sign-in through a tiny worker
type: decision
status: accepted
tags: [network]
created: 2026-11-05
---
## Context

GitHub won't complete OAuth from a static page: its device-flow and token endpoints don't allow browser (CORS) calls.

## Options

1. A ~30-line Cloudflare Worker for the token exchange (free)
2. Users paste a personal access token

## Decision

Both. A pasted token works today with no infrastructure, and a fine-grained token can be limited to one repo.
The device flow ("Continue with GitHub") is the friendlier path and appears once the Worker is deployed.
The Worker holds no secret, forwards only the two device-flow endpoints, forces our client id and the
`public_repo` scope, and only answers our own origins.

## Consequences

One small, optional piece of infrastructure. If it goes down, token sign-in still works.
