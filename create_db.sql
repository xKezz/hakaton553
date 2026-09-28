-- =========================================================
-- WINBACK MVP
-- Финальная структура базы данных
-- =========================================================


-- =========================================================
-- 1. CLIENT
-- Пользователи MAX, зарегистрированные в программе
-- =========================================================

CREATE TABLE client (
    id              BIGSERIAL PRIMARY KEY,

    phone_e164      VARCHAR(20) NOT NULL UNIQUE,

    max_user_id     VARCHAR(100) UNIQUE,

    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- =========================================================
-- 2. PURCHASE
-- Полная накопленная история покупок
-- =========================================================

CREATE TABLE purchase (
    id              BIGSERIAL PRIMARY KEY,

    purchase_id     VARCHAR(255) NOT NULL UNIQUE,

    client_id       BIGINT NOT NULL,

    purchase_date   TIMESTAMP NOT NULL,

    amount          NUMERIC(12, 2) NOT NULL
                    CHECK (amount >= 0),

    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_purchase_client
        FOREIGN KEY (client_id)
        REFERENCES client(id)
        ON DELETE CASCADE
);


-- =========================================================
-- 3. CAMPAIGN
-- Запущенная маркетинговая кампания
-- =========================================================

CREATE TABLE campaign (
    id                  BIGSERIAL PRIMARY KEY,

    status              VARCHAR(20) NOT NULL DEFAULT 'PROCESSING',

    source_file_path    TEXT,

    total_clients       INTEGER NOT NULL DEFAULT 0
                        CHECK (total_clients >= 0),

    at_risk_clients     INTEGER NOT NULL DEFAULT 0
                        CHECK (at_risk_clients >= 0),

    config              JSON,

    launched_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    campaign_ends_at    TIMESTAMP NOT NULL,

    CONSTRAINT campaign_status_check
        CHECK (
            status IN (
                'PROCESSING',
                'DRAFT',
                'APPROVED',
                'COMPLETED',
                'FAILED'
            )
        )
);


-- =========================================================
-- 4. CAMPAIGN_CATEGORY
-- 5 маркетинговых категорий конкретной кампании
-- =========================================================

CREATE TABLE campaign_category (
    id                  BIGSERIAL PRIMARY KEY,

    campaign_id         BIGINT NOT NULL,

    segment_group       VARCHAR(100) NOT NULL,

    label               VARCHAR(255) NOT NULL,

    clients_count       INTEGER NOT NULL DEFAULT 0
                        CHECK (clients_count >= 0),

    avg_recency         NUMERIC(12, 2),

    avg_frequency       NUMERIC(12, 2),

    avg_monetary_score  NUMERIC(12, 2),

    avg_amount          NUMERIC(12, 2),

    proposed_bonus      INTEGER NOT NULL DEFAULT 0
                        CHECK (proposed_bonus >= 0),

    final_bonus         INTEGER NOT NULL DEFAULT 0
                        CHECK (final_bonus >= 0),

    CONSTRAINT fk_category_campaign
        FOREIGN KEY (campaign_id)
        REFERENCES campaign(id)
        ON DELETE CASCADE
);


-- =========================================================
-- 5. CAMPAIGN_TARGET
-- Конкретные клиенты, которым начисляется бонус
-- =========================================================

CREATE TABLE campaign_target (
    id                  BIGSERIAL PRIMARY KEY,

    campaign_id         BIGINT NOT NULL,

    category_id         BIGINT NOT NULL,

    client_id           BIGINT NOT NULL,

    recency             INTEGER,

    frequency           INTEGER,

    monetary_score      NUMERIC(12, 2),

    avg_amount          NUMERIC(12, 2),

    bonus_amount        INTEGER NOT NULL DEFAULT 0
                        CHECK (bonus_amount >= 0),

    bonus_realised      INTEGER
                        CHECK (bonus_realised >= 0),

    CONSTRAINT fk_target_campaign
        FOREIGN KEY (campaign_id)
        REFERENCES campaign(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_target_category
        FOREIGN KEY (category_id)
        REFERENCES campaign_category(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_target_client
        FOREIGN KEY (client_id)
        REFERENCES client(id)
        ON DELETE CASCADE
);


-- =========================================================
-- ИНДЕКСЫ
-- =========================================================

CREATE INDEX idx_purchase_client_id
    ON purchase(client_id);

CREATE INDEX idx_purchase_date
    ON purchase(purchase_date);

CREATE INDEX idx_purchase_client_date
    ON purchase(client_id, purchase_date);


CREATE INDEX idx_campaign_category_campaign_id
    ON campaign_category(campaign_id);


CREATE INDEX idx_campaign_target_campaign_id
    ON campaign_target(campaign_id);

CREATE INDEX idx_campaign_target_category_id
    ON campaign_target(category_id);

CREATE INDEX idx_campaign_target_client_id
    ON campaign_target(client_id);