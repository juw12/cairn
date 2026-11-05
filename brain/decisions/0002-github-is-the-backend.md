---
title: GitHub is the backend
type: decision
status: accepted
tags: [network]
created: 2026-11-05
---
## Context

Profiles, sharing and join requests need identity, storage and permissions.

## Options

1. Run our own server and database
2. Build on GitHub: accounts, repos, topics, issues, collaborators

## Decision

Build on GitHub. Publishing is a repo topic; joining is an issue plus collaborator access.

## Consequences

Almost no infrastructure. Everyone needs a GitHub account, which suits the first audience.
