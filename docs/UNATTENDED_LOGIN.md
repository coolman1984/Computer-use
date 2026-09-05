# Unattended login

For a target that accepts username and password without MFA, set its external system profile to `auth.mode: unattended`. The profile contains only the login URL and DOM selectors; it never contains a secret:

```yaml
auth:
  mode: unattended
  login_url: https://target.example/login
  logged_in_selector: '#user-menu'
  login_selector: '#login-form'
  username_selector: '#username'
  password_selector: '#password'
  submit_selector: 'button[type=submit]'
  credential_ref: target-prod
```

Open `http://127.0.0.1:8765/app/credentials.html`, choose the configured target, and save the username/password. On Windows the values are stored in Credential Manager under `SmartOps/<credential_ref>`, scoped to the Windows account running the worker.

The runner reuses the saved Playwright session first. If it is expired, it performs one login attempt, verifies the success selector, refreshes the session state, and continues with Network/API then DOM extraction. Passwords are not written to SQLite, logs, screenshots, traces, error messages, or API responses. Login failures are not retried automatically to avoid account lockout.

The laptop must remain powered on, connected to the corporate network/VPN, and the worker must run under the same Windows account. MFA, smart-card, CAPTCHA, and interactive SSO require a different approved integration.
