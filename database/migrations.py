"""Database v134 baseline and subsequent incremental migrations.

The preceding ordered blocking release completes the historical chain through
v134. Fresh databases start at that same version; installed v134 data is never
reseeded. Future changes continue with migration 135 and higher.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import sqlite3

from localization import catalog, seed_text
from .connection import get_db
from .db_settings import _ensure_yadreno_admin_application_id
from .db_user_ui_texts import update_user_ui_text_defaults
from .user_ui_text_catalog import USER_UI_TEXT_DEFINITIONS

logger = logging.getLogger(__name__)

# The preceding blocking release guarantees this schema before an upgrade.
INITIAL_VERSION = 134
LATEST_VERSION = 134


DEFAULT_BROADCAST_STYLE_PROFILE = {
    "schema_version": 1,
    "tone": "friendly_professional",
    "address": "polite_you",
    "emoji_level": "medium",
    "length": "compact",
    "headline": "emoji_bold",
    "paragraphs": "short",
    "cta": "direct_calm",
    "use_lists": True,
    "custom_instructions": "",
}


def get_current_version() -> int:
    """
    Gets the current version of the database schema.
    
    Returns:
        int: Version number (0 if version table does not exist)
    """
    with get_db() as conn:
        # Checking the existence of the schema_version table
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if not cursor.fetchone():
            return 0
        
        cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        return row["version"] if row else 0


def set_version(conn: sqlite3.Connection, version: int) -> None:
    """
    Sets the database schema version.
    
    Args:
        conn: Connection to the database
        version: Version number
    """
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


_BASELINE_SCHEMA_V134 = r"""
CREATE TABLE account_credentials (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            phone TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            phone_verified INTEGER NOT NULL DEFAULT 0 CHECK(phone_verified IN (0, 1)),
            version INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL
        );

CREATE TABLE account_link_requests (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            session_hash TEXT NOT NULL REFERENCES account_sessions(token_hash),
            telegram_id INTEGER, telegram_username TEXT, telegram_first_name TEXT, telegram_last_name TEXT,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            telegram_confirmed_at INTEGER, completed_at INTEGER,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending', 'confirmed', 'completed', 'conflict'))
        , bot_id INTEGER, target_user_id INTEGER REFERENCES users(id), target_credential_version INTEGER);

CREATE TABLE account_operations (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            kind TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            request_json TEXT NOT NULL, result_json TEXT,
            order_id TEXT REFERENCES payments(order_id),
            created_at INTEGER NOT NULL, completed_at INTEGER, purged_at INTEGER, last_error_code TEXT,
            UNIQUE(user_id, kind, idempotency_key)
        );

CREATE TABLE account_sessions (
            token_hash TEXT PRIMARY KEY, csrf_hash TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id),
            source TEXT NOT NULL CHECK(source IN ('site', 'mini_app')),
            credential_version INTEGER NOT NULL,
            created_at INTEGER NOT NULL, authenticated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, revoked_at INTEGER
        , authentication_method TEXT NOT NULL DEFAULT 'password');

CREATE TABLE auth_challenges (
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
    );

CREATE TABLE auth_rate_limits (
            bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL
        );

CREATE TABLE balance_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_cents INTEGER NOT NULL,
            balance_before INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            performed_by INTEGER,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        , currency TEXT NOT NULL DEFAULT 'RUB', delta_minor INTEGER NOT NULL DEFAULT 0);

CREATE TABLE base_currency_switches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            to_units_per_from TEXT NOT NULL,
            admin_telegram_id INTEGER NOT NULL,
            backup_path TEXT NOT NULL,
            converted_tariffs INTEGER NOT NULL DEFAULT 0,
            converted_balances INTEGER NOT NULL DEFAULT 0,
            converted_referral_rows INTEGER NOT NULL DEFAULT 0,
            canceled_intents INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE core_events (
        event_id TEXT PRIMARY KEY,
        event_name TEXT NOT NULL,
        source_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE(event_name, source_id)
    );

CREATE TABLE currency_rates (
            base_currency TEXT NOT NULL,
            target_currency TEXT NOT NULL,
            units_per_base TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (base_currency, target_currency)
        );

CREATE TABLE extension_completion_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            extension_id TEXT NOT NULL,
            handler_name TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES payments(order_id) ON DELETE CASCADE,
            key_id INTEGER REFERENCES vpn_keys(id) ON DELETE SET NULL,
            stage TEXT NOT NULL DEFAULT 'key_configured',
            state TEXT NOT NULL DEFAULT 'waiting'
                CHECK(state IN (
                    'waiting', 'ready', 'processing', 'retry',
                    'completed', 'degraded'
                )),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TIMESTAMP,
            lease_until TIMESTAMP,
            last_error_code TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            UNIQUE(extension_id, handler_name, workflow_id),
            CHECK(LENGTH(TRIM(extension_id)) > 0),
            CHECK(LENGTH(TRIM(handler_name)) > 0),
            CHECK(LENGTH(TRIM(workflow_id)) > 0),
            CHECK(attempts >= 0)
        );

CREATE TABLE extension_core_operations (
            extension_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            target_user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            status TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, request_fingerprint TEXT,
            PRIMARY KEY (extension_id, idempotency_key)
        );

CREATE TABLE extension_event_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL REFERENCES core_events(event_id),
        extension_id TEXT NOT NULL,
        handler_name TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'processing', 'completed', 'degraded')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        lease_until TEXT,
        claim_token TEXT,
        last_error_code TEXT,
        completed_at TEXT,
        UNIQUE(event_id, extension_id, handler_name)
    );

CREATE TABLE extension_scheduled_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL UNIQUE,
        contract_version INTEGER NOT NULL DEFAULT 1 CHECK(contract_version = 1),
        extension_id TEXT NOT NULL,
        handler_name TEXT NOT NULL,
        user_id INTEGER,
        telegram_id INTEGER,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'processing', 'completed', 'degraded')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        lease_until TEXT,
        claim_token TEXT,
        last_error_code TEXT,
        completed_at TEXT
    );

CREATE TABLE extension_schema_versions (
            extension_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE extension_storage (
            extension_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, key)
        );

CREATE TABLE key_entitlements (
            key_id INTEGER PRIMARY KEY REFERENCES vpn_keys(id) ON DELETE CASCADE,
            order_id TEXT NOT NULL REFERENCES payments(order_id),
            tariff_json TEXT NOT NULL
        );

CREATE TABLE key_lifecycle_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL REFERENCES vpn_keys(id) ON DELETE CASCADE,
            event_name TEXT NOT NULL,
            event_token TEXT NOT NULL,
            metadata_json TEXT,
            emitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (vpn_key_id, event_name, event_token)
        );

CREATE TABLE key_operation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_days INTEGER DEFAULT 0,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            expires_before TEXT,
            expires_after TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE lapsed_coupon_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            lapse_token TEXT NOT NULL,
            lapsed_at TIMESTAMP NOT NULL,
            coupon_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'failed', 'canceled')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            UNIQUE (user_id, lapse_token),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (coupon_id) REFERENCES promo_codes(id) ON DELETE SET NULL
        );

CREATE TABLE module_uploads (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), module_id TEXT NOT NULL,
        filename TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL,
        relative_path TEXT NOT NULL UNIQUE, persistent INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, telegram_file_id TEXT
    );

CREATE TABLE module_webhook_bindings (
        module_id TEXT NOT NULL, name TEXT NOT NULL, reference TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY(module_id,name,reference)
    );

CREATE TABLE module_webhooks (
        id TEXT PRIMARY KEY, module_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, event_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, payload_json TEXT, task_id TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        UNIQUE(module_id,name,event_id)
    );

CREATE TABLE notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            sent_at DATE NOT NULL,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id)
        );

CREATE TABLE page_routes (
            route_key TEXT PRIMARY KEY,
            page_key TEXT NOT NULL,
            guard_names TEXT NOT NULL DEFAULT '[]',
            hook_names TEXT NOT NULL DEFAULT '[]',
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (page_key) REFERENCES pages(page_key)
        );

CREATE TABLE pages (
            page_key         TEXT PRIMARY KEY,
            text_default     TEXT NOT NULL DEFAULT '',
            image_default    TEXT,
            media_type_default TEXT,
            buttons_default  TEXT NOT NULL DEFAULT '[]',
            text_custom      TEXT,
            image_custom     TEXT,
            media_type_custom TEXT,
            updated_at       TIMESTAMP,
            buttons_custom   TEXT,
            guard_names      TEXT NOT NULL DEFAULT '[]',
            hook_names       TEXT NOT NULL DEFAULT '[]'
        , page_kind TEXT NOT NULL DEFAULT 'unknown' CHECK (page_kind IN ('core', 'custom', 'legacy_custom', 'unknown')));

CREATE TABLE panel_identity_renames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_id INTEGER NOT NULL REFERENCES vpn_keys(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        server_id INTEGER NOT NULL REFERENCES servers(id),
        old_email TEXT NOT NULL, new_email TEXT NOT NULL, sub_id TEXT NOT NULL,
        snapshot_json TEXT, state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'running', 'done')),
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0,
        error_code TEXT, created_at INTEGER NOT NULL, completed_at INTEGER
    );

CREATE TABLE payment_auto_checks (
            order_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active'
                CHECK (state IN (
                    'active', 'provider_succeeded', 'completed',
                    'canceled', 'exhausted', 'completion_failed'
                )),
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            next_check_at TIMESTAMP,
            last_check_at TIMESTAMP,
            check_attempts INTEGER NOT NULL DEFAULT 0,
            completion_attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        );

CREATE TABLE payment_effects (
            order_id TEXT NOT NULL,
            effect_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'started'
                CHECK (status IN ('started', 'completed', 'failed')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            attempts INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            PRIMARY KEY (order_id, effect_name),
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        );

CREATE TABLE payment_offers (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            payload_json TEXT NOT NULL, quote_json TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            order_id TEXT REFERENCES payments(order_id)
        );

CREATE TABLE payment_order_terms (
            order_id TEXT PRIMARY KEY REFERENCES payments(order_id),
            version INTEGER NOT NULL CHECK(version = 1),
            source TEXT NOT NULL CHECK(source IN ('telegram','site','mini_app','system')),
            tariff_json TEXT NOT NULL
        , pricing_json TEXT);

CREATE TABLE payment_provider_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL UNIQUE,
            provider_id TEXT NOT NULL,
            payment_type TEXT NOT NULL,
            provider_payment_id TEXT,
            payment_url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, purpose TEXT, charge_amount TEXT, charge_currency TEXT,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        );

CREATE TABLE payment_referral_effects (
            order_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            referrer_id INTEGER NOT NULL,
            payer_id INTEGER NOT NULL,
            reward_cents INTEGER NOT NULL DEFAULT 0,
            reward_days INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, reward_currency TEXT NOT NULL DEFAULT 'RUB', reward_minor INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (order_id, level),
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        );

CREATE TABLE "payments" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER,
            order_id TEXT NOT NULL UNIQUE,
            payment_type TEXT,
            period_days INTEGER,
            status TEXT DEFAULT 'paid',
            paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            promo_code_id INTEGER,
            promo_code TEXT,
            discount_percent INTEGER DEFAULT 0,
            is_promo_free INTEGER DEFAULT 0,
            intent_version INTEGER NOT NULL DEFAULT 1,
            purpose TEXT NOT NULL,
            purpose_data_json TEXT NOT NULL DEFAULT '{}',
            charge_amount TEXT,
            charge_currency TEXT,
            rate_snapshot_json TEXT NOT NULL DEFAULT '{}',
            description TEXT,
            success_target_json TEXT NOT NULL DEFAULT '{}',
            cancel_target_json TEXT NOT NULL DEFAULT '{}',
            fulfillment_status TEXT NOT NULL DEFAULT 'pending',
            fulfillment_attempts INTEGER NOT NULL DEFAULT 0,
            fulfillment_started_at TIMESTAMP,
            fulfillment_last_error TEXT,
            provider_confirmed_at TIMESTAMP,
            fulfilled_at TIMESTAMP,
            created_at TIMESTAMP,
            base_currency TEXT NOT NULL DEFAULT 'RUB',
            nominal_amount_minor INTEGER NOT NULL DEFAULT 0,
            payable_amount_minor INTEGER NOT NULL DEFAULT 0,
            balance_deduct_minor INTEGER NOT NULL DEFAULT 0,
            origin_extension_id TEXT,
            origin_context_version INTEGER,
            origin_context_json TEXT NOT NULL DEFAULT '{}',
            origin_workflow_id TEXT,
            origin_completion_handler TEXT, cancellation_source TEXT CHECK (cancellation_source IN ('provider', 'local')),
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        );

CREATE TABLE promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('promo', 'coupon')),
            code TEXT NOT NULL UNIQUE,
            discount_percent INTEGER NOT NULL DEFAULT 0
                CHECK (discount_percent >= 0 AND discount_percent <= 100),
            expires_at TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1,
            activation_limit INTEGER,
            usage_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',
            issued_to_user_id INTEGER,
            created_by_admin_id INTEGER,
            snapshot_discount_percent INTEGER,
            snapshot_lifetime_days INTEGER,
            snapshot_generated_at TIMESTAMP,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (issued_to_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

CREATE TABLE promo_link_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            user_id INTEGER,
            telegram_id INTEGER NOT NULL,
            start_param TEXT NOT NULL,
            converted_order_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            converted_at TIMESTAMP,
            FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );

CREATE TABLE promo_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            code TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved', 'applied', 'canceled')),
            payment_type TEXT,
            action TEXT,
            original_amount INTEGER NOT NULL DEFAULT 0,
            discount_amount INTEGER NOT NULL DEFAULT 0,
            final_amount INTEGER NOT NULL DEFAULT 0,
            amount_unit TEXT NOT NULL DEFAULT 'cents',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied_at TIMESTAMP,
            FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE referral_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level_number INTEGER NOT NULL UNIQUE,
            percent INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1
        );

CREATE TABLE referral_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referral_id INTEGER NOT NULL,
            level INTEGER NOT NULL,
            total_payments_count INTEGER DEFAULT 0,
            total_reward_cents INTEGER DEFAULT 0,
            total_reward_days INTEGER DEFAULT 0, reward_currency TEXT NOT NULL DEFAULT 'RUB', total_reward_minor INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (referrer_id) REFERENCES users(id),
            FOREIGN KEY (referral_id) REFERENCES users(id),
            UNIQUE (referrer_id, referral_id, level)
        );

CREATE TABLE schema_version (
            version INTEGER NOT NULL
        );

CREATE TABLE semantic_action_contexts (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            owner_extension_id TEXT NOT NULL,
            action TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            workflow_id TEXT NOT NULL,
            completion_handler TEXT,
            state TEXT NOT NULL DEFAULT 'active'
                CHECK(state IN ('active', 'consumed', 'canceled', 'expired')),
            order_id TEXT REFERENCES payments(order_id) ON DELETE SET NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            consumed_at TIMESTAMP,
            UNIQUE(owner_extension_id, workflow_id),
            CHECK(LENGTH(TRIM(token)) > 0),
            CHECK(LENGTH(TRIM(owner_extension_id)) > 0),
            CHECK(LENGTH(TRIM(action)) > 0),
            CHECK(schema_version > 0),
            CHECK(LENGTH(TRIM(workflow_id)) > 0),
            CHECK(completion_handler IS NULL OR LENGTH(TRIM(completion_handler)) > 0)
        );

CREATE TABLE server_groups (
            server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            group_id  INTEGER NOT NULL REFERENCES tariff_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (server_id, group_id)
        );

CREATE TABLE "servers" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            web_base_path TEXT NOT NULL,
            login TEXT NOT NULL,
            password TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            protocol TEXT DEFAULT 'https',
            api_token TEXT,
            panel_version TEXT,
            panel_checked_at TEXT
        , inbound_group_id INTEGER
            CHECK (
                inbound_group_id IS NULL
                OR (
                    TYPEOF(inbound_group_id) = 'integer'
                    AND inbound_group_id > 0
                )
            ));

CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

CREATE TABLE subscription_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_key_id INTEGER NOT NULL
                REFERENCES vpn_keys(id) ON DELETE CASCADE,
            component_key_id INTEGER NOT NULL
                REFERENCES vpn_keys(id) ON DELETE CASCADE,
            source_namespace TEXT NOT NULL DEFAULT 'core',
            source_reference TEXT,
            managed_token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(host_key_id, component_key_id),
            CHECK(host_key_id <> component_key_id),
            CHECK(LENGTH(TRIM(source_namespace)) > 0),
            CHECK(LENGTH(TRIM(managed_token)) > 0)
        );

CREATE TABLE subscription_composition_sync (
            host_key_id INTEGER PRIMARY KEY
                REFERENCES vpn_keys(id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending', 'synced', 'retrying', 'blocked')),
            desired_revision INTEGER NOT NULL DEFAULT 0,
            applied_revision INTEGER NOT NULL DEFAULT 0,
            applied_tokens_json TEXT NOT NULL DEFAULT '[]',
            applied_host_fingerprint TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMP,
            lease_until TIMESTAMP,
            lease_owner_token TEXT,
            last_attempt_at TIMESTAMP,
            last_error_code TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(desired_revision >= 0),
            CHECK(applied_revision >= 0),
            CHECK(attempts >= 0)
        );

CREATE TABLE support_admin_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            admin_telegram_id INTEGER NOT NULL,
            card_message_id INTEGER,
            copy_message_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, support_message_id INTEGER REFERENCES support_messages(id) ON DELETE CASCADE,
            FOREIGN KEY (thread_id) REFERENCES support_threads(id) ON DELETE CASCADE
        );

CREATE TABLE "support_messages" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'admin')),
            sender_telegram_id INTEGER,
            recipient_telegram_id INTEGER,
            text_html TEXT NOT NULL DEFAULT '',
            media_type TEXT,
            media_file_id TEXT,
            source_chat_id INTEGER,
            source_message_id INTEGER,
            origin_type TEXT NOT NULL DEFAULT 'telegram'
                CHECK (origin_type IN ('telegram', 'extension')),
            origin_extension_id TEXT,
            origin_operation_key TEXT,
            delivered_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, uploaded_file_id TEXT,
            CHECK (
                (
                    origin_type = 'telegram'
                    AND sender_telegram_id IS NOT NULL
                    AND source_chat_id IS NOT NULL
                    AND source_message_id IS NOT NULL
                    AND origin_extension_id IS NULL
                    AND origin_operation_key IS NULL
                )
                OR (
                    origin_type = 'extension'
                    AND origin_extension_id IS NOT NULL
                    AND LENGTH(TRIM(origin_extension_id)) > 0
                    AND origin_operation_key IS NOT NULL
                    AND LENGTH(TRIM(origin_operation_key)) > 0
                )
            ),
            FOREIGN KEY (thread_id) REFERENCES support_threads(id) ON DELETE CASCADE
        );

CREATE TABLE "support_threads" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_telegram_id INTEGER ,
            initiator_type TEXT NOT NULL CHECK (initiator_type IN ('user', 'admin')),
            initiator_admin_id INTEGER,
            assigned_admin_id INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, channel TEXT NOT NULL DEFAULT 'telegram',
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE tariff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 1,
            monthly_traffic_reset_enabled INTEGER NOT NULL DEFAULT 0
                CHECK (monthly_traffic_reset_enabled IN (0, 1)),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        , subscription_parent_group_id INTEGER REFERENCES tariff_groups(id) ON DELETE SET NULL);

CREATE TABLE "tariffs" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            display_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            traffic_limit_gb INTEGER DEFAULT 0,
            group_id INTEGER DEFAULT 1,
            max_ips INTEGER DEFAULT 1,
            system_type TEXT,
            price_minor INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE telegram_login_challenges (
        browser_hash TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL, expires_at INTEGER NOT NULL
    );

CREATE TABLE trial_activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            offer_id INTEGER,
            tariff_id INTEGER,
            group_id INTEGER,
            vpn_key_id INTEGER REFERENCES vpn_keys(id) ON DELETE SET NULL,
            payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL,
            legacy_global_block INTEGER NOT NULL DEFAULT 0
                CHECK (legacy_global_block IN (0, 1)),
            activated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE trial_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tariff_id INTEGER REFERENCES tariffs(id) ON DELETE RESTRICT,
            is_primary INTEGER NOT NULL DEFAULT 0
                CHECK (is_primary IN (0, 1)),
            is_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (is_enabled IN (0, 1)),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (is_primary = 1 OR tariff_id IS NOT NULL)
        );

CREATE TABLE user_ui_texts (
            text_key TEXT PRIMARY KEY,
            text_default TEXT NOT NULL,
            text_custom TEXT,
            text_format TEXT NOT NULL CHECK (text_format IN ('html', 'plain', 'button')),
            description TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE "users" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER  UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_banned INTEGER DEFAULT 0,
            is_bot_blocked INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_trial INTEGER DEFAULT 0,
            referral_code TEXT,
            referred_by INTEGER REFERENCES users(id),
            personal_balance INTEGER DEFAULT 0,
            referral_coefficient REAL DEFAULT 1.0,
            active_promo_code_id INTEGER
        , last_key_number INTEGER NOT NULL DEFAULT 0 CHECK (last_key_number >= 0), merged_into_user_id INTEGER REFERENCES users(id), account_name TEXT);

CREATE TABLE "vpn_keys" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            server_id INTEGER,
            tariff_id INTEGER NOT NULL,
            panel_email TEXT,
            custom_name TEXT,
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            traffic_updated_at DATETIME,
            traffic_notified_pct INTEGER DEFAULT 100,
            sub_id TEXT,
            traffic_limit_override INTEGER
                CHECK (traffic_limit_override IS NULL OR traffic_limit_override >= 0),
            max_ips_override INTEGER
                CHECK (max_ips_override IS NULL OR max_ips_override BETWEEN 0 AND 999), inactive_since TEXT, inactive_event_token TEXT,
            CHECK (
                (server_id IS NULL AND panel_email IS NULL AND sub_id IS NULL)
                OR
                (server_id IS NOT NULL
                 AND panel_email IS NOT NULL
                 AND sub_id IS NOT NULL
                 AND LENGTH(TRIM(panel_email)) > 0
                 AND LENGTH(TRIM(sub_id)) > 0)
            ),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        );

CREATE INDEX idx_account_sessions_owner
            ON account_sessions(user_id, expires_at);

CREATE INDEX idx_auth_challenges_expiry ON auth_challenges(expires_at);

CREATE UNIQUE INDEX idx_auth_challenges_provider ON auth_challenges(method, provider_config_hash, provider_request_id);

CREATE UNIQUE INDEX idx_balance_operations_payment_referral ON balance_operations(user_id, operation_type, source, reference_type, reference_id) WHERE reference_type = 'payment_referral' AND reference_id IS NOT NULL;

CREATE UNIQUE INDEX idx_balance_operations_payment_topup
        ON balance_operations(reference_type, reference_id)
        WHERE reference_type = 'payment_topup' AND reference_id IS NOT NULL;

CREATE INDEX idx_balance_operations_reference ON balance_operations(reference_type, reference_id);

CREATE INDEX idx_balance_operations_user_created ON balance_operations(user_id, created_at);

CREATE INDEX idx_extension_completion_jobs_due
        ON extension_completion_jobs(state, next_retry_at, lease_until)
        ;

CREATE INDEX idx_extension_events_due
        ON extension_event_deliveries(state, next_attempt_at, lease_until, id);

CREATE INDEX idx_extension_events_handler
        ON extension_event_deliveries(extension_id, handler_name, state);

CREATE INDEX idx_extension_tasks_due
        ON extension_scheduled_tasks(state, next_attempt_at, lease_until, id);

CREATE INDEX idx_extension_tasks_handler
        ON extension_scheduled_tasks(extension_id, handler_name, state);

CREATE INDEX idx_key_lifecycle_event_emitted ON key_lifecycle_event_log(emitted_at);

CREATE INDEX idx_key_lifecycle_event_lookup ON key_lifecycle_event_log(event_name, vpn_key_id, event_token);

CREATE INDEX idx_key_operation_log_key_created ON key_operation_log(vpn_key_id, created_at);

CREATE INDEX idx_key_operation_log_user_created ON key_operation_log(user_id, created_at);

CREATE UNIQUE INDEX idx_key_operations_payment_reward ON key_operation_log(user_id, source, reference_type, reference_id) WHERE reference_type IN ('payment_referral', 'payment_promo_reward') AND reference_id IS NOT NULL;

CREATE INDEX idx_lapsed_coupon_deliveries_coupon
        ON lapsed_coupon_deliveries(coupon_id);

CREATE INDEX idx_lapsed_coupon_deliveries_due
        ON lapsed_coupon_deliveries(status, lapsed_at, id);

CREATE INDEX idx_module_receipt_cleanup ON account_operations(completed_at,id) WHERE kind GLOB 'module.*' AND completed_at IS NOT NULL;

CREATE INDEX idx_module_receipt_kind ON account_operations(kind) WHERE completed_at IS NOT NULL AND purged_at IS NULL;

CREATE UNIQUE INDEX idx_notification_log_unique ON notification_log(vpn_key_id, sent_at);

CREATE INDEX idx_notification_log_vpn_key ON notification_log(vpn_key_id);

CREATE INDEX idx_page_routes_page_key ON page_routes(page_key);

CREATE INDEX idx_pages_page_kind ON pages(page_kind);

CREATE UNIQUE INDEX idx_panel_identity_rename_open ON panel_identity_renames(key_id) WHERE state != 'done';

CREATE INDEX idx_payment_auto_checks_due
        ON payment_auto_checks(state, next_check_at);

CREATE INDEX idx_payment_provider_orders_external ON payment_provider_orders(provider_id, provider_payment_id);

CREATE INDEX idx_payment_provider_orders_provider ON payment_provider_orders(provider_id, status);

CREATE INDEX idx_payments_extension_history
        ON payments(user_id, id DESC);

CREATE INDEX idx_payments_fulfillment ON payments(fulfillment_status, provider_confirmed_at);

CREATE INDEX idx_payments_key_status_paid_at ON payments(vpn_key_id, status, paid_at DESC);

CREATE INDEX idx_payments_order_id ON payments(order_id);

CREATE INDEX idx_payments_origin_pending ON payments(fulfillment_status, origin_extension_id) WHERE origin_extension_id IS NOT NULL;

CREATE INDEX idx_payments_origin_workflow ON payments(origin_extension_id, origin_workflow_id) WHERE origin_extension_id IS NOT NULL;

CREATE INDEX idx_payments_paid_at ON payments(paid_at);

CREATE INDEX idx_payments_promo_code_id ON payments(promo_code_id);

CREATE INDEX idx_payments_status_paid_at ON payments(status, paid_at);

CREATE INDEX idx_payments_user_id ON payments(user_id);

CREATE INDEX idx_promo_codes_expires ON promo_codes(expires_at);

CREATE UNIQUE INDEX idx_promo_codes_lapsed_coupon
        ON promo_codes(source) WHERE source LIKE 'auto_lapsed:%';

CREATE UNIQUE INDEX idx_promo_codes_payment_coupon ON promo_codes(source) WHERE source LIKE 'auto_payment:%';

CREATE INDEX idx_promo_codes_source ON promo_codes(source);

CREATE INDEX idx_promo_codes_type ON promo_codes(type, is_active);

CREATE INDEX idx_promo_link_visits_code ON promo_link_visits(promo_code_id, created_at);

CREATE INDEX idx_promo_link_visits_user ON promo_link_visits(user_id, created_at);

CREATE INDEX idx_promo_redemptions_code_status ON promo_redemptions(promo_code_id, status);

CREATE UNIQUE INDEX idx_promo_redemptions_order ON promo_redemptions(order_id) WHERE status != 'canceled';

CREATE INDEX idx_promo_redemptions_user ON promo_redemptions(user_id, created_at);

CREATE INDEX idx_semantic_action_contexts_order
        ON semantic_action_contexts(order_id)
        WHERE order_id IS NOT NULL
        ;

CREATE INDEX idx_semantic_action_contexts_user_state
        ON semantic_action_contexts(user_id, state, expires_at)
        ;

CREATE INDEX idx_server_groups_group ON server_groups(group_id);

CREATE INDEX idx_subscription_bindings_component
        ON subscription_bindings(component_key_id, host_key_id)
        ;

CREATE INDEX idx_subscription_composition_sync_due
        ON subscription_composition_sync(state, next_attempt_at, lease_until)
        ;

CREATE UNIQUE INDEX idx_support_admin_notifications_generated ON support_admin_notifications(support_message_id, admin_telegram_id) WHERE support_message_id IS NOT NULL;

CREATE INDEX idx_support_admin_notifications_thread ON support_admin_notifications(thread_id, is_active);

CREATE UNIQUE INDEX idx_support_messages_extension_operation ON support_messages(origin_extension_id, origin_operation_key) WHERE origin_type = 'extension';

CREATE INDEX idx_support_messages_thread ON support_messages(thread_id, created_at);

CREATE INDEX idx_support_messages_thread_id ON support_messages(thread_id, id);

CREATE INDEX idx_support_threads_activity ON support_threads(last_message_at DESC, id DESC);

CREATE INDEX idx_support_threads_assigned ON support_threads(assigned_admin_id, updated_at);

CREATE INDEX idx_support_threads_status_activity ON support_threads(status, last_message_at DESC, id DESC);

CREATE INDEX idx_support_threads_user ON support_threads(user_telegram_id, created_at);

CREATE UNIQUE INDEX idx_tariffs_admin_custom_group ON tariffs(group_id) WHERE system_type = 'admin_custom';

CREATE UNIQUE INDEX idx_trial_activations_legacy_user ON trial_activations(user_id) WHERE legacy_global_block = 1;

CREATE INDEX idx_trial_activations_offer ON trial_activations(offer_id);

CREATE UNIQUE INDEX idx_trial_activations_user_group ON trial_activations(user_id, group_id) WHERE legacy_global_block = 0 AND group_id IS NOT NULL;

CREATE UNIQUE INDEX idx_trial_offers_primary ON trial_offers(is_primary) WHERE is_primary = 1;

CREATE UNIQUE INDEX idx_users_account_name ON users(account_name) WHERE account_name IS NOT NULL AND account_name <> '';

CREATE INDEX idx_users_created_at ON users(created_at);

CREATE INDEX idx_users_is_bot_blocked ON users(is_bot_blocked);

CREATE INDEX idx_users_referral_code ON users(referral_code);

CREATE INDEX idx_users_referred_by ON users(referred_by);

CREATE INDEX idx_users_telegram_id ON users(telegram_id);

CREATE INDEX idx_users_username_lower ON users(LOWER(username));

CREATE INDEX idx_vpn_keys_expires_at ON vpn_keys(expires_at);

CREATE INDEX idx_vpn_keys_panel_email_lower ON vpn_keys(LOWER(panel_email));

CREATE INDEX idx_vpn_keys_server_email ON vpn_keys(server_id, panel_email);

CREATE INDEX idx_vpn_keys_server_id ON vpn_keys(server_id);

CREATE INDEX idx_vpn_keys_user_expires ON vpn_keys(user_id, expires_at DESC);

CREATE INDEX idx_vpn_keys_user_id ON vpn_keys(user_id);

CREATE TRIGGER servers_subscription_capability_after_update
        AFTER UPDATE OF is_active, panel_version ON servers
        WHEN OLD.is_active IS NOT NEW.is_active
          OR OLD.panel_version IS NOT NEW.panel_version
        BEGIN
            UPDATE subscription_composition_sync
            SET state = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN state
                    ELSE 'pending'
                END,
                desired_revision = desired_revision + 1,
                attempts = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN attempts
                    ELSE 0
                END,
                next_attempt_at = NULL,
                lease_until = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_until
                    ELSE NULL
                END,
                lease_owner_token = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_owner_token
                    ELSE NULL
                END,
                last_error_code = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE host_key_id IN (
                SELECT id FROM vpn_keys WHERE server_id = NEW.id
            );
        END;

CREATE TRIGGER subscription_bindings_after_delete
        AFTER DELETE ON subscription_bindings
        WHEN EXISTS (SELECT 1 FROM vpn_keys WHERE id = OLD.host_key_id)
        BEGIN
            INSERT INTO subscription_composition_sync (
                host_key_id, state, desired_revision, applied_revision,
                applied_tokens_json, attempts, next_attempt_at, lease_until,
                last_error_code, updated_at
            ) VALUES (
                OLD.host_key_id, 'pending', 1, 0, '[]', 0, NULL, NULL,
                NULL, CURRENT_TIMESTAMP
            )
            ON CONFLICT(host_key_id) DO UPDATE SET
                state = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN state
                    ELSE 'pending'
                END,
                desired_revision = desired_revision + 1,
                attempts = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN attempts
                    ELSE 0
                END,
                next_attempt_at = NULL,
                lease_until = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_until
                    ELSE NULL
                END,
                lease_owner_token = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_owner_token
                    ELSE NULL
                END,
                last_error_code = NULL,
                updated_at = CURRENT_TIMESTAMP;
        END;

CREATE TRIGGER subscription_bindings_after_insert
        AFTER INSERT ON subscription_bindings
        BEGIN
            INSERT INTO subscription_composition_sync (
                host_key_id, state, desired_revision, applied_revision,
                applied_tokens_json, attempts, next_attempt_at, lease_until,
                last_error_code, updated_at
            ) VALUES (
                NEW.host_key_id, 'pending', 1, 0, '[]', 0, NULL, NULL,
                NULL, CURRENT_TIMESTAMP
            )
            ON CONFLICT(host_key_id) DO UPDATE SET
                state = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN state
                    ELSE 'pending'
                END,
                desired_revision = desired_revision + 1,
                attempts = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN attempts
                    ELSE 0
                END,
                next_attempt_at = NULL,
                lease_until = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_until
                    ELSE NULL
                END,
                lease_owner_token = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_owner_token
                    ELSE NULL
                END,
                last_error_code = NULL,
                updated_at = CURRENT_TIMESTAMP;
        END;

CREATE TRIGGER trg_key_inactivity_insert
        AFTER INSERT ON vpn_keys WHEN ((COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) OR (COALESCE(NEW.traffic_limit, 0) > 0 AND COALESCE(NEW.traffic_used, 0) >= NEW.traffic_limit))
        BEGIN
            UPDATE vpn_keys SET inactive_since = CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) THEN NEW.expires_at ELSE CURRENT_TIMESTAMP END,
                inactive_event_token = CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) AND NOT EXISTS (SELECT 1 FROM key_lifecycle_event_log ev WHERE ev.vpn_key_id = NEW.id AND ev.event_name = 'key_expired' AND ev.event_token = NEW.expires_at) THEN NEW.expires_at ELSE 'inactive:' || lower(hex(randomblob(16))) END
            WHERE id = NEW.id;
        END;

CREATE TRIGGER trg_key_inactivity_update
        AFTER UPDATE OF expires_at, traffic_used, traffic_limit ON vpn_keys
        WHEN (NEW.expires_at IS NOT OLD.expires_at
            OR NEW.traffic_used IS NOT OLD.traffic_used
            OR NEW.traffic_limit IS NOT OLD.traffic_limit)
          AND ((((COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) OR (COALESCE(NEW.traffic_limit, 0) > 0 AND COALESCE(NEW.traffic_used, 0) >= NEW.traffic_limit)) AND OLD.inactive_since IS NULL)
            OR (NOT ((COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) OR (COALESCE(NEW.traffic_limit, 0) > 0 AND COALESCE(NEW.traffic_used, 0) >= NEW.traffic_limit)) AND OLD.inactive_since IS NOT NULL))
        BEGIN
            UPDATE vpn_keys SET inactive_since = CASE
                WHEN NOT ((COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) OR (COALESCE(NEW.traffic_limit, 0) > 0 AND COALESCE(NEW.traffic_used, 0) >= NEW.traffic_limit)) THEN NULL
                WHEN ((COALESCE(TRIM(CAST(OLD.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(OLD.expires_at) <= datetime('now'), 0)) OR (COALESCE(OLD.traffic_limit, 0) > 0 AND COALESCE(OLD.traffic_used, 0) >= OLD.traffic_limit))
                    THEN COALESCE(COALESCE(OLD.inactive_since, CASE WHEN (COALESCE(TRIM(CAST(OLD.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(OLD.expires_at) <= datetime('now'), 0)) THEN OLD.expires_at END), CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) THEN NEW.expires_at ELSE CURRENT_TIMESTAMP END)
                ELSE CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) THEN NEW.expires_at ELSE CURRENT_TIMESTAMP END END,
                inactive_event_token = CASE
                WHEN NOT ((COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) OR (COALESCE(NEW.traffic_limit, 0) > 0 AND COALESCE(NEW.traffic_used, 0) >= NEW.traffic_limit)) THEN NULL
                WHEN ((COALESCE(TRIM(CAST(OLD.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(OLD.expires_at) <= datetime('now'), 0)) OR (COALESCE(OLD.traffic_limit, 0) > 0 AND COALESCE(OLD.traffic_used, 0) >= OLD.traffic_limit))
                    THEN COALESCE(COALESCE(OLD.inactive_event_token, CASE WHEN (COALESCE(TRIM(CAST(OLD.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(OLD.expires_at) <= datetime('now'), 0)) THEN OLD.expires_at END), CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) AND NOT EXISTS (SELECT 1 FROM key_lifecycle_event_log ev WHERE ev.vpn_key_id = NEW.id AND ev.event_name = 'key_expired' AND ev.event_token = NEW.expires_at) THEN NEW.expires_at ELSE 'inactive:' || lower(hex(randomblob(16))) END)
                ELSE CASE WHEN (COALESCE(TRIM(CAST(NEW.expires_at AS TEXT)) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*', 0) AND COALESCE(datetime(NEW.expires_at) <= datetime('now'), 0)) AND NOT EXISTS (SELECT 1 FROM key_lifecycle_event_log ev WHERE ev.vpn_key_id = NEW.id AND ev.event_name = 'key_expired' AND ev.event_token = NEW.expires_at) THEN NEW.expires_at ELSE 'inactive:' || lower(hex(randomblob(16))) END END
            WHERE id = NEW.id;
        END;

CREATE TRIGGER trg_payments_prevent_v1_downgrade
        BEFORE UPDATE OF intent_version ON payments
        WHEN OLD.intent_version = 1 AND NEW.intent_version <> 1
        BEGIN
            SELECT RAISE(ABORT, 'Payment Intent v1 cannot be downgraded');
        END;

CREATE TRIGGER trg_payments_require_v1_insert
        BEFORE INSERT ON payments
        WHEN NEW.intent_version <> 1
        BEGIN
            SELECT RAISE(ABORT, 'new payments require Payment Intent v1');
        END;

CREATE TRIGGER trg_trial_offers_protect_primary_delete
        BEFORE DELETE ON trial_offers
        WHEN OLD.is_primary = 1
        BEGIN
            SELECT RAISE(ABORT, 'primary trial offer cannot be deleted');
        END;

CREATE TRIGGER trg_trial_offers_protect_primary_marker
        BEFORE UPDATE OF is_primary ON trial_offers
        WHEN NEW.is_primary <> OLD.is_primary
        BEGIN
            SELECT RAISE(ABORT, 'trial offer primary marker is immutable');
        END;

CREATE TRIGGER trg_trial_offers_reject_missing_insert
        BEFORE INSERT ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND NOT EXISTS (
                SELECT 1 FROM tariffs WHERE id = NEW.tariff_id
             )
        BEGIN
            SELECT RAISE(ABORT, 'trial tariff does not exist');
        END;

CREATE TRIGGER trg_trial_offers_reject_missing_update
        BEFORE UPDATE OF tariff_id ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND NOT EXISTS (
                SELECT 1 FROM tariffs WHERE id = NEW.tariff_id
             )
        BEGIN
            SELECT RAISE(ABORT, 'trial tariff does not exist');
        END;

CREATE TRIGGER trg_trial_offers_reject_system_insert
        BEFORE INSERT ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND EXISTS (
                SELECT 1 FROM tariffs
                WHERE id = NEW.tariff_id AND system_type IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'system tariff cannot be used by a trial offer');
        END;

CREATE TRIGGER trg_trial_offers_reject_system_update
        BEFORE UPDATE OF tariff_id ON trial_offers
        WHEN NEW.tariff_id IS NOT NULL
         AND EXISTS (
                SELECT 1 FROM tariffs
                WHERE id = NEW.tariff_id AND system_type IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'system tariff cannot be used by a trial offer');
        END;

CREATE TRIGGER trg_trial_offers_touch_updated_at
        AFTER UPDATE OF tariff_id, is_enabled ON trial_offers
        BEGIN
            UPDATE trial_offers
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = NEW.id;
        END;

CREATE TRIGGER trg_trial_usage_scope_insert
        BEFORE INSERT ON settings
        WHEN NEW.key = 'trial_usage_scope'
         AND NEW.value NOT IN ('once_per_user', 'once_per_group')
        BEGIN
            SELECT RAISE(ABORT, 'invalid trial usage scope');
        END;

CREATE TRIGGER trg_trial_usage_scope_update
        BEFORE UPDATE OF value ON settings
        WHEN NEW.key = 'trial_usage_scope'
         AND NEW.value NOT IN ('once_per_user', 'once_per_group')
        BEGIN
            SELECT RAISE(ABORT, 'invalid trial usage scope');
        END;

CREATE TRIGGER vpn_keys_subscription_identity_after_update
        AFTER UPDATE OF server_id, panel_email, sub_id ON vpn_keys
        WHEN OLD.server_id IS NOT NEW.server_id
          OR OLD.panel_email IS NOT NEW.panel_email
          OR OLD.sub_id IS NOT NEW.sub_id
        BEGIN
            INSERT INTO subscription_composition_sync (
                host_key_id, state, desired_revision, applied_revision,
                applied_tokens_json, attempts, next_attempt_at, lease_until,
                last_error_code, updated_at
            )
            SELECT NEW.id, 'pending', 1, 0, '[]', 0, NULL, NULL,
                   NULL, CURRENT_TIMESTAMP
            WHERE EXISTS (
                SELECT 1 FROM subscription_bindings
                WHERE host_key_id = NEW.id
            )
            ON CONFLICT(host_key_id) DO UPDATE SET
                state = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN state
                    ELSE 'pending'
                END,
                desired_revision = desired_revision + 1,
                attempts = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN attempts
                    ELSE 0
                END,
                next_attempt_at = NULL,
                lease_until = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_until
                    ELSE NULL
                END,
                lease_owner_token = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_owner_token
                    ELSE NULL
                END,
                last_error_code = NULL,
                updated_at = CURRENT_TIMESTAMP;

            INSERT INTO subscription_composition_sync (
                host_key_id, state, desired_revision, applied_revision,
                applied_tokens_json, attempts, next_attempt_at, lease_until,
                last_error_code, updated_at
            )
            SELECT DISTINCT b.host_key_id, 'pending', 1, 0, '[]', 0,
                   NULL, NULL, NULL, CURRENT_TIMESTAMP
            FROM subscription_bindings AS b
            WHERE b.component_key_id = NEW.id
            ON CONFLICT(host_key_id) DO UPDATE SET
                state = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN state
                    ELSE 'pending'
                END,
                desired_revision = desired_revision + 1,
                attempts = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN attempts
                    ELSE 0
                END,
                next_attempt_at = NULL,
                lease_until = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_until
                    ELSE NULL
                END,
                lease_owner_token = CASE
                    WHEN lease_until > CURRENT_TIMESTAMP THEN lease_owner_token
                    ELSE NULL
                END,
                last_error_code = NULL,
                updated_at = CURRENT_TIMESTAMP;
        END;
"""


_BASELINE_SETTINGS_V134 = (('account_name_pool', ''),
 ('base_currency', 'RUB'),
 ('broadcast_config_revision', '0'),
 ('broadcast_filter', '[]'),
 ('broadcast_filter_contract_version', '2'),
 ('broadcast_in_progress', '0'),
 ('broadcast_style_profile',
  '{"schema_version":1,"tone":"friendly_professional","address":"polite_you","emoji_level":"medium","length":"compact","headline":"emoji_bold","paragraphs":"short","cta":"direct_calm","use_lists":true,"custom_instructions":""}'),
 ('cardlink_api_token', ''),
 ('cardlink_enabled', '0'),
 ('cardlink_shop_id', ''),
 ('cards_currency', 'RUB'),
 ('cards_enabled', '0'),
 ('cards_provider_token', ''),
 ('cards_yookassa_receipt', '1'),
 ('coupon_auto_discount_percent', '10'),
 ('coupon_auto_enabled', '0'),
 ('coupon_auto_lifetime_days', '90'),
 ('coupon_lapsed_delay_days', '7'),
 ('coupon_lapsed_discount_percent', '10'),
 ('coupon_lapsed_enabled', '0'),
 ('coupon_lapsed_enabled_since', ''),
 ('coupon_lapsed_lifetime_days', '90'),
 ('crypto_enabled', '0'),
 ('crypto_item_url', ''),
 ('crypto_secret_key', ''),
 ('cryptobot_api_token', ''),
 ('cryptobot_enabled', '0'),
 ('custom_extensions_enabled', '0'),
 ('custom_payment_webhooks_enabled', '0'),
 ('custom_payment_webhooks_host', '127.0.0.1'),
 ('custom_payment_webhooks_path_prefix', '/custom-payment-webhook'),
 ('custom_payment_webhooks_port', '8088'),
 ('daily_tasks_time', '03:00'),
 ('demo_payment_enabled', '0'),
 ('device_limit_mode', 'ip'),
 ('display_timezone', 'Europe/Moscow'),
 ('expired_key_deletion_notifications_enabled', '1'),
 ('expired_key_panel_cleanup_delay_days', '0'),
 ('expired_key_retention_days', '30'),
 ('key_name_prefix', ''),
 ('my_keys_item_template', ''),
 ('myfatoorah_api_token', ''),
 ('myfatoorah_currency', ''),
 ('myfatoorah_enabled', '0'),
 ('myfatoorah_region', ''),
 ('notification_days', '3'),
 ('notification_text', ''),
 ('nowpayments_api_key', ''),
 ('nowpayments_email', ''),
 ('nowpayments_enabled', '0'),
 ('nowpayments_password', ''),
 ('payment_admin_visible_cardlink', '1'),
 ('payment_admin_visible_cards', '1'),
 ('payment_admin_visible_crypto', '0'),
 ('payment_admin_visible_cryptobot', '1'),
 ('payment_admin_visible_demo', '1'),
 ('payment_admin_visible_myfatoorah', '0'),
 ('payment_admin_visible_nowpayments', '0'),
 ('payment_admin_visible_paypal', '0'),
 ('payment_admin_visible_paytabs', '0'),
 ('payment_admin_visible_platega', '1'),
 ('payment_admin_visible_stars', '1'),
 ('payment_admin_visible_stripe', '0'),
 ('payment_admin_visible_wata', '1'),
 ('payment_admin_visible_yookassa_qr', '1'),
 ('paypal_client_id', ''),
 ('paypal_client_secret', ''),
 ('paypal_enabled', '0'),
 ('paypal_environment', 'live'),
 ('paytabs_enabled', '0'),
 ('paytabs_profile_id', ''),
 ('paytabs_region', ''),
 ('paytabs_server_key', ''),
 ('platega_enabled', '0'),
 ('platega_merchant_id', ''),
 ('platega_secret', ''),
 ('referral_attribution_window_hours', '0'),
 ('referral_enabled', '0'),
 ('referral_new_ref_notification_text', ''),
 ('referral_new_ref_notifications_enabled', '0'),
 ('referral_notification_levels', '1'),
 ('referral_purchase_notification_text', ''),
 ('referral_purchase_notifications_enabled', '0'),
 ('referral_reward_type', 'days'),
 ('stablecoin_rub_rate', '95'),
 ('star_rub_rate', '1.235'),
 ('stars_enabled', '0'),
 ('stripe_enabled', '0'),
 ('stripe_secret_key', ''),
 ('support_claim_cleanup_mode', 'remove_button'),
 ('telegram_link_domain', 't.me'),
 ('traffic_notification_text', ''),
 ('trial_usage_scope', 'once_per_user'),
 ('update_blocked', '0'),
 ('update_check_time', '12:00'),
 ('update_notifications_enabled', '1'),
 ('usd_rub_rate', '9500'),
 ('wata_enabled', '0'),
 ('wata_jwt_token', ''),
 ('web_bot_id', ''),
 ('web_bot_username', ''),
 ('web_enabled', '0'),
 ('web_listen_port', '18764'),
 ('web_public_origin', ''),
 ('web_telegram_login_enabled', '0'),
 ('web_ui_preset', 'clear'),
 ('web_ui_sync_interval_seconds', '300'),
 ('web_ui_theme', 'light'),
 ('web_verification_enabled', '0'),
 ('web_verification_method', 'ucaller'),
 ('web_verification_smsaero_api_key', ''),
 ('web_verification_smsaero_email', ''),
 ('web_verification_smsaero_mobile_sign', ''),
 ('web_verification_smsaero_sms_sign', ''),
 ('web_verification_ucaller_secret_key', ''),
 ('web_verification_ucaller_service_id', ''),
 ('yadreno_admin_core_changes_enabled', '0'),
 ('yookassa_qr_enabled', '0'),
 ('yookassa_secret_key', ''),
 ('yookassa_shop_id', ''))


_BASELINE_PAGE_BUTTONS_V134 = (('access_blocked',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('action_unavailable',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('balance_insufficient',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('balance_topup_amount',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('balance_topup_amount_invalid',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('balance_topup_result',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('custom_profile',
  '[{"id": "btn_profile_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_profile_buy", "label": "", '
  '"color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_buy"}, {"id": "btn_profile_referral", "label": "", "color": "secondary", "row": 1, "col": 0, '
  '"is_hidden": false, "action_type": "internal", "action_value": "cmd_referral"}, {"id": '
  '"btn_profile_show_id", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_show_id"}, {"id": "btn_profile_help", "label": "", '
  '"color": "secondary", "row": 2, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_help"}, {"id": "btn_profile_back_main", "label": "", "color": "secondary", "row": 3, "col": 0, '
  '"is_hidden": false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('demo_payment',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('expired_keys_deleted',
  '[{"id": "btn_buy_key", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_buy"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('expiry_notification_actions',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('help',
  '[{"id": "btn_news", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "url", "action_value": "https://%telegram_link_domain%/plushkin_blog"}, {"id": '
  '"btn_support", "label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": '
  '"url", "action_value": "https://%telegram_link_domain%/plushkin_chat"}, {"id": "btn_back_main", "label": '
  '"", "color": "secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", '
  '"action_value": "cmd_back_main"}]'),
 ('key_delivery',
  '[{"id": "btn_help", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_help"}, {"id": "btn_my_keys", "label": "", "color": '
  '"secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 0, '
  '"is_hidden": false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('key_delivery_failed',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_delivery_partial',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_details',
  '[{"id": "btn_key_show_subscription", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}, {"id": "btn_key_configure", "label": "", "color": '
  '"secondary", "row": 0, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_key_renew", "label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_key_replace", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_key_delete", "label": "", "color": "secondary", "row": 1, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_key_rename", "label": "", "color": '
  '"secondary", "row": 1, "col": 1, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 3, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 3, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}, {"id": "btn_key_devices", "label": "", "color": "secondary", "row": 2, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}]'),
 ('key_devices',
  '[{"id": "btn_key_device_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": '
  'false, "action_type": "system_collection", "action_value": null}, {"id": "btn_key_devices_back", "label": '
  '"", "color": "secondary", "row": 100, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_back_main", "label": "", "color": "secondary", "row": 100, "col": 1, '
  '"is_hidden": false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('key_not_found',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_operation_failed',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_operation_unavailable',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_progress',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_rename_invalid',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_rename_prompt',
  '[{"id": "btn_key_flow_back", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}]'),
 ('key_renewed',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_replace_confirm',
  '[{"id": "btn_key_flow_confirm", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}, {"id": "btn_key_flow_back", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}]'),
 ('key_replace_server_select',
  '[{"id": "btn_server_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_key_flow_back", "label": "", '
  '"color": "secondary", "row": 1000, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}]'),
 ('key_replace_server_unavailable',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('key_show_unconfigured',
  '[{"id": "btn_help", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_help"}, {"id": "btn_my_keys", "label": "", "color": '
  '"secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 0, '
  '"is_hidden": false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('key_status',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('key_subscription_host_select',
  '[{"id": "btn_subscription_host_items", "label": "", "color": "secondary", "row": 0, "col": 0, '
  '"is_hidden": false, "action_type": "system_collection", "action_value": null}, {"id": '
  '"btn_subscription_host_skip", "label": "", "color": "secondary", "row": 1000, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}]'),
 ('lapsed_key_coupon',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('main',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_buy_key", "label": "", "color": '
  '"secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_buy"}, {"id": "btn_trial", "label": "", "color": "secondary", "row": 1, "col": 0, "is_hidden": true, '
  '"action_type": "internal", "action_value": "cmd_trial"}, {"id": "btn_referral", "label": "", "color": '
  '"secondary", "row": 2, "col": 0, "is_hidden": true, "action_type": "internal", "action_value": '
  '"cmd_referral"}, {"id": "btn_help", "label": "", "color": "secondary", "row": 2, "col": 1, "is_hidden": '
  'false, "action_type": "internal", "action_value": "cmd_help"}, {"id": "btn_support", "label": "", '
  '"color": "secondary", "row": 3, "col": 0, "is_hidden": true, "action_type": "internal", "action_value": '
  '"cmd_support"}, {"id": "open_web_cabinet", "label": "", "color": "secondary", "row": 4, "col": 0, '
  '"is_hidden": true, "action_type": "web_app", "action_value": "%web_app_url%"}]'),
 ('my_keys',
  '[{"id": "btn_key_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1000, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('my_keys_empty',
  '[{"id": "btn_buy_key", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_buy"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('my_keys_key_deleted',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('new_key_no_servers',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('new_key_server_select',
  '[{"id": "btn_server_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1000, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('payment_auto_completed',
  '[{"id": "btn_my_keys", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_my_keys"}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('payment_canceled',
  '[{"id": "btn_intent_check", "label": "", "row": 0, "col": 0, "action_type": "system", "action_value": '
  'null, "color": "secondary", "is_hidden": false}, {"id": "btn_intent_methods", "label": "", "row": 1, '
  '"col": 0, "action_type": "system", "action_value": null, "color": "secondary", "is_hidden": false}, '
  '{"id": "btn_intent_cancel", "label": "", "row": 2, "col": 0, "action_type": "system", "action_value": '
  'null, "color": "secondary", "is_hidden": false}, {"id": "btn_back_main", "label": "", "row": 3, "col": 0, '
  '"action_type": "internal", "action_value": "cmd_back_main", "color": "secondary", "is_hidden": false}]'),
 ('payment_check_wait',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_completed', '[]'),
 ('payment_coupon_message', '[]'),
 ('payment_creating',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_failed',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_link_renewal',
  '[{"id": "btn_intent_open", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_check", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 3, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_link_topup',
  '[{"id": "btn_intent_open", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_check", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 3, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_method_select',
  '[{"id": "btn_intent_provider_cryptobot", "label": "", "color": "secondary", "row": 0, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_crypto", '
  '"label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_stars", "label": "", "color": "secondary", "row": 1, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_cards", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_yookassa_qr", "label": "", '
  '"color": "secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_wata", "label": "", "color": "secondary", "row": 4, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_platega", '
  '"label": "", "color": "secondary", "row": 5, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_cardlink", "label": "", "color": "secondary", "row": '
  '6, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_demo", "label": "", "color": "secondary", "row": 7, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_balance", "label": "", "color": '
  '"secondary", "row": 11, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_cancel", "label": "", "color": "secondary", "row": 12, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 12, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}, {"id": "btn_intent_provider_nowpayments", "label": "", "color": "secondary", "row": 8, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_stripe", "label": "", "color": "secondary", "row": 8, "col": 1, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_paypal", "label": "", '
  '"color": "secondary", "row": 9, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_paytabs", "label": "", "color": "secondary", "row": 9, "col": 1, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_myfatoorah", "label": "", "color": "secondary", "row": 10, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}]'),
 ('payment_method_select_renewal',
  '[{"id": "btn_intent_provider_cryptobot", "label": "", "color": "secondary", "row": 0, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_crypto", '
  '"label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_stars", "label": "", "color": "secondary", "row": 1, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_cards", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_yookassa_qr", "label": "", '
  '"color": "secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_wata", "label": "", "color": "secondary", "row": 4, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_platega", '
  '"label": "", "color": "secondary", "row": 5, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_cardlink", "label": "", "color": "secondary", "row": '
  '6, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_demo", "label": "", "color": "secondary", "row": 7, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_balance", "label": "", "color": '
  '"secondary", "row": 11, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_cancel", "label": "", "color": "secondary", "row": 12, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 12, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}, {"id": "btn_intent_provider_nowpayments", "label": "", "color": "secondary", "row": 8, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_stripe", "label": "", "color": "secondary", "row": 8, "col": 1, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_paypal", "label": "", '
  '"color": "secondary", "row": 9, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_paytabs", "label": "", "color": "secondary", "row": 9, "col": 1, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_myfatoorah", "label": "", "color": "secondary", "row": 10, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}]'),
 ('payment_method_select_surcharge',
  '[{"id": "btn_intent_provider_cryptobot", "label": "", "color": "secondary", "row": 0, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_crypto", '
  '"label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_stars", "label": "", "color": "secondary", "row": 1, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_cards", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_yookassa_qr", "label": "", '
  '"color": "secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_wata", "label": "", "color": "secondary", "row": 4, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_platega", '
  '"label": "", "color": "secondary", "row": 5, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_cardlink", "label": "", "color": "secondary", "row": '
  '6, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_demo", "label": "", "color": "secondary", "row": 7, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_balance", "label": "", "color": '
  '"secondary", "row": 11, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_cancel", "label": "", "color": "secondary", "row": 12, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 12, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}, {"id": "btn_intent_provider_nowpayments", "label": "", "color": "secondary", "row": 8, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_stripe", "label": "", "color": "secondary", "row": 8, "col": 1, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_paypal", "label": "", '
  '"color": "secondary", "row": 9, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_paytabs", "label": "", "color": "secondary", "row": 9, "col": 1, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_myfatoorah", "label": "", "color": "secondary", "row": 10, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}]'),
 ('payment_method_select_topup',
  '[{"id": "btn_intent_provider_cryptobot", "label": "", "color": "secondary", "row": 0, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_crypto", '
  '"label": "", "color": "secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_stars", "label": "", "color": "secondary", "row": 1, '
  '"col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_cards", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_yookassa_qr", "label": "", '
  '"color": "secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_wata", "label": "", "color": "secondary", "row": 4, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}, {"id": "btn_intent_provider_platega", '
  '"label": "", "color": "secondary", "row": 5, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_cardlink", "label": "", "color": "secondary", "row": '
  '6, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_demo", "label": "", "color": "secondary", "row": 7, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_balance", "label": "", "color": '
  '"secondary", "row": 11, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_promo", "label": "", "color": "secondary", "row": 12, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 13, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 13, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}, {"id": "btn_intent_provider_nowpayments", '
  '"label": "", "color": "secondary", "row": 8, "col": 0, "is_hidden": false, "action_type": "system", '
  '"action_value": null}, {"id": "btn_intent_provider_stripe", "label": "", "color": "secondary", "row": 8, '
  '"col": 1, "is_hidden": false, "action_type": "system", "action_value": null}, {"id": '
  '"btn_intent_provider_paypal", "label": "", "color": "secondary", "row": 9, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_provider_paytabs", "label": "", '
  '"color": "secondary", "row": 9, "col": 1, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_intent_provider_myfatoorah", "label": "", "color": "secondary", "row": 10, "col": 0, '
  '"is_hidden": false, "action_type": "system", "action_value": null}]'),
 ('payment_minimum_unavailable',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_order_unavailable',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_pending',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_tariff_select',
  '[{"id": "btn_tariff_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_tariff_back", "label": "", '
  '"color": "secondary", "row": 1000, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_back_main", "label": "", "color": "secondary", "row": 1000, "col": 1, "is_hidden": '
  'false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('payment_unavailable',
  '[{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 1, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('prepayment',
  '[{"id": "btn_tariff_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_enter_promo", "label": "", '
  '"color": "secondary", "row": 999, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_back_main", "label": "", "color": "secondary", "row": 1000, "col": 0, "is_hidden": '
  'false, "action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('prepayment_unavailable',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('promo_applied',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_enter',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_exhausted',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_expired',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_inactive',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_invalid',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_link_saved',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_not_found',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('promo_status', '[]'),
 ('promo_unavailable',
  '[{"id": "btn_promo_return", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('qr_payment',
  '[{"id": "btn_intent_open", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_check", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_intent_methods", "label": "", "color": "secondary", "row": 2, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_intent_cancel", "label": "", "color": '
  '"secondary", "row": 3, "col": 0, "is_hidden": false, "action_type": "system", "action_value": null}, '
  '{"id": "btn_back_main", "label": "", "color": "secondary", "row": 3, "col": 1, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('referral',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('renew_payment',
  '[{"id": "btn_tariff_items", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system_collection", "action_value": null}, {"id": "btn_renew_enter_promo", "label": "", '
  '"color": "secondary", "row": 999, "col": 0, "is_hidden": false, "action_type": "system", "action_value": '
  'null}, {"id": "btn_tariff_back", "label": "", "color": "secondary", "row": 1000, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1000, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('renew_payment_unavailable',
  '[{"id": "btn_renew_back", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 0, "col": 1, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('screen_unavailable',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('show_id',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_admin_notification',
  '[{"id": "btn_admin_support_reply", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": '
  'false, "action_type": "system", "action_value": null}]'),
 ('support_failed',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_format_unsupported',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_reply',
  '[{"id": "btn_support_reply", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('support_reply_start',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_sent',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_start',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_status',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('support_thread_unavailable',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'),
 ('trial',
  '[{"id": "btn_activate_trial", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "system", "action_value": null}, {"id": "btn_back_main", "label": "", "color": '
  '"secondary", "row": 1, "col": 0, "is_hidden": false, "action_type": "internal", "action_value": '
  '"cmd_back_main"}]'),
 ('trial_already_used',
  '[{"id": "btn_back_main", "label": "", "color": "secondary", "row": 0, "col": 0, "is_hidden": false, '
  '"action_type": "internal", "action_value": "cmd_back_main"}]'))


_BASELINE_COMPACT_BUTTON_PAGES_V134 = ('payment_method_select',
 'payment_method_select_renewal',
 'payment_method_select_surcharge',
 'payment_method_select_topup')


_BASELINE_UPDATED_PAGES_V134 = ('balance_topup_result',
 'expired_keys_deleted',
 'key_delivery',
 'key_delivery_failed',
 'key_delivery_partial',
 'key_details',
 'key_devices',
 'key_operation_failed',
 'key_operation_unavailable',
 'key_progress',
 'key_renewed',
 'key_replace_server_unavailable',
 'key_subscription_host_select',
 'new_key_no_servers',
 'new_key_server_select',
 'payment_auto_completed',
 'payment_method_select',
 'payment_method_select_renewal',
 'payment_method_select_surcharge',
 'payment_method_select_topup',
 'prepayment',
 'renew_payment',
 'trial')


_BASELINE_USER_UI_TEXT_KEYS_V134 = ('account.link.button.cancel',
 'account.link.button.confirm',
 'account.link.cancelled',
 'account.link.confirmed',
 'account.link.conflict',
 'account.link.invalid',
 'account.link.merge_prompt',
 'account.link.prompt',
 'account.link.unavailable',
 'account.merge.balance_reason',
 'balance.reason.payment',
 'command.help',
 'command.mykeys',
 'command.start',
 'format.days_short',
 'format.duration_unlimited',
 'format.time_left',
 'format.traffic_gb',
 'interface.action_dispatcher.apply_extension_callback_result.1',
 'interface.action_dispatcher.apply_extension_callback_result.2',
 'interface.action_dispatcher.dispatch_core_action.1',
 'interface.action_dispatcher.dispatch_core_action.2',
 'interface.action_dispatcher.dispatch_core_action.3',
 'interface.action_dispatcher.redirect_to_extension_action.1',
 'interface.page_flow.not_banned_guard.1',
 'interface.page_flow.referral_enabled_guard.1',
 'interface.page_flow.run_page_guards.1',
 'interface.policy_registry.apply_pricing_policies.1',
 'key.deleted_list.item',
 'key.deleted_list.more',
 'key.devices.delete_error',
 'key.devices.deleted',
 'key.devices.empty',
 'key.devices.item',
 'key.devices.load_error',
 'key.devices.unknown',
 'key.history.operation',
 'key.history.operation_with_days',
 'key.history.payment',
 'key.history.promo_suffix',
 'key.name.default',
 'key.name.draft',
 'key.status.active',
 'key.status.expired',
 'key.status.traffic_exhausted',
 'key.tariff.custom',
 'key.traffic.limited',
 'key.traffic.needs_setup',
 'key.traffic.unlimited',
 'key.traffic.used_unlimited',
 'navigation.cancel',
 'navigation.home',
 'payment.invoice.change_method_button',
 'payment.invoice.pay_button',
 'payment.invoice.purchase_description',
 'payment.invoice.renewal_description',
 'payment.invoice.stale_error',
 'payment.invoice.topup_description',
 'payment.quote.price_line',
 'payment.quote.promo_line',
 'promo.auto_coupon',
 'referral.balance_line',
 'referral.level_row',
 'referral.no_levels',
 'reward.reason.promotion',
 'support.button.contact',
 'support.button.reply',
 'tariff.missing',
 'tariff.price_unset',
 'trial.eligibility.once_per_group',
 'trial.eligibility.once_per_user',
 'trial.offer.summary')


_BASELINE_TARIFF_GROUPS_V134 = ((1, '', 1, 0, None),)


_BASELINE_TARIFFS_V134 = ((1, '', 0, 999, 0, 0, 1, 1, 'admin_custom', 0),)


_BASELINE_REFERRAL_LEVELS_V134 = ((1, 1, 10, 1), (2, 2, 5, 0), (3, 3, 2, 0))


_BASELINE_CURRENCY_RATES_V134 = (('RUB', 'USDT', '0.01052631578947368421052631579'), ('RUB', 'XTR', '0.8097165991902834008097165992'))


_BASELINE_TRIAL_OFFERS_V134 = ((1, None, 1, 0),)


_BASELINE_PAGE_ROUTES_V134 = (('balance_topup_result', 'balance_topup_result', '["not_banned"]', '[]', 1),
 ('profile', 'custom_profile', '["not_banned"]', '[]', 1))


_BASELINE_SEQUENCES_V134 = (('payment_provider_orders', 0),
 ('payments', 0),
 ('referral_levels', 3),
 ('servers', 0),
 ('support_messages', 0),
 ('support_threads', 0),
 ('tariff_groups', 1),
 ('tariffs', 1),
 ('trial_offers', 1),
 ('users', 0),
 ('vpn_keys', 0))


def migration_initial(conn: sqlite3.Connection) -> None:
    """Create the complete localized v134 state in the caller's transaction."""
    from .db_language import initial_language
    from core.payment_provider_specs import payment_setting_defaults

    language = initial_language()
    texts = catalog('seed', language)
    logger.info("Создание БД (сжатая базовая схема v134)...")

    # executescript commits any prior transaction. Start inside the script so
    # schema, localized defaults, identity and the caller's version roll back together.
    conn.executescript("BEGIN IMMEDIATE;\n" + _BASELINE_SCHEMA_V134)
    provider_defaults = payment_setting_defaults(language)
    settings = dict(_BASELINE_SETTINGS_V134)
    for key, value in settings.items():
        settings[key] = texts.get('setting.' + key, provider_defaults.get(key, value))
    settings['installation_language'] = language
    settings['account_name_pool'] = json.dumps(
        [value for key, value in texts.items() if key.startswith('account_name.')], ensure_ascii=False,
    )
    conn.executemany('INSERT INTO settings(key,value) VALUES (?,?)', settings.items())
    _ensure_yadreno_admin_application_id(conn)
    conn.executemany(
        'INSERT INTO tariff_groups(id,name,sort_order,monthly_traffic_reset_enabled,subscription_parent_group_id) VALUES (?,?,?,?,?)',
        (tuple(seed_text('tariff_groups.' + str(row[0]) + '.name', language) if index == 1 else value
                   for index, value in enumerate(row)) for row in _BASELINE_TARIFF_GROUPS_V134),
    )
    conn.executemany(
        'INSERT INTO tariffs(id,name,duration_days,display_order,is_active,traffic_limit_gb,group_id,max_ips,system_type,price_minor) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (tuple(seed_text('tariffs.' + str(row[0]) + '.name', language) if index == 1 else value
                   for index, value in enumerate(row)) for row in _BASELINE_TARIFFS_V134),
    )
    conn.executemany(
        'INSERT INTO referral_levels(id,level_number,percent,enabled) VALUES (?,?,?,?)',
        _BASELINE_REFERRAL_LEVELS_V134,
    )
    conn.executemany(
        'INSERT INTO currency_rates(base_currency,target_currency,units_per_base) VALUES (?,?,?)',
        _BASELINE_CURRENCY_RATES_V134,
    )
    conn.executemany(
        'INSERT INTO trial_offers(id,tariff_id,is_primary,is_enabled) VALUES (?,?,?,?)',
        _BASELINE_TRIAL_OFFERS_V134,
    )
    for page_key, button_template in _BASELINE_PAGE_BUTTONS_V134:
        buttons = json.loads(button_template)
        for button in buttons:
            if 'label' in button:
                button['label'] = seed_text(f"page.{page_key}.button.{button['id']}", language)
        separators = (',', ':') if page_key in _BASELINE_COMPACT_BUTTON_PAGES_V134 else (', ', ': ')
        conn.execute(
            "INSERT INTO pages(page_key,text_default,buttons_default,page_kind) VALUES (?,?,?,'core')",
            (page_key, seed_text('page.' + page_key + '.text', language),
             json.dumps(buttons, ensure_ascii=False, separators=separators)),
        )
    conn.executemany('UPDATE pages SET updated_at=CURRENT_TIMESTAMP WHERE page_key=?',
                     ((key,) for key in _BASELINE_UPDATED_PAGES_V134))
    conn.executemany(
        'INSERT INTO page_routes(route_key,page_key,guard_names,hook_names,is_enabled) VALUES (?,?,?,?,?)',
        _BASELINE_PAGE_ROUTES_V134,
    )
    update_user_ui_text_defaults(
        (replace(item, text_default=seed_text('ui.' + item.text_key, language))
         for item in USER_UI_TEXT_DEFINITIONS if item.text_key in _BASELINE_USER_UI_TEXT_KEYS_V134),
        conn=conn,
    )
    conn.execute('DELETE FROM sqlite_sequence')
    conn.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)', _BASELINE_SEQUENCES_V134)
    logger.info("БД создана (сжатая базовая схема v134)")


# Future schema changes start at version 135. Never renumber persisted versions.
MIGRATIONS = {}


def _assert_migration_database_integrity(
    conn: sqlite3.Connection,
    *,
    stage: str,
) -> None:
    """Fail a migration boundary on structural or foreign-key corruption."""
    quick_rows = conn.execute('PRAGMA quick_check').fetchall()
    if len(quick_rows) != 1 or quick_rows[0][0] != 'ok':
        raise RuntimeError(
            f"quick_check failed {stage}: {quick_rows[:5]}"
        )
    foreign_key_rows = conn.execute('PRAGMA foreign_key_check').fetchall()
    if foreign_key_rows:
        raise RuntimeError(
            f"foreign_key_check failed {stage}: {foreign_key_rows[:5]}"
        )


def run_migrations() -> None:
    """
    Runs all necessary migrations.
    
    Logic:
    - version = 0 (new install): calls migration_initial → sets INITIAL_VERSION → applies incremental migrations up to LATEST_VERSION
    - version = LATEST_VERSION: does nothing
    - version < INITIAL_VERSION: error (need to update via intermediate version)
    - version >= INITIAL_VERSION: applies incremental migrations from MIGRATIONS
    """
    try:
        current = get_current_version()
        
        if current >= LATEST_VERSION:
            logger.info(f"✅ БД соответствует версии {LATEST_VERSION}. Миграция не требуется.")
            return
        
        # Protection: Database on an intermediate version that cannot be updated with compressed migrations
        if 0 < current < INITIAL_VERSION:
            raise RuntimeError(
                f"Версия БД ({current}) ниже минимально поддерживаемой ({INITIAL_VERSION}). "
                f"Сначала обновите бот до промежуточной версии, чтобы БД мигрировала до v{INITIAL_VERSION}."
            )

        with get_db() as validation_conn:
            _assert_migration_database_integrity(
                validation_conn,
                stage="before migrations",
            )
        
        logger.info(f"🔄 Требуется миграция БД с версии {current} до {LATEST_VERSION}")
        
        with get_db() as conn:
            # New installation - creating a database from scratch
            if current == 0:
                migration_initial(conn)
                set_version(conn, INITIAL_VERSION)
                # Commit the complete baseline before any future incremental step.
                conn.commit()
                current = INITIAL_VERSION
            
            # Incremental migrations after the compressed baseline.
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)

        with get_db() as validation_conn:
            _assert_migration_database_integrity(
                validation_conn,
                stage="after migrations",
            )
            version_row = validation_conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            final_version = int(version_row[0]) if version_row else 0
            if final_version != LATEST_VERSION:
                raise RuntimeError(
                    f"schema version mismatch after migrations: "
                    f"expected {LATEST_VERSION}, got {final_version}"
                )
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        from .db_language import refresh_installation_language
        refresh_installation_language()
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
