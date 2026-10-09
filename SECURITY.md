# Security and public-repository hygiene

SSM-SpiceConex does not require AWS account IDs, organization names, instance IDs, private IPs or credentials to be stored in source code.

- AWS profiles are discovered from the local AWS configuration.
- Authentication/session validation uses `sts:GetCallerIdentity` at runtime.
- Account identity is not written back to the repository or generated configuration files.
- Demo data uses documentation-only IP space (`192.0.2.0/24`) and synthetic instance IDs.
- Never commit `~/.aws`, credentials, SSO cache files, `.env` files, generated logs or exported infrastructure data.
