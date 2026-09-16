# Security policy

Security fixes target the latest published `research-repo-tools` release.
Before the first release, reports against the current main branch are welcome.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/acgetchell/research-repo-tools/security/advisories/new)
or email [adam@adamgetchell.org](mailto:adam@adamgetchell.org).
Please keep vulnerability details out of public issues until coordinated disclosure.

Include the affected version, platform, minimal reproduction, and expected impact.
Reports are acknowledged and investigated as maintainer availability permits.
Accepted fixes will be accompanied by release notes and a GitHub Security Advisory
when appropriate. Reporter credit is optional.

Relevant issues include unsafe subprocess execution, unexpected file modification,
path traversal, credential exposure, and malformed-input handling that compromises
repository integrity or availability. Avoid data destruction, service disruption,
or accessing another person's data while investigating.
