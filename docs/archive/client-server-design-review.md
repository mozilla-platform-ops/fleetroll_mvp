# Client/server design review — March 11, 2026

Historical review of the [client/server proposal](../design/client-server-design.md).
The proposal now lists the implementation questions from this review. This page
records review context, not current implementation status.

The review supported the DataProvider boundary, SSE for one-way updates,
PostgreSQL advisory-lock leader election, incremental delivery, and keeping
privileged writes and vault contents on operators' machines.

It corrected an unimplemented Phase 1 completion claim and identified missing
specifications for content-addressed storage, audit records, snapshot/SSE payloads,
PostgreSQL migration, scan configuration, remote/offline behavior, shared-note
conflicts, HTTP client execution, and testing. Those questions must be resolved
against the current code before implementing the central service.
