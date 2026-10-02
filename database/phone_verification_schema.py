"""Schema and initial data for the unreleased phone-verification contract."""

DEFAULTS = {
    'web_verification_enabled': '0', 'web_verification_method': 'ucaller',
    'web_verification_ucaller_service_id': '', 'web_verification_ucaller_secret_key': '',
    'web_verification_smsaero_email': '', 'web_verification_smsaero_api_key': '',
    'web_verification_smsaero_mobile_sign': '', 'web_verification_smsaero_sms_sign': '',
}


def create_phone_verification_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS auth_challenges (
        id TEXT PRIMARY KEY, phone TEXT NOT NULL, purpose TEXT NOT NULL
            CHECK(purpose IN ('register', 'reset', 'credentials')),
        session_hash TEXT, user_id INTEGER REFERENCES users(id),
        method TEXT NOT NULL CHECK(method IN ('ucaller', 'smsaero_mobile', 'smsaero_sms')),
        code_hash TEXT, proof_hash TEXT, provider_request_id TEXT,
        provider_config_hash TEXT NOT NULL,
        provider_state TEXT NOT NULL DEFAULT 'waiting'
            CHECK(provider_state IN ('waiting', 'code_required', 'confirmed', 'failed')),
        next_provider_check_at INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL CHECK(state IN ('sending', 'sent', 'failed', 'unknown', 'verified', 'used')),
        attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL, verified_at INTEGER, used_at INTEGER,
        CHECK((method = 'smsaero_mobile' AND code_hash IS NULL)
            OR (method != 'smsaero_mobile' AND code_hash IS NOT NULL))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_challenges_expiry ON auth_challenges(expires_at)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_challenges_provider '
                 'ON auth_challenges(method, provider_config_hash, provider_request_id)')
    conn.executemany('INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)', DEFAULTS.items())
