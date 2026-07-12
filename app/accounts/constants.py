"""
Accounts (Auth & Account Management) — constants & policy.

Single source of truth for the account status lifecycle, registration modes,
token types/TTLs, the configurable password policy, module RBAC (users_*), and
default settings seeded into `auth_settings`. UI strings live in the i18n JSON.
"""

# ==========================================================================
# Account status lifecycle
# ==========================================================================
class AccountStatus:
    DRAFT = "draft"
    PENDING_VERIFICATION = "pending_verification"   # email sent, not verified
    EMAIL_VERIFIED = "email_verified"               # verified, awaiting approval
    PENDING_APPROVAL = "pending_approval"           # (alias of email_verified in mode 3)
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    LOCKED = "locked"
    ARCHIVED = "archived"

    ALL = [DRAFT, PENDING_VERIFICATION, EMAIL_VERIFIED, PENDING_APPROVAL, ACTIVE,
           REJECTED, SUSPENDED, LOCKED, ARCHIVED]
    # statuses that may hold a live login session
    CAN_LOGIN = [ACTIVE]

    I18N = {
        DRAFT: "acc.status.draft",
        PENDING_VERIFICATION: "acc.status.pending_verification",
        EMAIL_VERIFIED: "acc.status.email_verified",
        PENDING_APPROVAL: "acc.status.pending_approval",
        ACTIVE: "acc.status.active",
        REJECTED: "acc.status.rejected",
        SUSPENDED: "acc.status.suspended",
        LOCKED: "acc.status.locked",
        ARCHIVED: "acc.status.archived",
    }
    LABEL_EN = {
        DRAFT: "Draft",
        PENDING_VERIFICATION: "Pending Email Verification",
        EMAIL_VERIFIED: "Email Verified",
        PENDING_APPROVAL: "Pending Administrator Approval",
        ACTIVE: "Active",
        REJECTED: "Rejected",
        SUSPENDED: "Suspended",
        LOCKED: "Locked",
        ARCHIVED: "Archived",
    }
    TONE = {
        DRAFT: "muted", PENDING_VERIFICATION: "warn", EMAIL_VERIFIED: "info",
        PENDING_APPROVAL: "warn", ACTIVE: "ok", REJECTED: "crit",
        SUSPENDED: "crit", LOCKED: "crit", ARCHIVED: "muted",
    }


class RegistrationMode:
    EMAIL_ONLY = "email_only"              # verify email -> active
    ADMIN_ONLY = "admin_only"             # admin approval -> active
    EMAIL_THEN_ADMIN = "email_then_admin"  # verify email -> approval -> active (default)
    INVITE_ONLY = "invite_only"          # only pre-invited emails may register
    ALL = [EMAIL_ONLY, ADMIN_ONLY, EMAIL_THEN_ADMIN, INVITE_ONLY]


class TokenType:
    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"


class AuthEvent:
    REGISTER = "register"
    VERIFY_SENT = "verify_sent"
    VERIFY_OK = "verify_ok"
    VERIFY_FAIL = "verify_fail"
    RESEND_VERIFY = "resend_verify"
    APPROVE = "approve"
    REJECT = "reject"
    SUSPEND = "suspend"
    REACTIVATE = "reactivate"
    UNLOCK = "unlock"
    ROLE_ASSIGN = "role_assign"
    FORGOT_REQUEST = "forgot_request"
    RESET_SENT = "reset_sent"
    RESET_OK = "reset_ok"
    RESET_FAIL = "reset_fail"
    PASSWORD_CHANGED = "password_changed"
    LOGIN_OK = "login_ok"
    LOGIN_FAIL = "login_fail"
    LOGIN_BLOCKED = "login_blocked"
    RATE_LIMITED = "rate_limited"


# ==========================================================================
# Password policy (defaults; overridable in auth_settings)
# ==========================================================================
PW_MIN_LENGTH = 12
PW_REQUIRE_UPPER = True
PW_REQUIRE_LOWER = True
PW_REQUIRE_DIGIT = True
PW_REQUIRE_SPECIAL = True
PW_HISTORY_COUNT = 5          # block reuse of the last N passwords
SPECIALS = r"""!@#$%^&*()_+-=[]{}|;:'\",.<>/?`~"""

# A compact deny-list of the most common / breached passwords. An offline check
# (full HIBP k-anonymity is a documented integration point requiring network).
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "123456", "12345678",
    "123456789", "1234567890", "qwerty", "qwerty123", "abc123", "111111",
    "letmein", "welcome", "welcome1", "admin", "admin123", "root", "iloveyou",
    "monkey", "dragon", "sunshine", "princess", "football", "baseball",
    "master", "login", "starwars", "changeme", "trustno1", "whatever",
    "tcgarments", "tc@12345", "tcgarments1", "garments", "company123",
    "p@ssw0rd", "p@ssword", "aa123456", "1q2w3e4r", "1qaz2wsx", "zaq12wsx",
    "qazwsx", "112233", "123123", "654321", "000000", "121212", "q1w2e3r4",
}


# ==========================================================================
# Token TTLs (minutes) — overridable in auth_settings / env
# ==========================================================================
VERIFY_TTL_MIN = 24 * 60      # 24h
RESET_TTL_MIN = 20            # 15–30 recommended
INVITE_TTL_MIN = 7 * 24 * 60  # 7 days

# Rate-limit windows (per identifier + per ip)
FORGOT_MAX_PER_HOUR = 5
RESEND_COOLDOWN_SEC = 60
RESEND_MAX_PER_HOUR = 5


# ==========================================================================
# Default settings seeded into auth_settings (all string values)
# ==========================================================================
DEFAULT_SETTINGS = {
    "registration_enabled": "1",
    "registration_mode": RegistrationMode.EMAIL_THEN_ADMIN,
    "default_role": "normal_user",           # safest default; never admin
    "pw_min_length": str(PW_MIN_LENGTH),
    "pw_require_upper": "1",
    "pw_require_lower": "1",
    "pw_require_digit": "1",
    "pw_require_special": "1",
    "pw_history_count": str(PW_HISTORY_COUNT),
    "verify_ttl_min": str(VERIFY_TTL_MIN),
    "reset_ttl_min": str(RESET_TTL_MIN),
    "forgot_max_per_hour": str(FORGOT_MAX_PER_HOUR),
    "resend_cooldown_sec": str(RESEND_COOLDOWN_SEC),
    "email_domain_allowlist": "",            # comma-separated; blank = any
    "require_terms": "1",
    "require_privacy": "1",
}

# Fields whose distinct existing values seed the org dropdowns (auth_org_options).
ORG_KINDS = ["company", "department", "location", "job_title", "manager"]


# ==========================================================================
# RBAC — merged into the platform catalogue by app.security
# ==========================================================================
USERS_PERMISSIONS = [
    "users_view",
    "users_create",
    "users_approve",
    "users_reject",
    "users_suspend",
    "users_unlock",
    "users_assign_role",
    "users_initiate_password_reset",
    "users_view_security_audit",
]

# IT leadership manages registrations by default; super_admin already has "*".
USERS_ROLE_PERMS = {
    "it_director": list(USERS_PERMISSIONS),
    "it_manager": ["users_view", "users_approve", "users_reject",
                   "users_initiate_password_reset", "users_view_security_audit"],
}
USERS_ROLE_LABELS = {}

PERMISSION_LABELS = {
    "users_view": "Accounts: view registrations",
    "users_create": "Accounts: create users",
    "users_approve": "Accounts: approve registrations",
    "users_reject": "Accounts: reject registrations",
    "users_suspend": "Accounts: suspend / reactivate",
    "users_unlock": "Accounts: unlock",
    "users_assign_role": "Accounts: assign role & scope",
    "users_initiate_password_reset": "Accounts: initiate password reset",
    "users_view_security_audit": "Accounts: view security audit",
}
PERMISSION_DESC = {
    "users_view": "See pending registrations and account details.",
    "users_create": "Create user accounts directly.",
    "users_approve": "Approve a registration and assign a role/scope.",
    "users_reject": "Reject a registration with a mandatory reason.",
    "users_suspend": "Suspend or reactivate an account.",
    "users_unlock": "Unlock a locked account.",
    "users_assign_role": "Assign an authorised role and organisation scope.",
    "users_initiate_password_reset": "Send a secure password-reset email.",
    "users_view_security_audit": "View the account security audit trail.",
}
