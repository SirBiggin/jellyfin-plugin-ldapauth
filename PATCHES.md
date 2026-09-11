# Fork patches: technical notes

This fork of [jellyfin/jellyfin-plugin-ldapauth](https://github.com/jellyfin/jellyfin-plugin-ldapauth)
carries two small changes for use against Active Directory. Both live on the branches
`patch-v23` (Jellyfin 10.11.x, plugin 23.x) and `patch-v24` (Jellyfin 12.x, plugin 24.x).
Packaged builds are published through
[SirBiggin/jellyfin-plugin-repo](https://github.com/SirBiggin/jellyfin-plugin-repo).

| Patch | Versions | Status |
|---|---|---|
| 1. `unicodePwd` password change for AD | 23.0.1.0 / 24.0.1.0 | Published |
| 2. Skip case-only username rename | 23.0.2.0 / 24.0.2.0 | Published |

---

## Patch 1: `ChangePassword` against Active Directory

Upstream issues: [#164](https://github.com/jellyfin/jellyfin-plugin-ldapauth/issues/164),
[#200](https://github.com/jellyfin/jellyfin-plugin-ldapauth/issues/200).

### Symptom

With **Allow Password Change** enabled and **LDAP Password Attribute** set to `unicodePwd`,
changing a password from the Jellyfin profile page fails. The Jellyfin log shows:

```
LdapException: Unwilling To Perform (53)
LdapException: Server Message: 0000001F: SvcErr: DSID-031A126C, problem 5003 (WILL_NOT_PERFORM), data 0
```

### Cause

`ChangePassword` first tries the LDAP Password Modify extended operation (RFC 3062).
Active Directory does not advertise it, so the plugin falls back to writing the configured
password attribute directly:

```csharp
var ldapAttr = new LdapAttribute(passAttr, newPassword);   // plain UTF-8 string
```

AD has strict rules for `unicodePwd` writes:

1. The value must be the new password **enclosed in double quotes**, encoded as **UTF-16LE**.
2. The connection must be encrypted (LDAPS or StartTLS).
3. A single `replace` of the attribute is an administrative **reset**, and needs the
   *Reset Password* control-access right on the target user (plus write on `pwdLastSet`).

A plain UTF-8 string fails rule 1, and AD answers with `0000001F` (`ERROR_GEN_FAILURE`),
regardless of the password's content or policy.

### Change

`LDAP-Auth/LDAPAuthenticationProviderPlugin.cs`, in `ChangePassword`:

```csharp
LdapAttribute ldapAttr;
if (string.Equals(passAttr, "unicodePwd", StringComparison.OrdinalIgnoreCase))
{
    // Active Directory: the value must be the password in double quotes, UTF-16LE encoded,
    // and the connection must be encrypted (LDAPS or StartTLS) or AD refuses the write.
    ldapAttr = new LdapAttribute(passAttr, Encoding.Unicode.GetBytes("\"" + newPassword + "\""));
}
else
{
    ldapAttr = new LdapAttribute(passAttr, newPassword);
}
var ldapMod = new LdapModification(LdapModification.Replace, ldapAttr);
ldapClient.Modify(ldapUser.Dn, ldapMod);
```

Any other attribute name keeps upstream behaviour. `Encoding.Unicode` in .NET is UTF-16LE.

### Behavioural notes

- The write is made on the **bind account's** connection, not the user's. It is therefore an
  admin reset: minimum password age and password history are bypassed, and the user's current
  password is not re-checked by AD (Jellyfin already verified it before calling `ChangePassword`).
- Because it is a reset, the bind account needs delegated rights on the users' OU:
  *Reset Password* extended right and read/write on `pwdLastSet`.
- AD still enforces length and complexity. A policy failure returns a different code,
  `0000052D` (`ERROR_PASSWORD_RESTRICTION`), so the two are easy to tell apart in the log.
- Verified 2026-09-10/11 against Windows Server 2022 DCs over LDAPS 636: the reset is logged on
  the DC as event 4724 with the bind account as subject.

---

## Patch 2: skip the rename when names differ only by case

Upstream issue: [#201](https://github.com/jellyfin/jellyfin-plugin-ldapauth/issues/201).

### Symptom

A user whose Jellyfin username differs from their AD `sAMAccountName` **only in letter case**
(for example Jellyfin `Pops`, AD `pops`) cannot log in. The web client reports
"Connection Failure. We're unable to connect to the selected server right now", because the
login request returned HTTP 500. The Jellyfin log shows:

```
[ERR] Error processing request. URL "POST" "/Users/authenticatebyname".
System.ArgumentException: The new and old names must be different.
   at Jellyfin.Server.Implementations.Users.UserManager.RenameUser(Guid userId, String oldName, String newName)
   at Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin.Authenticate(String username, String password)
```

The AD bind itself succeeded; the failure is entirely on the Jellyfin side after authentication.

### How Jellyfin compares usernames (10.11+)

`Jellyfin.Server.Implementations/Users/UserManager.cs` keeps a `NormalizedUsername` column
holding the upper-cased name, and every name operation uses it:

| Operation | Comparison |
|---|---|
| `GetUserByName(name)` | `u.NormalizedUsername == name.ToUpperInvariant()` |
| `CreateUserAsync(name)` | refuses if any `NormalizedUsername == name.ToUpperInvariant()` |
| `RenameUser(id, oldName, newName)` | throws `"The new and old names must be different."` when `oldName.Equals(newName, StringComparison.OrdinalIgnoreCase)`; refuses if another user's `NormalizedUsername` matches |

So Jellyfin usernames are **case-insensitive**: `Pops` and `pops` are the same user and can
never coexist. That uniqueness rule is enforced inside Jellyfin core and is not reachable
from a plugin.

### What the stock plugin does

After a successful AD bind, `Authenticate` locates the existing Jellyfin user and then:

```csharp
// User exists; if needed update its username
if (!string.Equals(user.Username, ldapUsername, StringComparison.Ordinal))
{
    await userManager.RenameUser(user.Id, user.Username, ldapUsername);
}
```

`user.Username` is the name stored on the Jellyfin user; `ldapUsername` is the value of the
configured username attribute (`sAMAccountName` for AD) returned by the directory.
The plugin compares them **case-sensitively**, sees `Pops` != `pops`, and calls `RenameUser`.
Jellyfin compares them case-insensitively, sees no change, and throws. The exception
propagates out of the login request.

### Change

One comparison, same file:

```diff
-if (!string.Equals(user.Username, ldapUsername, StringComparison.Ordinal))
+// Jellyfin treats usernames case-insensitively and rejects a rename that only changes case,
+// so only rename when the names really differ.
+if (!string.Equals(user.Username, ldapUsername, StringComparison.OrdinalIgnoreCase))
```

### Scope of the change

The comparison involves exactly two strings, the logging-in user's Jellyfin name and that same
account's directory name, and decides exactly one thing: whether to ask Jellyfin for a rename.

It does **not** affect:

- Which Jellyfin user is logged in. Jellyfin core resolves that from the typed name
  (case-insensitively, via `GetUserByName`) before the plugin runs, and passes the result in.
- Password verification. The directory performs the bind with the credentials as typed.
- User creation or duplicate detection. Both remain in Jellyfin core and run unchanged.
- Any other user. Only the authenticating account and its own directory entry are compared.

If the two names differ in anything other than case, the rename still happens exactly as
upstream does it. The stock behaviour in the case-only situation is a guaranteed exception, so
there is no scenario in which the skipped call would have succeeded.

### Status

Shipped as 23.0.2.0 / 24.0.2.0 on 2026-09-10, briefly withdrawn on 2026-09-11 while the scope of
the change was reviewed (commits `2829016` / `c0a00e2` reverted it, `18f7496` / `c5af29a`
reapplied it), and republished the same day. Both versions are current.

Without this patch, every account whose Jellyfin name and AD `sAMAccountName` differ only in
case fails to log in. The workaround on stock code is to make the names match exactly; the
Jellyfin dashboard also refuses a case-only rename, so it must go through an intermediate name
(for example `Pops` -> `Pops2` -> `pops`).
