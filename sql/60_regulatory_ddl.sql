-- M360MART regulatory corpus objects
-- Deployed by dataPrepRegulatory/load_regulatory.py
--
-- These live in SERVING so the corpus sits behind the same RBAC boundary as the
-- views the copilot reads. M360_APP_ROLE gets READ on the stage and SELECT on the
-- tables; it still cannot see BRONZE, SILVER or GOLD.

CREATE SCHEMA IF NOT EXISTS M360MART.SERVING;

-- The "folder" for regulatory source documents. DIRECTORY is enabled so the app
-- can build a scoped URL back to the exact PDF an answer was drawn from.
CREATE STAGE IF NOT EXISTS M360MART.SERVING.STG_REGULATORY
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Public-domain regulatory source documents (CFR, CMS, OIG, Texas). Every file is a US federal or state government work.';

-- One row per source document: the provenance record behind every citation.
CREATE OR REPLACE TABLE M360MART.SERVING.REGULATORY_SOURCES (
    doc_key          VARCHAR(60)  NOT NULL COMMENT 'Stable key from config/regulatory_sources.py',
    title            VARCHAR(500) NOT NULL COMMENT 'Full document title',
    citation_id      VARCHAR(200) NOT NULL COMMENT 'What an analyst would cite, e.g. 42 CFR 422.568',
    authority        VARCHAR(300) NOT NULL COMMENT 'Issuing body',
    source_url       VARCHAR(600) NOT NULL COMMENT 'Live URL - re-checked by validate/validate_app.py',
    fmt              VARCHAR(20)  NOT NULL COMMENT 'ecfr_xml | pdf | fr_json | html',
    themes           VARCHAR(300) NOT NULL COMMENT 'Pipe-delimited analyst themes this document serves',
    licence          VARCHAR(200) NOT NULL COMMENT 'Redistribution status',
    effective_date   VARCHAR(40)           COMMENT 'Effective or snapshot date',
    size_bytes       NUMBER(18,0) NOT NULL COMMENT 'Bytes actually downloaded',
    sha256           VARCHAR(64)  NOT NULL COMMENT 'SHA-256 of the retrieved bytes',
    chunk_count      NUMBER(10,0) NOT NULL COMMENT 'Chunks this document contributed',
    fetched_at       VARCHAR(40)  NOT NULL COMMENT 'When it was retrieved',
    notes            VARCHAR(2000)         COMMENT 'Traps and caveats found while sourcing',
    CONSTRAINT pk_regulatory_sources PRIMARY KEY (doc_key)
)
COMMENT = 'Provenance for the regulatory corpus. One row per source document.';

-- One row per retrievable chunk. Every column here exists so an answer can be
-- traced back to a specific, checkable place in a specific document.
CREATE OR REPLACE TABLE M360MART.SERVING.REGULATORY_CHUNKS (
    chunk_id         VARCHAR(40)   NOT NULL COMMENT 'Deterministic unique chunk id',
    doc_key          VARCHAR(60)   NOT NULL COMMENT 'Parent document',
    citation_id      VARCHAR(200)  NOT NULL COMMENT 'Citation an analyst recognises and can look up',
    section_label    VARCHAR(300)  NOT NULL COMMENT 'Precise locator: section, or page range for PDFs',
    heading          VARCHAR(600)           COMMENT 'Section heading as published',
    chunk_text       VARCHAR(16777216) NOT NULL COMMENT 'The searched text. Quoted source law, never paraphrased.',
    char_len         NUMBER(10,0)  NOT NULL COMMENT 'Length in characters',
    page_no          NUMBER(8,0)            COMMENT 'First page for PDF-sourced chunks; NULL for XML/HTML',
    themes           VARCHAR(300)  NOT NULL COMMENT 'Pipe-delimited analyst themes',
    authority        VARCHAR(300)  NOT NULL COMMENT 'Issuing body',
    source_url       VARCHAR(600)  NOT NULL COMMENT 'Live URL for verification',
    effective_date   VARCHAR(40)            COMMENT 'Effective or snapshot date',
    licence          VARCHAR(200)  NOT NULL COMMENT 'Redistribution status',
    ingested_at_utc  TIMESTAMP_NTZ NOT NULL COMMENT 'When the chunk was produced locally',
    CONSTRAINT pk_regulatory_chunks PRIMARY KEY (chunk_id),
    CONSTRAINT fk_regulatory_chunks_doc FOREIGN KEY (doc_key)
        REFERENCES M360MART.SERVING.REGULATORY_SOURCES (doc_key)
)
COMMENT = 'Citation-anchored chunks of public-domain regulatory text, backing SVC_REGULATORY.';
