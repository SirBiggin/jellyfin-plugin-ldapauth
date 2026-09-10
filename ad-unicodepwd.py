#!/usr/bin/env python3
"""Patch LDAPAuthenticationProviderPlugin.cs so ChangePassword works against Active Directory.

AD only accepts writes to unicodePwd as the new password wrapped in double quotes and
encoded UTF-16LE, over an encrypted (LDAPS) connection. Upstream writes a plain UTF-8
string, which AD rejects with "Invalid Attribute Syntax". See upstream issues #164 / #200.
"""
import sys, pathlib

p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("LDAP-Auth/LDAPAuthenticationProviderPlugin.cs")
src = p.read_text(encoding="utf-8")

old = """                var passAttr = LdapPlugin.Instance.Configuration.LdapPasswordAttribute;
                var ldapAttr = new LdapAttribute(passAttr, newPassword);
                var ldapMod = new LdapModification(LdapModification.Replace, ldapAttr);

                ldapClient.Modify(ldapUser.Dn, ldapMod);
"""

new = """                var passAttr = LdapPlugin.Instance.Configuration.LdapPasswordAttribute;
                LdapAttribute ldapAttr;
                if (string.Equals(passAttr, "unicodePwd", StringComparison.OrdinalIgnoreCase))
                {
                    // Active Directory: the value must be the password in double quotes, UTF-16LE encoded,
                    // and the connection must be encrypted (LDAPS or StartTLS) or AD refuses the write.
                    ldapAttr = new LdapAttribute(passAttr, Encoding.Unicode.GetBytes("\\"" + newPassword + "\\""));
                }
                else
                {
                    ldapAttr = new LdapAttribute(passAttr, newPassword);
                }

                var ldapMod = new LdapModification(LdapModification.Replace, ldapAttr);

                ldapClient.Modify(ldapUser.Dn, ldapMod);
"""

if new in src:
    print(f"{p}: already patched")
    sys.exit(0)
if old not in src:
    print(f"{p}: expected block not found, refusing to patch")
    sys.exit(1)
p.write_text(src.replace(old, new, 1), encoding="utf-8")
print(f"{p}: patched")
