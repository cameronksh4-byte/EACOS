---
doc_id: SOP-AUTH-001
title: Self-service password reset and account unlock
system_name: Entra ID
os_platform: any
last_verified_date: 2026-11-01
---
# Self-service password reset and account unlock

## When to use
The user forgot their password, the password expired, or the account is locked after too many attempts.

## Steps for the user
1. Go to the self-service reset portal at https://passwordreset.microsoftonline.com.
2. Enter your work email address and complete the CAPTCHA.
3. Verify your identity with the method you registered (Authenticator app or mobile phone).
4. Choose a new password: at least 14 characters, not used in the last 10 changes.
5. Wait 5 minutes, then sign in again. On a laptop, connect to the office network or VPN so Windows picks up the new password.

## Escalate to Tier 2 when
- The user never registered a verification method (identity must be verified by phone call per SOP-AUTH-002).
- The account is disabled rather than locked.
