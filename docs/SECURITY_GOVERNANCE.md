# Security and Governance

## Current state of the repository

The repository is currently public, so uploading any sensitive internal data or details is prohibited.

## Forbidden inside Git

- Passwords or login tokens.
- Cookie and session files.
- API keys.
- Addresses or URLs of private internal systems.
- Employee, customer, or supplier data.
- Real raw data files.
- Production database copies.
- Screenshots containing sensitive data.

## Least-privilege principle

Every worker or agent gets only the tools its task needs.

## Action levels

### Green
Retry, reopen a page, redownload, check a file.

### Yellow
Modify code or a workflow inside a sandbox and test it.

### Red
Production changes, data deletion, changing permissions or sensitive
databases. Requires human approval.

## Agent rules

- Reading is separate from writing.
- Production execution is separate from experimentation.
- Every change has a diff, a log, and tests.
- Every change is reversible.
- Two agents are never allowed to modify the same part at the same moment without a lock.

## Operating inside the company

Access to any system or service must use an officially authorized account
and permissions. This project includes no mechanism to bypass device,
network, or security-control policies.
