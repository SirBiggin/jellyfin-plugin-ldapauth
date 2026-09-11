#!/usr/bin/env python3
"""Patch: do not try to rename a Jellyfin user when the LDAP username differs only by case.

Jellyfin 10.11+ rejects RenameUser when old and new names are equal ignoring case
("The new and old names must be different"), which made every login (and password change,
which re-authenticates first) fail for such users. Upstream issue #201, PR #205 (unmerged).
"""
import sys, pathlib

p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("LDAP-Auth/LDAPAuthenticationProviderPlugin.cs")
src = p.read_text(encoding="utf-8")

old = """                if (!string.Equals(user.Username, ldapUsername, StringComparison.Ordinal))
                {
                    _logger.LogDebug("Updating user {Username} username to: {LdapUsername}.", user.Username, ldapUsername);
"""
new = """                // Jellyfin treats usernames case-insensitively and rejects a rename that only changes case,
                // so only rename when the names really differ.
                if (!string.Equals(user.Username, ldapUsername, StringComparison.OrdinalIgnoreCase))
                {
                    _logger.LogDebug("Updating user {Username} username to: {LdapUsername}.", user.Username, ldapUsername);
"""

if new in src:
    print(f"{p}: already patched"); sys.exit(0)
if old not in src:
    print(f"{p}: expected block not found, refusing to patch"); sys.exit(1)
p.write_text(src.replace(old, new, 1), encoding="utf-8")
print(f"{p}: patched")
